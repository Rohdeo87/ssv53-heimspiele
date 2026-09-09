from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import threading
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from mower.hydrawise import HydrawiseError, evaluate_continuous_clear_confirmation, fetch_status
from mower.status_cache import CoordinatedHydrawiseStatusCache, controller_cache_key, read_status_cached
from mower.status_cache_store import AzureTableStatusCacheStore, InMemoryStatusCacheStore, StatusCacheConflict


NOW = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
CONFIG = {"enabled": True, "include_all_zones": True, "expected_relay_ids": [1, 2], "before_minutes": 30}


def payload(at=NOW, nextpoll=60):
    return {"time": int(at.timestamp()), "nextpoll": nextpoll,
            "relays": [{"relay_id": relay, "relay": relay, "name": f"Zone {relay}", "time": 0, "run": 600}
                       for relay in (1, 2)]}


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def reader(store, clock, fetcher):
    return CoordinatedHydrawiseStatusCache(store, clock=clock, fetcher=fetcher)


def read(cache, key="key-one", controller="123"):
    return cache.read(key, controller, hydrawise_config=CONFIG)


def test_cached_read_keeps_source_receipt_and_budget_unchanged():
    clock, store, calls = Clock(), InMemoryStatusCacheStore(), []

    def fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        return payload(clock())

    cache = reader(store, clock, fetcher)
    first = read(cache)
    clock.advance(30)
    cached = read(cache, key="another-account-key", controller="00123")
    assert len(calls) == 1
    assert first.new_observation and not cached.new_observation
    assert cached.status == first.status
    assert cached.status["time"] == int(NOW.timestamp())
    assert cached.fetched_at_utc == first.fetched_at_utc == NOW.isoformat()
    assert cached.next_poll_at_utc == (NOW + timedelta(seconds=60)).isoformat()
    assert cached.confirmation_observed_until_utc == NOW.isoformat()
    cached.status["relays"][0]["run"] = 999
    assert read(cache).status["relays"][0]["run"] == 600
    clock.advance(30)
    assert read(cache).new_observation
    assert len(calls) == 2
    assert controller_cache_key("00123") == controller_cache_key("123")


def test_nextpoll_begins_at_real_receipt_and_long_budget_does_not_make_stale_data_fresh():
    clock, store = Clock(), InMemoryStatusCacheStore()

    def fetcher(*_, **__):
        clock.advance(5)
        return payload(NOW, nextpoll=900)

    cache = reader(store, clock, fetcher)
    first = read(cache)
    assert first.fetched_at_utc == (NOW + timedelta(seconds=5)).isoformat()
    assert first.next_poll_at_utc == (NOW + timedelta(seconds=905)).isoformat()
    clock.advance(180)
    stale = read(reader(store, clock, lambda *_a, **_k: pytest.fail("nextpoll not reached")))
    assert stale.status is None and stale.last_known_status == first.status
    assert stale.quality == "SOURCE_STALE_OR_INVALID"


@pytest.mark.parametrize("retry", ["1200", format_datetime(NOW + timedelta(minutes=30), usegmt=True)])
def test_failed_request_obeys_retry_after_without_refreshing_last_good_source(retry):
    clock, store = Clock(), InMemoryStatusCacheStore()
    first = read(reader(store, clock, lambda *_a, **_k: payload()))
    clock.advance(60)

    def error(*_, **__):
        raise HydrawiseError("secret URL must not be stored", http_status=429, retry_after=retry)

    failed = read(reader(store, clock, error))
    assert failed.status is None and failed.last_known_status == first.status
    assert failed.fetched_at_utc == first.fetched_at_utc
    assert failed.confirmation_observed_until_utc is None
    expected = NOW + timedelta(seconds=1260) if retry == "1200" else NOW + timedelta(minutes=30)
    assert failed.next_poll_at_utc == expected.isoformat()
    clock.advance(10)
    held = read(reader(store, clock, lambda *_a, **_k: pytest.fail("Retry-After violated")))
    assert held.status is None
    serialized = json.dumps(asdict(store.load(controller_cache_key("123")).entry))
    assert "secret" not in serialized and "key-one" not in serialized


def test_transport_retry_after_metadata_is_preserved_without_any_network():
    failure = HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "900"}, None)
    with patch("mower.hydrawise.urlopen", side_effect=failure):
        with pytest.raises(HydrawiseError) as caught:
            fetch_status("not-a-real-key", "123")
    assert caught.value.http_status == 429
    assert caught.value.retry_after == "900"


@pytest.mark.parametrize("change", [
    {"nextpoll": None}, {"nextpoll": True}, {"nextpoll": 1.5}, {"nextpoll": -1},
    {"relays": None}, {"time": "invalid"}, {"relays": []},
])
def test_invalid_response_has_no_new_usable_status_and_no_fast_retry(change):
    clock, store = Clock(), InMemoryStatusCacheStore()
    bad = {**payload(), **change}
    value = read(reader(store, clock, lambda *_a, **_k: bad))
    assert value.status is None and not value.new_observation
    assert value.fetched_at_utc is None
    assert value.next_poll_at_utc == (NOW + timedelta(minutes=5)).isoformat()
    assert read(reader(store, clock, lambda *_a, **_k: pytest.fail("error budget ignored"))).status is None


def test_malformed_status_still_preserves_valid_manufacturer_nextpoll():
    result = read(reader(InMemoryStatusCacheStore(), Clock(),
                         lambda *_a, **_k: {**payload(nextpoll=1800), "relays": []}))
    assert result.status is None and result.quality == "STATUS_INVALID"
    assert result.next_poll_at_utc == (NOW + timedelta(minutes=30)).isoformat()


@pytest.mark.parametrize("source", [NOW, NOW - timedelta(seconds=1)])
def test_new_http_reply_cannot_make_same_or_older_source_a_new_observation(source):
    clock, store = Clock(), InMemoryStatusCacheStore()
    first = read(reader(store, clock, lambda *_a, **_k: payload()))
    clock.advance(60)
    duplicate = read(reader(store, clock, lambda *_a, **_k: payload(source, nextpoll=900)))
    assert duplicate.status is None and not duplicate.new_observation
    assert duplicate.quality == "SOURCE_NOT_ADVANCED"
    assert duplicate.fetched_at_utc == first.fetched_at_utc
    assert duplicate.source_observed_at_utc == first.source_observed_at_utc
    assert duplicate.confirmation_observed_until_utc is None
    assert duplicate.next_poll_at_utc == (clock() + timedelta(seconds=900)).isoformat()


@pytest.mark.parametrize("source", [NOW - timedelta(seconds=181), NOW + timedelta(seconds=61)])
def test_stale_or_future_source_is_never_published_as_success(source):
    result = read(reader(InMemoryStatusCacheStore(), Clock(), lambda *_a, **_k: payload(source)))
    assert result.status is None and result.quality == "SOURCE_STALE"
    assert result.fetched_at_utc is None and result.source_observed_at_utc is None


def test_source_timestamp_remains_valid_after_2038():
    clock = Clock()
    clock.now = datetime(2039, 9, 9, 8, tzinfo=timezone.utc)
    cache = reader(InMemoryStatusCacheStore(), clock, lambda *_a, **_k: payload(clock()))
    first = read(cache)
    assert first.new_observation
    clock.advance(30)
    assert read(cache).status == first.status


def test_two_independent_clients_send_only_one_fetch_and_reuse_completed_result():
    clock, store = Clock(), InMemoryStatusCacheStore()
    started, finish = threading.Event(), threading.Event()
    calls = []

    def fetcher(*args, **kwargs):
        calls.append(args)
        started.set()
        assert finish.wait(3)
        return payload()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read, reader(store, clock, fetcher))
        assert started.wait(3)
        concurrent = read(reader(store, clock, lambda *_a, **_k: pytest.fail("second vendor request")), key="second-key")
        assert concurrent.status is None and concurrent.quality == "FETCH_IN_PROGRESS"
        finish.set()
        completed = first.result(timeout=3)
    cached = read(reader(store, clock, lambda *_a, **_k: pytest.fail("budget violated")))
    assert len(calls) == 1
    assert cached.status == completed.status


def test_crashed_fetch_recovers_after_shared_lease_and_backoff_on_restart():
    class SimulatedCrash(BaseException):
        pass

    clock, store = Clock(), InMemoryStatusCacheStore()

    def crashed(*_, **__):
        raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        read(reader(store, clock, crashed))
    reserved = store.load(controller_cache_key("123")).entry
    assert reserved.next_poll_at_utc == (NOW + timedelta(seconds=360)).isoformat()
    clock.advance(61)
    held = read(reader(store, clock, lambda *_a, **_k: pytest.fail("recovery budget not reached")))
    assert held.status is None and held.quality == "FETCH_RECOVERY_BACKOFF"
    clock.advance(298)
    assert read(reader(store, clock, lambda *_a, **_k: pytest.fail("early retry"))).status is None
    clock.advance(1)
    recovered = read(reader(store, clock, lambda *_a, **_k: payload(clock())))
    assert recovered.status is not None and recovered.new_observation
    assert recovered.consecutive_errors == 0 and not recovered.escalation_required


def test_repeated_crashes_escalate_but_keep_bounded_hourly_recovery():
    class SimulatedCrash(BaseException):
        pass

    clock, store = Clock(), InMemoryStatusCacheStore()

    def crashed(*_, **__):
        raise SimulatedCrash()

    for start, recovery_at in [(0, 360), (360, 1020), (1020, 4680)]:
        clock.now = NOW + timedelta(seconds=start)
        with pytest.raises(SimulatedCrash):
            read(reader(store, clock, crashed))
        entry = store.load(controller_cache_key("123")).entry
        assert entry.next_poll_at_utc == (NOW + timedelta(seconds=recovery_at)).isoformat()
    clock.advance(61)
    escalated = read(reader(store, clock, lambda *_a, **_k: pytest.fail("backoff violated")))
    assert escalated.status is None and escalated.escalation_required
    assert escalated.consecutive_errors == 3
    clock.now = NOW + timedelta(seconds=4680)
    recovered = read(reader(store, clock, lambda *_a, **_k: payload(clock())))
    assert recovered.new_observation and not recovered.escalation_required


def test_timed_out_old_get_cannot_publish_after_a_new_fenced_fetch():
    clock, store = Clock(), InMemoryStatusCacheStore()
    started, finish = threading.Event(), threading.Event()

    def late(*_, **__):
        started.set()
        assert finish.wait(3)
        return payload(clock(), nextpoll=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(read, reader(store, clock, late))
        assert started.wait(3)
        old_token = store.load(controller_cache_key("123")).entry.attempt_id
        clock.advance(360)
        new = read(reader(store, clock, lambda *_a, **_k: payload(clock(), nextpoll=1800)))
        assert new.new_observation
        current = store.load(controller_cache_key("123"))
        assert current.entry.attempt_id != old_token
        finish.set()
        expired = old.result(timeout=3)
    assert expired.status is None and expired.quality == "FETCH_RESERVATION_CHANGED"
    assert store.load(controller_cache_key("123")) == current
    assert current.entry.next_poll_at_utc == (clock() + timedelta(seconds=1800)).isoformat()


def test_concurrent_recovery_race_has_one_cas_winner_and_one_new_get():
    class SimulatedCrash(BaseException):
        pass

    class RacingStore(InMemoryStatusCacheStore):
        racing = False
        first_loads = set()
        barrier = threading.Barrier(2)
        counter_lock = threading.Lock()

        def load(self, key):
            snapshot = super().load(key)
            with self.counter_lock:
                identity = threading.get_ident()
                wait = self.racing and identity not in self.first_loads
                self.first_loads.add(identity)
            if wait:
                self.barrier.wait(timeout=3)
            return snapshot

    clock, store, calls = Clock(), RacingStore(), []

    def crashed(*_, **__):
        raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        read(reader(store, clock, crashed))
    clock.advance(360)
    store.racing = True

    def recovered(*_, **__):
        calls.append(1)
        return payload(clock())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read, reader(store, clock, recovered)) for _ in range(2)]
        values = [future.result(timeout=3) for future in futures]
    assert len(calls) == 1
    assert sum(value.new_observation for value in values) == 1


def test_response_exceeding_observation_lease_never_refreshes_source_time():
    clock, store = Clock(), InMemoryStatusCacheStore()

    def too_slow(*_, **__):
        clock.advance(61)
        return payload(clock())

    result = read(reader(store, clock, too_slow))
    assert result.quality == "FETCH_LEASE_EXPIRED" and result.status is None
    assert result.fetched_at_utc is None and result.source_observed_at_utc is None
    assert result.next_poll_at_utc == (NOW + timedelta(seconds=361)).isoformat()


def test_late_response_cannot_overwrite_reconciled_reservation_or_later_budget():
    clock, store = Clock(), InMemoryStatusCacheStore()
    key = controller_cache_key("123")

    def late(*_, **__):
        current = store.load(key)
        replacement = replace(current.entry, phase="ERROR", attempt_id=None, attempt_until_utc=None,
                              last_error="RECONCILED_UNKNOWN", next_poll_at_utc=(NOW + timedelta(hours=1)).isoformat())
        store.compare_exchange(key, current.token, replacement)
        return payload()

    result = read(reader(store, clock, late))
    assert result.status is None and result.quality == "FETCH_RESERVATION_CHANGED"
    assert store.load(key).entry.next_poll_at_utc == (NOW + timedelta(hours=1)).isoformat()
    assert store.load(key).entry.payload_json is None


def test_storage_failures_never_bypass_cache_or_publish_uncommitted_http_reply():
    class Unavailable(InMemoryStatusCacheStore):
        def load(self, key):
            raise RuntimeError("storage down")

    assert read(reader(Unavailable(), Clock(), lambda *_a, **_k: pytest.fail("fallback fetch"))).status is None

    class FailedCommit(InMemoryStatusCacheStore):
        def compare_exchange(self, key, token, entry):
            if entry.phase == "SUCCESS":
                raise RuntimeError("commit answer lost")
            super().compare_exchange(key, token, entry)

    store = FailedCommit()
    value = read(reader(store, Clock(), lambda *_a, **_k: payload()))
    assert value.status is None and value.quality == "CACHE_COMMIT_UNCONFIRMED"
    assert store.load(controller_cache_key("123")).entry.phase == "PENDING"


def test_off_mode_has_no_storage_or_vendor_effect_and_unknown_controller_is_rejected():
    with patch("mower.status_cache._azure_store", side_effect=AssertionError("storage called")):
        assert read_status_cached("key", "123", environment={}, hydrawise_config=CONFIG).quality == "CACHE_DISABLED"
    assert read(reader(InMemoryStatusCacheStore(), Clock(), lambda *_a, **_k: pytest.fail("ambiguous controller")), controller=None).status is None


def test_cached_elapsed_wall_time_does_not_complete_two_minute_data_confirmation():
    options = dict(available=True, fresh=True, clear_now=True, physical_reason="clear",
                   clear_since_utc=NOW.isoformat(), drying_since_utc=(NOW - timedelta(minutes=150)).isoformat(),
                   now_utc=NOW + timedelta(minutes=2), required_clear_minutes=150,
                   persistent_state_available=True, telemetry_confirmation_minutes=2)
    cached = evaluate_continuous_clear_confirmation(**options, confirmation_observed_until_utc=NOW.isoformat())
    assert not cached.allowed and not cached.telemetry_confirmed and cached.confirmed_for_seconds == 0
    assert cached.dry_until_utc == NOW.isoformat()
    fresh = evaluate_continuous_clear_confirmation(**options, confirmation_observed_until_utc=(NOW + timedelta(minutes=2)).isoformat())
    assert fresh.allowed and fresh.telemetry_confirmed


def test_azure_cache_uses_independent_partition_and_conditional_etags():
    class Entity(dict):
        metadata = {}

    class Table:
        entity = None
        version = 0
        calls = []

        def get_entity(self, **kwargs):
            self.calls.append(kwargs)
            if self.entity is None:
                raise ResourceNotFoundError("missing")
            item = Entity(deepcopy(self.entity))
            item.metadata = {"etag": str(self.version)}
            return item

        def create_entity(self, *, entity, **kwargs):
            self.entity = deepcopy(entity)
            self.version += 1

        def update_entity(self, *, entity, etag, **kwargs):
            if etag != str(self.version):
                error = HttpResponseError("conflict")
                error.status_code = 412
                raise error
            self.entity = deepcopy(entity)
            self.version += 1

    table = Table()
    first, second = AzureTableStatusCacheStore(table), AzureTableStatusCacheStore(table)
    key = controller_cache_key("123")
    empty = first.load(key)
    first.compare_exchange(key, empty.token, replace(empty.entry, phase="ERROR", last_error="test"))
    left, right = first.load(key), second.load(key)
    first.compare_exchange(key, left.token, replace(left.entry, last_error="updated"))
    with pytest.raises(StatusCacheConflict):
        second.compare_exchange(key, right.token, right.entry)
    assert table.entity["PartitionKey"] == "ssv53-hydrawise-status-cache-v1"
    assert all(call["partition_key"] != "ssv53-mower" for call in table.calls)
