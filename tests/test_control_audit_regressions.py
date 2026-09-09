from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import timedelta

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from mower.full_failsafe import run_full_failsafe_cycle
from mower.safety import occupancy_override_allowed
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, InMemoryStateStore, StateConflictError
from test_full_failsafe import ENV, NOW, RELAYS, result, settings


class ControlAuditRegressionTests(unittest.TestCase):
    def run_cycle(self, store, *, when=NOW, cycle=None, starts=None, start_sender=None, park_sender=None, stop_sender=None):
        observed = deepcopy(cycle or result())
        observed.details["mower"]["status_timestamp_ms"] = int(when.timestamp() * 1000)
        observed.details["hydrawise"]["safety"]["observed_at_utc"] = when.isoformat()
        return run_full_failsafe_cycle(
            now_utc=when, settings=settings(), environment=ENV,
            past_due=False, source="offline-audit-test",
            read_only_runner=lambda **_: observed,
            state_store_factory=lambda _: store,
            park_sender=park_sender or (lambda *_: {"accepted": True}),
            start_sender=start_sender or (lambda *args: (starts.append(args) if starts is not None else None) or {"accepted": True}),
            suspend_zone_sender=lambda *_: self.fail("Unexpected irrigation schedule mutation"),
            start_zone_sender=lambda *_: self.fail("Unexpected real watering intent"),
            stop_zone_sender=stop_sender or (lambda *_: self.fail("Unexpected immediate water stop")),
        )

    def refresh_state(self):
        return AutomationState(
            continuous_mowing_owned=True, continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=(NOW + timedelta(hours=3)).isoformat(),
            last_mower_activity="MOWING", hydrawise_clear_since_utc=(NOW - timedelta(hours=3)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )

    def test_refresh_must_reserve_before_sender_and_cas_conflict_sends_nothing(self):
        class ConflictingStore(InMemoryStateStore):
            def save(self, *_args, **_kwargs):
                raise StateConflictError("another scheduler changed state")

        starts = []
        output = self.run_cycle(ConflictingStore(self.refresh_state()),
                                cycle=result(activity="MOWING", window_end=NOW + timedelta(minutes=90)), starts=starts)
        self.assertEqual(output.decision_code, "MOWER_START_RESERVATION_FAILED")
        self.assertEqual(starts, [])

    def test_two_schedulers_cannot_send_two_refresh_starts(self):
        store = InMemoryStateStore(self.refresh_state())
        observed = result(activity="MOWING", window_end=NOW + timedelta(minutes=90))
        starts = []

        def first_sender(*args):
            starts.append(args)
            self.assertIsNotNone(store.load().mower_start_pending_since_utc)
            second = self.run_cycle(store, cycle=observed,
                                    start_sender=lambda *_: self.fail("Concurrent START"),
                                    park_sender=lambda *_: self.fail("No opposite command during in-flight request"))
            self.assertEqual(second.decision_code, "MOWER_START_OUTCOME_UNCONFIRMED")
            return {"accepted": True}

        output = self.run_cycle(store, cycle=observed, start_sender=first_sender)
        self.assertEqual(len(starts), 1)
        self.assertEqual(output.decision_code, "MOWER_START_OUTCOME_UNCONFIRMED")
        self.assertEqual(store.load().continuous_mowing_window_end_utc, (NOW + timedelta(hours=3)).isoformat())
        self.assertIsNotNone(store.load().mower_start_pending_since_utc)

    def test_lost_start_response_keeps_unknown_latch_across_restart_and_dock(self):
        for refresh in (False, True):
            with self.subTest(refresh=refresh):
                state = self.refresh_state() if refresh else AutomationState(
                    parked_by_automation=True, automation_park_source="training", automation_restart_allowed=True,
                    hydrawise_clear_since_utc=(NOW - timedelta(hours=3)).isoformat(),
                    last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
                )
                observed = result(activity="MOWING" if refresh else "PARKED_IN_CS", window_end=NOW + timedelta(minutes=90))
                store = InMemoryStateStore(state)
                calls = []

                def lost_reply(*args):
                    calls.append(args)
                    raise TimeoutError("device may already have accepted")

                output = self.run_cycle(store, cycle=observed, start_sender=lost_reply)
                self.assertEqual(output.decision_code, "MOWER_START_OUTCOME_UNCONFIRMED")
                self.assertEqual(store.load().continuous_mowing_window_end_utc, state.continuous_mowing_window_end_utc)
                store = InMemoryStateStore(AutomationState.from_mapping(store.load().to_dict()))
                for minute in (2, 3, 20):
                    held = self.run_cycle(store, when=NOW + timedelta(minutes=minute), cycle=result(),
                                          start_sender=lambda *_: self.fail("Unknown START may not expire/retry"))
                    self.assertEqual(held.decision_code, "MOWER_START_OUTCOME_UNCONFIRMED")
                self.assertEqual(len(calls), 1)
                self.assertIsNotNone(store.load().mower_start_pending_since_utc)

    def test_unconfirmed_start_allows_protective_park_and_explicit_immediate_water_stop(self):
        pending = AutomationState.from_mapping({
            **self.refresh_state().to_dict(),
            "mower_start_pending_since_utc": (NOW - timedelta(minutes=2)).isoformat(),
            "mower_start_pending_deadline_utc": (NOW + timedelta(minutes=30)).isoformat(),
        })
        parks = []
        output = self.run_cycle(InMemoryStateStore(pending), cycle=result(activity="MOWING"),
                                park_sender=lambda *args: parks.append(args) or {"accepted": True})
        self.assertEqual(output.decision_code, "MOWER_START_OUTCOME_UNCONFIRMED")
        self.assertEqual(len(parks), 1)
        stop_pending = AutomationState.from_mapping({
            **pending.to_dict(), "operator_request_id": "stop-water",
            "operator_request_action": "STOP_IRRIGATION_NOW", "operator_request_status": "PENDING",
            "operator_request_expires_utc": (NOW + timedelta(minutes=5)).isoformat(),
        })
        stops = []
        output = self.run_cycle(InMemoryStateStore(stop_pending), cycle=result(active_ids=[RELAYS[0]], clear=False),
                                stop_sender=lambda *args: stops.append(args) or {"message_type": "info"})
        self.assertEqual(output.decision_code, "MOWER_START_OUTCOME_UNCONFIRMED")
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0][1], RELAYS[0])

    def test_failed_minute_poll_and_restart_preserve_physical_hold_for_both_origins(self):
        for phase in (None, "COMPLETE_HOLD"):
            with self.subTest(phase=phase):
                physical_end = NOW - timedelta(minutes=149)
                store = InMemoryStateStore(AutomationState(
                    parked_by_automation=True, automation_park_source="training",
                    automation_restart_allowed=True,
                    park_confirmed_utc=(NOW - timedelta(minutes=150)).isoformat(),
                    park_confirmed_observations=2,
                    last_mower_activity="PARKED_IN_CS",
                    hydrawise_clear_origin="IRRIGATION_END",
                    hydrawise_clear_since_utc=physical_end.isoformat(),
                    last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
                    last_hydrawise_active_count=0, irrigation_phase=phase,
                ))
                missing = deepcopy(result())
                missing.details["hydrawise"]["safety"].update(available=False, fresh=False, clear_now=False)
                missing.details["hydrawise"]["zones"] = []
                starts = []
                self.run_cycle(store, cycle=missing, starts=starts)
                self.assertEqual(starts, [])
                # A new store object models process restart with durable state.
                store = InMemoryStateStore(AutomationState.from_mapping(store.load().to_dict()))
                returned = self.run_cycle(store, when=NOW + timedelta(minutes=1), starts=starts)
                self.assertEqual(returned.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
                self.assertEqual(returned.details["hydrawise_release_gate"]["dry_until_utc"], (NOW + timedelta(minutes=1)).isoformat())
                self.assertEqual(returned.details["hydrawise_release_gate"]["release_at_utc"], (NOW + timedelta(minutes=3)).isoformat())
                self.assertEqual(starts, [])
                resumed = self.run_cycle(store, when=NOW + timedelta(minutes=3), starts=starts)
                self.assertEqual(resumed.decision_code, "CONTINUOUS_MOWING_START_SENT")
                self.assertEqual(len(starts), 1)

    def test_long_gap_without_schedule_does_not_release_after_two_minutes(self):
        store = InMemoryStateStore(AutomationState(
            parked_by_automation=True, automation_park_source="training", automation_restart_allowed=True,
            hydrawise_clear_since_utc=(NOW - timedelta(hours=3)).isoformat(),
            hydrawise_clear_origin="DATA_GAP",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=20)).isoformat(),
            last_hydrawise_active_count=0,
        ))
        starts = []
        first = self.run_cycle(store, starts=starts)
        self.assertEqual(first.details["hydrawise_release_gate"]["origin"], "POSSIBLE_IRRIGATION_DURING_GAP")
        after_two = self.run_cycle(store, when=NOW + timedelta(minutes=2), starts=starts)
        self.assertEqual(after_two.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
        self.assertEqual(after_two.details["hydrawise_release_gate"]["dry_until_utc"], (NOW + timedelta(minutes=150)).isoformat())
        self.assertEqual(starts, [])

    def test_external_irrigation_short_gap_cannot_shorten_physical_hold(self):
        physical_end = NOW - timedelta(minutes=20)
        store = InMemoryStateStore(AutomationState(
            parked_by_automation=True, automation_park_source="training", automation_restart_allowed=True,
            hydrawise_clear_since_utc=physical_end.isoformat(), hydrawise_clear_origin="IRRIGATION_END",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            last_hydrawise_active_count=0,
        ))
        missing = deepcopy(result())
        missing.details["hydrawise"]["safety"].update(available=False, fresh=False, clear_now=False)
        starts = []
        self.run_cycle(store, cycle=missing, starts=starts)
        for minute in (1, 2, 3):
            output = self.run_cycle(store, when=NOW + timedelta(minutes=minute), starts=starts)
        self.assertEqual(starts, [])
        release = output.details["hydrawise_release_gate"]
        self.assertFalse(release["allowed"])
        self.assertTrue(release["telemetry_confirmed"])
        self.assertEqual(release["dry_until_utc"], (physical_end + timedelta(minutes=150)).isoformat())

    def test_special_and_fail_closed_occupancy_cannot_be_overridden(self):
        for source, details in (
            ("special", {}), ("special", {"fail_closed": True}),
            ("training", {"items": [{"source": "training", "details": {"fail_closed": True}}]}),
        ):
            with self.subTest(source=source, details=details):
                block = {"start": (NOW - timedelta(minutes=20)).isoformat(), "end": (NOW + timedelta(hours=1)).isoformat(), "source": source, "details": details}
                key = "|".join(block[name] for name in ("start", "end", "source"))
                observed = deepcopy(result())
                observed.details["current_plan"]["blocked_now"] = block
                observed.details["current_plan"]["parking_block"] = block
                store = InMemoryStateStore(AutomationState(
                    parked_by_automation=True, automation_park_source="operator", automation_restart_allowed=False,
                    operator_request_id="explicit-action", operator_request_action="START_MOWING",
                    operator_request_status="PENDING", operator_requested_utc=NOW.isoformat(),
                    operator_request_expires_utc=(NOW + timedelta(minutes=5)).isoformat(),
                    operator_request_occupancy_override_key=key,
                    hydrawise_clear_since_utc=(NOW - timedelta(hours=3)).isoformat(),
                    last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
                ))
                starts = []
                output = self.run_cycle(store, cycle=observed, starts=starts)
                self.assertEqual(output.decision_code, "OPERATOR_OCCUPANCY_OVERRIDE_REJECTED")
                self.assertEqual(starts, [])

    def test_plain_match_and_training_remain_explicitly_requestable(self):
        self.assertTrue(occupancy_override_allowed({"source": "match+training", "details": {"items": [{"source": "match"}, {"source": "training"}]}}))
        self.assertFalse(occupancy_override_allowed(None))
        self.assertFalse(occupancy_override_allowed({"source": "match", "details": {"items": [{"source": "special"}]}}))


class ETagRegressionTests(unittest.TestCase):
    def test_successive_writes_use_returned_etag_and_still_reject_competitor(self):
        class Entity(dict):
            metadata: dict

        class Table:
            revision = 0
            entity = None
            received_etags = []

            def get_entity(self, **_):
                if self.entity is None:
                    raise ResourceNotFoundError("missing")
                value = Entity(self.entity)
                value.metadata = {"etag": str(self.revision)}
                return value

            def create_entity(self, *, entity):
                self.entity = entity
                self.revision += 1
                return {"etag": str(self.revision)}

            def update_entity(self, *, entity, etag, **_):
                self.received_etags.append(etag)
                if etag != str(self.revision):
                    error = HttpResponseError("conflict")
                    error.status_code = 412
                    raise error
                self.entity = entity
                self.revision += 1
                return {"etag": str(self.revision)}

        table = Table()
        writer = AzureTableStateStore(table)
        initial = writer.load()
        created = initial.record_cycle(started_utc=NOW, success=True, decision_code="FIRST")
        writer.save(created, expected_revision=initial.revision)
        competitor = AzureTableStateStore(table)
        competing_state = competitor.load()
        updated = created.record_cycle(started_utc=NOW + timedelta(minutes=1), success=True, decision_code="SECOND")
        writer.save(updated, expected_revision=created.revision)
        third = updated.record_cycle(started_utc=NOW + timedelta(minutes=2), success=True, decision_code="THIRD")
        writer.save(third, expected_revision=updated.revision)
        self.assertEqual(table.received_etags, ["1", "2"])
        with self.assertRaises(StateConflictError):
            competitor.save(competing_state.record_cycle(started_utc=NOW, success=True, decision_code="STALE"), expected_revision=competing_state.revision)


if __name__ == "__main__":
    unittest.main()
