from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import tempfile
import unittest
from unittest.mock import patch

from mower.config_source import InputUnavailable, resolve_runtime_inputs
from mower.full_failsafe import run_full_failsafe_cycle
from mower.husqvarna_actions import park_until_further_notice
from mower.input_failure_guard import run_input_failure_guard
from mower.runtime import CycleResult, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore


NOW = datetime(2026, 9, 9, 13, 32, tzinfo=timezone.utc)


def settings(*, park: bool = True) -> RuntimeSettings:
    return RuntimeSettings.from_mapping(
        {
            "CONTROL_MODE": "FULL_FAILSAFE",
            "ENABLE_LIVE_READS": "true",
            "ENABLE_PARK_COMMANDS": str(park).lower(),
            "ENABLE_START_COMMANDS": "true",
            "ENABLE_IRRIGATION_COMMANDS": "true",
            "FULL_MOWER_CONFIRMATION": "SSV53-TRAINING-MATCH-PARK-START",
            "FULL_FAILSAFE_CONFIRMATION": "SSV53-MOWER-HYDRAWISE-7-ZONES-150-MINUTES-ADAPTIVE-V1",
        }
    )


ENV = {
    "HUSQVARNA_CLIENT_ID": "client",
    "HUSQVARNA_CLIENT_SECRET": "secret",
    "MOWER_STATUS_MAX_AGE_SECONDS": "180",
    "HYDRAWISE_EXPECTED_ZONE_COUNT": "1",
    "HYDRAWISE_EXPECTED_RELAY_IDS": "123",
}


def mower_item(
    *,
    mower_id: str = "mower-1",
    activity: str = "MOWING",
    state: str = "IN_OPERATION",
    connected: bool = True,
    observed: datetime = NOW,
    error_code: int = 0,
    model: str = "Husqvarna Automower 580 EPOS",
    override_action: str = "FORCE_MOW",
    next_start_timestamp: int | None = 0,
    include_next_start: bool = True,
) -> dict:
    planner = {
        "override": {"action": override_action},
        "restrictedReason": "NONE",
        "externalReason": 253053,
    }
    if include_next_start:
        planner["nextStartTimestamp"] = next_start_timestamp
    return {
        "id": mower_id,
        "attributes": {
            "system": {"name": "Schaf", "model": model},
            "battery": {"batteryPercent": 57},
            "mower": {
                "activity": activity,
                "state": state,
                "mode": "MAIN_AREA",
                "errorCode": error_code,
                "inactiveReason": "NONE",
            },
            "planner": planner,
            "metadata": {
                "connected": connected,
                "statusTimestamp": int(observed.timestamp() * 1000),
            },
            "workAreas": [],
        },
    }


def cause() -> InputUnavailable:
    return InputUnavailable("current und previous sind älter als das Maximalalter")


def guard(
    store: InMemoryStateStore,
    *,
    item: dict | None = None,
    park_sender=lambda *_: {"accepted": True},
    env: dict[str, str] | None = None,
    now: datetime = NOW,
) -> CycleResult:
    return run_input_failure_guard(
        now_utc=now,
        settings=settings(),
        environment=env or ENV,
        past_due=False,
        source="test",
        cause=cause(),
        state_store_factory=lambda _env: store,
        park_sender=park_sender,
        mower_fetcher=lambda *_: [item or mower_item(observed=now)],
        clock=lambda: now,
    )


class _Download:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def readall(self) -> bytes:
        return self.value


class _Blob:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.etag = '"etag"'

    def get_blob_properties(self):
        return self

    def download_blob(self) -> _Download:
        return _Download(self.value)


class _Container:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def get_blob_client(self, name: str) -> _Blob:
        if name not in self.blobs:
            raise ValueError(f"missing {name}")
        return _Blob(self.blobs[name])


class _Service:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.container = _Container(blobs)

    def get_container_client(self, _name: str) -> _Container:
        return self.container


class InputFailureGuardTests(unittest.TestCase):
    def test_actual_resolver_raises_typed_error_when_both_manifests_are_stale(self) -> None:
        config = b"{}\n"
        matches = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        blobs: dict[str, bytes] = {}
        for channel in ("current", "previous"):
            version = f"{channel}-stale"
            config_blob = f"versions/{version}/mower/config.json"
            matches_blob = f"versions/{version}/public/rasen.ics"
            manifest = {
                "schema_version": 1,
                "version": version,
                "published_at_utc": (NOW - timedelta(days=2)).isoformat(),
                "config_blob": config_blob,
                "matches_blob": matches_blob,
                "config_sha256": sha256(config).hexdigest(),
                "matches_sha256": sha256(matches).hexdigest(),
            }
            blobs[f"{channel}/manifest.json"] = json.dumps(manifest).encode()
            blobs[config_blob] = config
            blobs[matches_blob] = matches
        with tempfile.TemporaryDirectory() as cache:
            env = {
                "SSV53_DYNAMIC_CONFIG_ENABLED": "true",
                "SSV53_CONFIG_STORAGE_ACCOUNT_URL": "https://example.invalid",
                "SSV53_CONFIG_CONTAINER": "runtime-config",
                "SSV53_CONFIG_MANAGED_IDENTITY_CLIENT_ID": "identity",
                "SSV53_CONFIG_CACHE_DIR": cache,
                "SSV53_CONFIG_MAX_AGE_MINUTES": "1440",
            }
            with self.assertRaisesRegex(InputUnavailable, "fail-closed"):
                resolve_runtime_inputs(
                    env,
                    now_utc=NOW,
                    service_client_factory=lambda **_: _Service(blobs),
                    credential_factory=lambda **_: object(),
                )

    def test_full_failsafe_catches_only_typed_source_failure(self) -> None:
        calls = []

        def fallback(**kwargs):
            calls.append(kwargs)
            return CycleResult(
                schema_version=2, executed_at_utc=NOW.isoformat(), source="test",
                control_mode="FULL_FAILSAFE", past_due=False,
                decision_code="GUARDED", command_sent=False, message="guarded",
            )

        output = run_full_failsafe_cycle(
            now_utc=NOW, settings=settings(), environment=ENV, past_due=False,
            source="test", read_only_runner=lambda **_: (_ for _ in ()).throw(cause()),
            input_failure_runner=fallback,
        )
        self.assertEqual(output.decision_code, "GUARDED")
        self.assertIsInstance(calls[0]["cause"], InputUnavailable)
        with self.assertRaisesRegex(RuntimeError, "programming bug"):
            run_full_failsafe_cycle(
                now_utc=NOW, settings=settings(), environment=ENV, past_due=False,
                source="test",
                read_only_runner=lambda **_: (_ for _ in ()).throw(RuntimeError("programming bug")),
                input_failure_runner=fallback,
            )

    def test_fresh_single_mower_is_reserved_before_one_protective_park(self) -> None:
        store = InMemoryStateStore(
            AutomationState(last_success_utc=(NOW - timedelta(hours=1)).isoformat())
        )
        observed = []

        def send(*_):
            observed.append(store.load())
            return {"accepted": True}

        output = guard(store, park_sender=send)
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_PARK_COMMAND_SENT")
        self.assertTrue(output.command_sent)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].automation_park_source, "input_unavailable")
        self.assertFalse(observed[0].automation_restart_allowed)
        self.assertEqual(output.details["occupancy"]["status"], "UNKNOWN")

    def test_retry_restart_and_lost_response_never_blindly_repeat(self) -> None:
        store = InMemoryStateStore(
            AutomationState(last_success_utc=(NOW - timedelta(hours=1)).isoformat())
        )
        calls = []
        first = guard(
            store,
            park_sender=lambda *_: calls.append("park") or (_ for _ in ()).throw(TimeoutError()),
        )
        self.assertEqual(first.decision_code, "INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN")
        self.assertTrue(first.command_sent)
        restarted_state = replace(
            store.load(),
            last_success_utc=(NOW + timedelta(minutes=10)).isoformat(),
        )
        second = guard(
            InMemoryStateStore(restarted_state),
            park_sender=lambda *_: calls.append("retry") or {},
            now=NOW + timedelta(minutes=20),
            item=mower_item(observed=NOW + timedelta(minutes=20)),
        )
        self.assertEqual(second.decision_code, "INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN")
        self.assertEqual(calls, ["park"])
        self.assertTrue(second.details["escalation"]["required"])

    def test_slow_source_attempt_does_not_age_a_fresh_guard_reservation(self) -> None:
        live_now = NOW + timedelta(minutes=5)
        store = InMemoryStateStore()
        calls = []
        output = run_input_failure_guard(
            now_utc=NOW,
            settings=settings(), environment=ENV, past_due=False, source="test",
            cause=cause(), state_store_factory=lambda _env: store,
            park_sender=lambda *_: calls.append("park") or {"accepted": True},
            mower_fetcher=lambda *_: [mower_item(observed=live_now)],
            clock=lambda: live_now,
        )
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_PARK_COMMAND_SENT")
        self.assertEqual(calls, ["park"])

    def test_reentrant_writer_observes_reservation_and_does_not_send_twice(self) -> None:
        store = InMemoryStateStore(
            AutomationState(last_success_utc=(NOW - timedelta(hours=1)).isoformat())
        )
        nested = []

        def send(*_):
            nested.append(guard(store, park_sender=lambda *_: self.fail("second send")))
            return {"accepted": True}

        first = guard(store, park_sender=send)
        self.assertEqual(first.decision_code, "INPUT_UNAVAILABLE_PARK_COMMAND_SENT")
        self.assertEqual(nested[0].decision_code, "INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN")

    def test_expired_reservation_is_not_sent_late(self) -> None:
        store = InMemoryStateStore(
            AutomationState(last_success_utc=(NOW - timedelta(hours=1)).isoformat())
        )
        calls = []
        times = iter((NOW, NOW, NOW + timedelta(seconds=31)))
        output = run_input_failure_guard(
            now_utc=NOW, settings=settings(), environment=ENV, past_due=False,
            source="test", cause=cause(), state_store_factory=lambda _env: store,
            park_sender=lambda *_: calls.append("park") or {},
            mower_fetcher=lambda *_: [mower_item()],
            clock=lambda: next(times),
        )
        self.assertEqual(output.details["park_gate"]["code"], "RESERVATION_EXPIRED_BEFORE_SEND")
        self.assertEqual(calls, [])

    def test_multiple_mowers_without_explicit_id_escalates(self) -> None:
        store = InMemoryStateStore()
        calls = []
        output = run_input_failure_guard(
            now_utc=NOW, settings=settings(), environment=ENV, past_due=False,
            source="test", cause=cause(), state_store_factory=lambda _env: store,
            park_sender=lambda *_: calls.append("park") or {},
            mower_fetcher=lambda *_: [mower_item(mower_id="one"), mower_item(mower_id="two")],
            clock=lambda: NOW,
        )
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_ESCALATION_REQUIRED")
        self.assertEqual(output.details["escalation"]["reason"], "MOWER_TELEMETRY_UNAVAILABLE")
        self.assertEqual(calls, [])

    def test_pending_start_manual_request_and_irrigation_journal_remain_unchanged(self) -> None:
        initial = AutomationState(
            revision=7,
            operator_request_id="manual-stop",
            operator_request_action="PARK_MOWER",
            operator_request_status="PENDING",
            irrigation_phase="RUNNING",
            irrigation_plan_id="plan-1",
            irrigation_plan_json='[{"relay_id":123}]',
            irrigation_current_relay_id=123,
            irrigation_zone_started_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        store = InMemoryStateStore(initial)
        calls = []
        output = guard(
            store,
            item=mower_item(activity="STOPPED_IN_GARDEN"),
            park_sender=lambda *_: calls.append("park") or {},
        )
        self.assertEqual(
            output.decision_code,
            "INPUT_UNAVAILABLE_MANUAL_IRRIGATION_UNKNOWN_HOLD",
        )
        self.assertTrue(output.details["escalation"]["required"])
        self.assertEqual(calls, [])
        self.assertEqual(store.load().to_dict(), initial.to_dict())

    def test_pending_start_reservation_is_not_overwritten_while_mowing(self) -> None:
        initial = AutomationState(
            last_success_utc=(NOW - timedelta(hours=1)).isoformat(),
            last_command_fingerprint="start-reservation",
            last_command_utc=(NOW - timedelta(minutes=2)).isoformat(),
            mower_start_pending_since_utc=(NOW - timedelta(minutes=2)).isoformat(),
            mower_start_pending_deadline_utc=(NOW + timedelta(minutes=30)).isoformat(),
            irrigation_phase="READY",
            irrigation_plan_id="plan-1",
        )
        store = InMemoryStateStore(initial)
        calls = []
        output = guard(store, park_sender=lambda *_: calls.append("park") or {})
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_START_OUTCOME_UNKNOWN")
        self.assertEqual(calls, [])
        self.assertEqual(store.load().to_dict(), initial.to_dict())

    def test_station_requires_native_park_override_or_reserves_park(self) -> None:
        held_store = InMemoryStateStore()
        held_calls = []
        held = guard(
            held_store,
            item=mower_item(activity="CHARGING", override_action="FORCE_PARK"),
            park_sender=lambda *_: held_calls.append("park") or {},
        )
        self.assertEqual(held.decision_code, "INPUT_UNAVAILABLE_SAFE_HOLD")
        self.assertEqual(held_calls, [])

        unheld_store = InMemoryStateStore()
        unheld_calls = []
        unheld = guard(
            unheld_store,
            item=mower_item(activity="CHARGING", override_action="FORCE_MOW"),
            park_sender=lambda *_: unheld_calls.append("park") or {"accepted": True},
        )
        self.assertEqual(unheld.decision_code, "INPUT_UNAVAILABLE_PARK_COMMAND_SENT")
        self.assertEqual(unheld_calls, ["park"])

    def test_timed_or_ambiguous_station_park_is_never_reported_safe(self) -> None:
        for override, include_next, next_start in (
            ("PARK_UNTIL_NEXT_SCHEDULE", True, 0),
            ("PARK", True, 0),
            ("FORCE_PARK", False, None),
            ("FORCE_PARK", True, int((NOW + timedelta(hours=1)).timestamp() * 1000)),
        ):
            with self.subTest(override=override, include_next=include_next):
                store = InMemoryStateStore()
                calls = []
                output = guard(
                    store,
                    item=mower_item(
                        activity="PARKED_IN_CS",
                        override_action=override,
                        include_next_start=include_next,
                        next_start_timestamp=next_start,
                    ),
                    park_sender=lambda *_: calls.append("park") or {},
                )
                self.assertEqual(
                    output.decision_code,
                    "INPUT_UNAVAILABLE_PARK_HOLD_UNKNOWN",
                )
                self.assertTrue(output.details["escalation"]["required"])
                self.assertFalse(output.details["mower"]["indefinite_native_hold"])
                self.assertEqual(calls, [])

    def test_going_home_without_park_override_is_not_reported_safe(self) -> None:
        store = InMemoryStateStore()
        calls = []
        output = guard(
            store,
            item=mower_item(activity="GOING_HOME", override_action="FORCE_MOW"),
            park_sender=lambda *_: calls.append("park") or {},
        )
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_RETURN_PENDING")
        self.assertTrue(output.details["escalation"]["required"])
        self.assertEqual(calls, [])

    def test_active_irrigation_never_reports_station_as_safe(self) -> None:
        store = InMemoryStateStore(
            AutomationState(irrigation_phase="READY", irrigation_plan_id="plan-1")
        )
        output = guard(
            store,
            item=mower_item(activity="PARKED_IN_CS", override_action="FORCE_PARK"),
        )
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_IRRIGATION_UNKNOWN_HOLD")
        self.assertEqual(output.details["irrigation"]["status"], "UNKNOWN")
        self.assertTrue(output.details["escalation"]["required"])

    def test_pending_operator_starts_are_persistently_rejected(self) -> None:
        for action in ("START_MOWING", "START_IRRIGATION", "START_IRRIGATION_ZONE"):
            with self.subTest(action=action):
                initial = AutomationState(
                    operator_request_id="start-1",
                    operator_request_action=action,
                    operator_request_status="PENDING",
                )
                store = InMemoryStateStore(initial)
                calls = []
                output = guard(store, park_sender=lambda *_: calls.append("park") or {})
                saved = store.load()
                self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_OPERATOR_START_REJECTED")
                self.assertEqual(saved.operator_request_status, "REJECTED")
                self.assertIn("Laufzeitdaten nicht verfügbar", saved.operator_request_result)
                self.assertEqual(calls, [])
                self.assertNotIn(str(cause()), json.dumps(output.to_dict(), ensure_ascii=False))

    def test_maintenance_gate_keeps_input_failure_visible_and_escalates(self) -> None:
        store = InMemoryStateStore(AutomationState(maintenance_mode=True))
        calls = []
        output = guard(store, park_sender=lambda *_: calls.append("park") or {})
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_PARK_GATE_BLOCKED")
        self.assertEqual(output.details["park_gate"]["code"], "MAINTENANCE_MODE")
        self.assertEqual(output.details["runtime_config"]["status"], "UNAVAILABLE")
        self.assertTrue(output.details["escalation"]["required"])
        self.assertEqual(calls, [])

    def test_post_cas_writer_fences_the_vendor_send(self) -> None:
        class RacingStore(InMemoryStateStore):
            armed = False

            def save(self, state, *, expected_revision):
                super().save(state, expected_revision=expected_revision)
                self.armed = True

            def load(self):
                current = super().load()
                if self.armed:
                    self.armed = False
                    changed = replace(
                        current,
                        revision=current.revision + 1,
                        maintenance_mode=True,
                    )
                    super().save(changed, expected_revision=current.revision)
                    return super().load()
                return current

        store = RacingStore()
        calls = []
        output = guard(store, park_sender=lambda *_: calls.append("park") or {})
        self.assertEqual(output.details["park_gate"]["code"], "POST_CAS_OWNERSHIP_LOST")
        self.assertEqual(calls, [])

    def test_slow_authentication_fence_blocks_the_canonical_http_post(self) -> None:
        store = InMemoryStateStore()
        current = [NOW]

        def authenticate(*_args, **_kwargs):
            current[0] = NOW + timedelta(seconds=31)
            return "token"

        with (
            patch(
                "mower.husqvarna_actions.get_access_token",
                side_effect=authenticate,
            ),
            patch("mower.husqvarna_actions.urlopen") as post,
        ):
            output = run_input_failure_guard(
                now_utc=NOW, settings=settings(), environment=ENV,
                past_due=False, source="test", cause=cause(),
                state_store_factory=lambda _env: store,
                park_sender=park_until_further_notice,
                mower_fetcher=lambda *_: [mower_item()],
                clock=lambda: current[0],
            )
        self.assertEqual(
            output.details["park_gate"]["code"],
            "RESERVATION_EXPIRED_DURING_AUTH",
        )
        self.assertFalse(output.command_sent)
        post.assert_not_called()

    def test_concurrent_maintenance_during_auth_blocks_canonical_post(self) -> None:
        store = InMemoryStateStore()

        def authenticate(*_args, **_kwargs):
            current = store.load()
            store.save(
                replace(
                    current,
                    revision=current.revision + 1,
                    maintenance_mode=True,
                ),
                expected_revision=current.revision,
            )
            return "token"

        with (
            patch(
                "mower.husqvarna_actions.get_access_token",
                side_effect=authenticate,
            ),
            patch("mower.husqvarna_actions.urlopen") as post,
        ):
            output = run_input_failure_guard(
                now_utc=NOW, settings=settings(), environment=ENV,
                past_due=False, source="test", cause=cause(),
                state_store_factory=lambda _env: store,
                park_sender=park_until_further_notice,
                mower_fetcher=lambda *_: [mower_item()],
                clock=lambda: NOW,
            )
        self.assertEqual(
            output.details["park_gate"]["code"],
            "PRE_POST_OWNERSHIP_LOST",
        )
        self.assertFalse(output.command_sent)
        post.assert_not_called()

    def test_existing_protective_park_ownership_is_not_overwritten(self) -> None:
        initial = AutomationState(
            last_success_utc=(NOW - timedelta(hours=1)).isoformat(),
            parked_by_automation=True,
            automation_park_source="irrigation",
            automation_restart_allowed=False,
            park_command_sent_utc=(NOW - timedelta(minutes=20)).isoformat(),
            irrigation_phase="READY",
            irrigation_plan_id="plan-1",
        )
        store = InMemoryStateStore(initial)
        calls = []
        output = guard(store, park_sender=lambda *_: calls.append("park") or {})
        self.assertEqual(output.decision_code, "INPUT_UNAVAILABLE_ACTIVE_RESERVATION_HOLD")
        self.assertEqual(output.details["park_gate"]["code"], "ACTIVE_PROTECTIVE_PARK")
        self.assertEqual(calls, [])
        self.assertEqual(store.load().to_dict(), initial.to_dict())

    def test_source_return_bypasses_failure_guard(self) -> None:
        invoked = []
        start_calls = []
        safe_cycle = CycleResult(
            schema_version=2, executed_at_utc=NOW.isoformat(), source="test",
            control_mode="FULL_FAILSAFE", past_due=False,
            decision_code="ALREADY_SAFE", command_sent=False, message="fresh",
            details={
                "mower": {
                    "mower_id": "mower-1", "activity": "PARKED_IN_CS",
                    "state": "IN_OPERATION", "override_action": "FORCE_PARK",
                    "error_code": 0, "battery_percent": 100,
                    "connected": True, "status_timestamp_ms": int(NOW.timestamp() * 1000),
                },
                "current_plan": {}, "decision": {},
                "hydrawise": {"safety": {}},
            },
        )
        store = InMemoryStateStore(AutomationState(
            operator_request_id="start-before-outage",
            operator_request_action="START_MOWING",
            operator_request_status="PENDING",
        ))
        outage = guard(store)
        self.assertEqual(
            outage.decision_code,
            "INPUT_UNAVAILABLE_OPERATOR_START_REJECTED",
        )
        output = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=1),
            settings=settings(), environment=ENV, past_due=False,
            source="test", read_only_runner=lambda **_: safe_cycle,
            state_store_factory=lambda _env: store,
            input_failure_runner=lambda **_: invoked.append(True),
            start_sender=lambda *_: start_calls.append("start") or {},
        )
        self.assertNotEqual(output.decision_code, "INPUT_UNAVAILABLE_OPERATOR_START_REJECTED")
        self.assertEqual(invoked, [])
        self.assertEqual(start_calls, [])
        self.assertEqual(store.load().operator_request_status, "REJECTED")


if __name__ == "__main__":
    unittest.main()
