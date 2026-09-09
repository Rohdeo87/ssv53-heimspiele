from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import time

from mower.dashboard_observations import (
    AzureTableDashboardObservationStore,
    DashboardObservationEntry,
    DashboardObservationSnapshot,
    InMemoryDashboardObservationStore,
    read_dashboard_observation,
    publish_dashboard_observation,
    dashboard_observation_store_from_environment,
)
from mower.status_cache import controller_cache_key
from mower.status_cache_store import StatusCacheConflict


NOW = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
RELAYS = tuple(range(1, 8))


def payload(at=NOW):
    return {
        "time": int(at.timestamp()), "nextpoll": 900,
        "api_key": "secret", "relays": [
            {"relay_id": relay, "relay": relay, "name": f"Zone {relay}", "time": 0, "run": 600}
            for relay in RELAYS
        ],
    }


def test_publish_and_load_preserves_source_time_and_filters_credentials():
    store = InMemoryDashboardObservationStore()
    assert publish_dashboard_observation(store, "00123", payload(), expected_relay_ids=RELAYS, clock=lambda: NOW) == "PUBLISHED"
    result = read_dashboard_observation(store, "123", expected_relay_ids=RELAYS, now_utc=NOW + timedelta(seconds=30))
    assert result.known and result.status["time"] == int(NOW.timestamp())
    assert "api_key" not in result.status
    assert result.source_observed_at_utc == NOW.isoformat()
    assert result.fetched_at_utc == NOW.isoformat()


def test_equal_or_older_source_cannot_overwrite():
    store = InMemoryDashboardObservationStore()
    publish_dashboard_observation(store, "123", payload(NOW), expected_relay_ids=RELAYS, clock=lambda: NOW)
    assert publish_dashboard_observation(store, "123", payload(NOW), expected_relay_ids=RELAYS, clock=lambda: NOW + timedelta(seconds=5)) == "STALE_OR_EQUAL"
    assert publish_dashboard_observation(store, "123", payload(NOW - timedelta(seconds=1)), expected_relay_ids=RELAYS, clock=lambda: NOW + timedelta(seconds=5)) == "STALE_OR_EQUAL"


def test_invalid_relay_set_and_stale_snapshot_are_unknown():
    store = InMemoryDashboardObservationStore()
    assert publish_dashboard_observation(store, "123", payload(), expected_relay_ids=(1, 2), clock=lambda: NOW) == "INVALID_CONFIGURATION"
    publish_dashboard_observation(store, "123", payload(), expected_relay_ids=RELAYS, clock=lambda: NOW)
    result = read_dashboard_observation(store, "123", expected_relay_ids=RELAYS, now_utc=NOW + timedelta(seconds=181))
    assert not result.known and result.quality == "SOURCE_STALE"


def test_reader_is_load_only_and_store_conflict_is_not_fallback():
    class ExplodingStore(InMemoryDashboardObservationStore):
        def load(self, key):
            raise AssertionError("vendor/network fallback must not occur")
    result = read_dashboard_observation(ExplodingStore(), "123", expected_relay_ids=RELAYS, now_utc=NOW)
    assert not result.known and result.quality == "CACHE_UNAVAILABLE"

    store = InMemoryDashboardObservationStore()
    original = store.compare_exchange
    def conflict(*args, **kwargs):
        raise StatusCacheConflict("race")
    store.compare_exchange = conflict
    try:
        publish_dashboard_observation(store, "123", payload(), expected_relay_ids=RELAYS, clock=lambda: NOW)
    except StatusCacheConflict:
        pass
    else:
        raise AssertionError("CAS conflict must be visible to the publisher")
    store.compare_exchange = original


def test_malformed_schema_payload_and_source_mismatch_are_unknown():
    store = InMemoryDashboardObservationStore()
    key = controller_cache_key("123")
    store.compare_exchange(key, None, DashboardObservationEntry(
        payload_json="{}", source_observed_at_utc=NOW.isoformat(),
        fetched_at_utc=NOW.isoformat(), schema_version="v2"))
    assert read_dashboard_observation(store, "123", expected_relay_ids=RELAYS, now_utc=NOW).quality == "INVALID_SCHEMA"
    mismatch = payload(); mismatch["time"] = 1
    import json
    store.compare_exchange(key, store.load(key).token, DashboardObservationEntry(
        payload_json=json.dumps(mismatch), source_observed_at_utc=NOW.isoformat(),
        fetched_at_utc=NOW.isoformat()))
    assert read_dashboard_observation(store, "123", expected_relay_ids=RELAYS, now_utc=NOW).quality == "SOURCE_MISMATCH"


def test_invalid_clock_and_relay_values_never_raise_or_publish():
    store = InMemoryDashboardObservationStore()
    assert publish_dashboard_observation(store, "123", payload(), expected_relay_ids=(1, 2, 3, 4, 5, 6, 1), clock=lambda: NOW) == "INVALID_CONFIGURATION"
    assert publish_dashboard_observation(store, "123", payload(), expected_relay_ids=(1, 2, 3, 4, 5, 6, 7.0), clock=lambda: NOW) == "INVALID_CONFIGURATION"
    assert publish_dashboard_observation(store, "123", payload(), expected_relay_ids=RELAYS, clock=lambda: NOW.replace(tzinfo=None)) == "INVALID_OBSERVATION"


def test_azure_adapter_uses_existing_table_and_if_match():
    class Client:
        def __init__(self): self.calls = []
        def create_entity(self, **kwargs): self.calls.append(("create", kwargs))
        def update_entity(self, **kwargs): self.calls.append(("update", kwargs))
    client = Client()
    adapter = AzureTableDashboardObservationStore(client)
    entry = DashboardObservationEntry("{}", NOW.isoformat(), NOW.isoformat())
    adapter.compare_exchange("123", None, entry)
    adapter.compare_exchange("123", "etag-1", entry)
    assert client.calls[0][1]["entity"]["PartitionKey"] == "ssv53-hydrawise-dashboard-observation-v1"
    assert client.calls[1][0] == "update"
    assert client.calls[1][1]["etag"] == "etag-1"
    assert dashboard_observation_store_from_environment(
        {"HYDRAWISE_DASHBOARD_OBSERVATION_MODE": "OFF"},
        credential_factory=lambda **_: (_ for _ in ()).throw(AssertionError()),
        table_client_factory=lambda **_: (_ for _ in ()).throw(AssertionError()),
    ) is None


def test_top_level_nested_values_and_oversize_are_rejected():
    store = InMemoryDashboardObservationStore()
    nested = payload(); nested["nextpoll"] = {"secret": "do-not-store"}
    assert publish_dashboard_observation(store, "123", nested, expected_relay_ids=RELAYS, clock=lambda: NOW) == "INVALID_OBSERVATION"
    huge = payload(); huge["relays"][0]["name"] = "x" * 161
    assert publish_dashboard_observation(store, "123", huge, expected_relay_ids=RELAYS, clock=lambda: NOW) == "INVALID_OBSERVATION"
    malformed = DashboardObservationEntry("x" * (60 * 1024), NOW.isoformat(), NOW.isoformat())
    key = controller_cache_key("124")
    store.compare_exchange(key, None, malformed)
    assert read_dashboard_observation(store, "124", expected_relay_ids=RELAYS, now_utc=NOW).quality == "PAYLOAD_TOO_LARGE"


def test_future_and_malformed_metadata_are_unknown():
    store = InMemoryDashboardObservationStore()
    future = NOW + timedelta(seconds=1)
    store.compare_exchange(controller_cache_key("125"), None, DashboardObservationEntry(
        json.dumps(payload(future)), future.isoformat(), NOW.isoformat()))
    assert read_dashboard_observation(store, "125", expected_relay_ids=RELAYS, now_utc=NOW).quality == "INVALID_TIMESTAMP"
    store.compare_exchange(controller_cache_key("126"), None, DashboardObservationEntry(
        json.dumps(payload()), NOW.isoformat(), (NOW - timedelta(seconds=1)).isoformat()))
    assert read_dashboard_observation(store, "126", expected_relay_ids=RELAYS, now_utc=NOW).quality == "INVALID_TIMESTAMP"


def test_concurrent_publishers_have_one_cas_winner_and_older_loser():
    store = InMemoryDashboardObservationStore()
    barrier = __import__("threading").Barrier(2)
    class CoordinatedStore(InMemoryDashboardObservationStore):
        def load(self, key):
            value = super().load(key); barrier.wait(); return value
        def compare_exchange(self, key, expected_token, entry):
            if entry.source_observed_at_utc == NOW.isoformat():
                time.sleep(0.02)
            return super().compare_exchange(key, expected_token, entry)
    store = CoordinatedStore()
    newer = payload(NOW + timedelta(seconds=1))
    older = payload(NOW)
    def run(item):
        try:
            return publish_dashboard_observation(store, "127", item, expected_relay_ids=RELAYS, clock=lambda: NOW + timedelta(seconds=2))
        except StatusCacheConflict:
            return "CAS_CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (newer, older)))
    assert results.count("PUBLISHED") == 1
    assert set(results) <= {"PUBLISHED", "CAS_CONFLICT", "STALE_OR_EQUAL"}
