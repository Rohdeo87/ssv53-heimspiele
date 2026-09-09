from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mower.coordination_simulation import (
    DryingEvidence, Interval, JournalConflict, Settings, SimulationJournal,
    example_scenario, local_instant, propose, safety_blockers, simulate_day, union_minutes,
)


class CoordinationSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario, self.now, self.evidence = example_scenario()
        self.settings = Settings(enabled=True)

    def proposal(self, **kwargs):
        return propose(kwargs.pop("scenario", self.scenario), now=self.now,
                       evidence=kwargs.pop("evidence", self.evidence),
                       settings=kwargs.pop("settings", self.settings), **kwargs)

    def blockers(self, *, evidence=None, occupancy=None):
        return safety_blockers(self.scenario.need, self.now, self.now, evidence or self.evidence,
                               occupancy if occupancy is not None else self.scenario.occupancy,
                               self.settings, before_start=True)

    def test_default_disabled_and_device_execution_is_unconditionally_rejected(self):
        self.assertIn("DISABLED", self.proposal(settings=Settings()).blockers)
        with self.assertRaises(ValueError):
            Settings(execution_enabled=True)
        for kwargs in ({"drying_minutes": 149}, {"return_minutes": 3}, {"dock_confirmation_minutes": 0},
                       {"return_minutes": 4.2}, {"enabled": "false"}, {"minimum_gain_minutes": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_charging_alone_never_creates_or_authorizes_water_demand(self):
        for change in ({"required": False}, {"timing_window_approved": False}):
            scenario = replace(self.scenario, need=replace(self.scenario.need, **change))
            self.assertIsNone(self.proposal(scenario=scenario).selected_start)
        self.assertIsNone(self.proposal(evidence=replace(self.evidence, need_still_current=False)).selected_start)
        with self.assertRaises(ValueError):
            replace(self.scenario.need, required="false")

    def test_missing_stale_or_future_telemetry_blocks_each_source(self):
        for field in ("mower_observed_at", "irrigation_observed_at", "occupancy_observed_at"):
            for delta in (-181, 1):
                evidence = replace(self.evidence, **{field: self.now + timedelta(seconds=delta)})
                with self.subTest(field=field, delta=delta):
                    self.assertIsNone(self.proposal(evidence=evidence).selected_start)

    def test_two_independent_fresh_station_observations_are_required(self):
        cases = ({"activity": "GOING_HOME"}, {"connected": False}, {"own_park_confirmed": False},
                 {"dock_observations": (self.now, self.now)},
                 {"dock_observations": (self.now - timedelta(minutes=10), self.now)},
                 {"dock_observations": (self.now, self.now + timedelta(minutes=1))})
        for change in cases:
            with self.subTest(change=change):
                self.assertIsNone(self.proposal(evidence=replace(self.evidence, **change)).selected_start)

    def test_spatial_safety_manual_stop_and_active_unexpected_water_block(self):
        for change in ({"station_and_paths_safe": False}, {"manual_stop": True}, {"unexpected_active_zone": True}):
            with self.subTest(change=change):
                self.assertIsNone(self.proposal(evidence=replace(self.evidence, **change)).selected_start)

    def test_start_requires_verified_device_hold_and_original_schedule_suppression(self):
        for field in ("mower_held_until", "original_suppressed_until"):
            with self.subTest(field=field):
                evidence = replace(self.evidence, **{field: None})
                self.assertTrue(self.blockers(evidence=evidence))
                # A shadow suggestion may identify the opportunity, but cannot
                # become a simulated intent before controls are confirmed.
                self.assertIsNotNone(self.proposal(evidence=evidence).selected_start)

    def test_approved_window_and_coverage_cannot_be_inferred_from_empty_blocks(self):
        too_early = replace(self.scenario.need, earliest_start=self.now + timedelta(minutes=1))
        self.assertIsNone(self.proposal(scenario=replace(self.scenario, need=too_early)).selected_start)
        self.assertIn("OCCUPANCY_COVERAGE_INCOMPLETE", self.blockers(
            evidence=replace(self.evidence, occupancy_complete_until=self.now + timedelta(minutes=20)), occupancy=[]))

    def test_charging_bundle_improves_productive_minutes_without_shortening_water_or_drying(self):
        # A network attempt would fail this test. The library only models data.
        with patch("socket.socket", side_effect=AssertionError("No network in offline tests")):
            baseline = simulate_day(self.scenario, self.scenario.need.original_start, self.settings)
            simple = self.proposal()
            predictive = self.proposal(strategy="predictive")
            bundled = simulate_day(self.scenario, simple.selected_start, self.settings)
        self.assertEqual(baseline["productive_mowing_minutes"], 642)
        self.assertEqual(bundled["productive_mowing_minutes"], 702)
        self.assertEqual(simple.expected_gain_minutes, 60)
        self.assertEqual(simple.selected_start, predictive.selected_start)
        self.assertEqual(predictive.candidate_count, 23)
        self.assertEqual(baseline["final_battery_fraction"], bundled["final_battery_fraction"])
        for result in (baseline, bundled):
            self.assertEqual(result["irrigation_minutes"], 160)
            self.assertEqual(result["reason_minutes_including_overlaps"]["DRYING"], 150)
            self.assertEqual(result["water_needs_fulfilled"], 1)
            self.assertFalse(result["safety_violations"])
            self.assertIsNone(result["water_volume_litres"])

    def test_physical_and_primary_reason_accounts_balance_without_double_counting(self):
        result = simulate_day(self.scenario, self.now, self.settings)
        self.assertEqual(sum(result[key] for key in ("productive_mowing_minutes", "return_minutes", "charging_minutes", "parked_minutes")), 1440)
        self.assertEqual(sum(result["primary_reason_minutes_exclusive"].values()), 1440)
        self.assertEqual(result["nonproductive_union_minutes"], 738)
        # Charging overlaps water/occupancy. Adding these raw counters would be wrong.
        self.assertGreater(sum(result["reason_minutes_including_overlaps"].values()), result["nonproductive_union_minutes"])

    def test_minimum_gain_avoids_needless_replanning(self):
        self.assertIn("GAIN_BELOW_THRESHOLD", self.proposal(settings=replace(self.settings, minimum_gain_minutes=100)).blockers)

    def test_interval_union_retains_real_elapsed_minutes_at_midnight_and_dst(self):
        first = Interval(local_instant("2026-09-15T23:30:00"), local_instant("2026-09-16T00:30:00"), "first")
        second = Interval(local_instant("2026-09-16T00:00:00"), local_instant("2026-09-16T01:00:00"), "second")
        self.assertEqual(union_minutes((first, second)), 90)
        for start, end, minutes in (("2026-03-29T00:00:00", "2026-03-30T00:00:00", 1380),
                                    ("2026-10-25T00:00:00", "2026-10-26T00:00:00", 1500)):
            with self.subTest(start=start):
                self.assertEqual(union_minutes([Interval(local_instant(start), local_instant(end), "day")]), minutes)

    def test_nonexistent_and_ambiguous_local_times_do_not_silently_choose_a_start(self):
        for value in ("2026-03-29T02:30:00", "2026-10-25T02:30:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_instant(value)
        early = local_instant("2026-10-25T02:30:00", fold=0)
        late = local_instant("2026-10-25T02:30:00", fold=1)
        self.assertEqual(late - early, timedelta(hours=1))
        with self.assertRaises(ValueError):
            Interval(datetime(2026, 9, 15), datetime(2026, 9, 16), "naive")


class PersistentCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.scenario, self.now, self.evidence = example_scenario()
        self.settings = Settings(enabled=True)
        self.proposal = propose(self.scenario, now=self.now, evidence=self.evidence, settings=self.settings)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "simulation-only.sqlite"
        self.journal = self.open()
        self.reservation = self.journal.reserve(self.scenario.need, self.proposal)

    def open(self):
        journal = SimulationJournal(self.path)
        self.addCleanup(journal.close)
        return journal

    def claim(self, journal=None, revision=1, **kwargs):
        return (journal or self.journal).claim_simulated_start(
            self.scenario.need.need_id, revision, now=kwargs.pop("now", self.now),
            evidence=kwargs.pop("evidence", self.evidence),
            occupancy=kwargs.pop("occupancy", self.scenario.occupancy), settings=self.settings,
        )

    def test_reservation_survives_restart_and_original_timer_never_gets_second_attempt(self):
        restarted = self.open()
        self.assertEqual(restarted.load(self.scenario.need.need_id), self.reservation)
        claimed = self.claim(restarted)
        self.assertEqual(claimed["status"], "INTENT_RECORDED")
        self.assertEqual(restarted.original_disposition(self.scenario.need.need_id), "SUPPRESSED_IN_SIMULATION")
        with self.assertRaises(JournalConflict):
            self.claim(self.journal, revision=claimed["revision"])

    def test_changed_demand_and_horizon_do_not_automatically_reshuffle_reservation(self):
        for need, proposal in (
            (replace(self.scenario.need, demand_reference="changed requirement"), self.proposal),
            (self.scenario.need, replace(self.proposal, selected_start=self.now + timedelta(minutes=5))),
        ):
            with self.subTest(need=need), self.assertRaises(JournalConflict):
                self.journal.reserve(need, proposal)
        self.assertEqual(self.journal.load(self.scenario.need.need_id), self.reservation)

    def test_new_or_relocated_match_is_revalidated_immediately_before_start(self):
        changed = [Interval(self.now + timedelta(minutes=20), self.now + timedelta(hours=1), "New match including buffers")]
        with self.assertRaisesRegex(JournalConflict, "OCCUPANCY_CONFLICT"):
            self.claim(occupancy=iter(changed))
        self.assertEqual(self.journal.load(self.scenario.need.need_id)["status"], "RESERVED")

    def test_restart_cannot_reuse_stale_telemetry_or_expired_suspension(self):
        for evidence in (replace(self.evidence, mower_observed_at=self.now - timedelta(minutes=4)),
                         replace(self.evidence, original_suppressed_until=self.now),
                         replace(self.evidence, activity="GOING_HOME")):
            with self.subTest(evidence=evidence), self.assertRaises(JournalConflict):
                self.claim(self.open(), evidence=evidence)

    def test_premature_or_missed_execution_time_requires_review(self):
        for now in (self.now - timedelta(minutes=1), self.now + timedelta(minutes=1)):
            with self.subTest(now=now), self.assertRaises(JournalConflict):
                self.claim(now=now)

    def test_two_simultaneous_schedulers_can_record_only_one_intent(self):
        barrier = threading.Barrier(2)

        def scheduler():
            journal = SimulationJournal(self.path)
            try:
                barrier.wait(timeout=5)
                self.claim(journal)
                return "claimed"
            except JournalConflict:
                return "conflict"
            finally:
                journal.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: scheduler(), range(2)))
        self.assertCountEqual(results, ["claimed", "conflict"])

    def test_accepted_but_lost_response_stays_unknown_after_restart_without_retry(self):
        claimed = self.claim()
        unknown = self.journal.response_lost(self.scenario.need.need_id, claimed["revision"])
        restarted = self.open()
        self.assertEqual(restarted.load(self.scenario.need.need_id)["status"], "OUTCOME_UNKNOWN")
        with self.assertRaises(JournalConflict):
            self.claim(restarted, revision=unknown["revision"])
        confirmed = restarted.confirm_started(self.scenario.need.need_id, unknown["revision"], observed_at=self.now + timedelta(minutes=1))
        self.assertEqual(confirmed["attempt_id"], claimed["attempt_id"])
        self.assertEqual(confirmed["status"], "RUNNING_CONFIRMED")

    def runs(self, *, first_short=False, gap_minutes=0):
        cursor = self.now
        runs = []
        for index, zone in enumerate(self.scenario.need.zones):
            end = cursor + timedelta(minutes=zone.minutes - int(first_short and index == 0))
            runs.append(Interval(cursor, end, zone.zone_id))
            cursor = end + timedelta(minutes=gap_minutes)
        return runs

    def running(self):
        claimed = self.claim()
        return self.journal.confirm_started(self.scenario.need.need_id, claimed["revision"], observed_at=self.now)

    def test_drying_uses_actual_last_zone_end_including_delays_not_planned_end(self):
        running = self.running()
        runs = self.runs(gap_minutes=3)
        complete = self.journal.confirm_zone_ends(self.scenario.need.need_id, running["revision"], runs, settings=self.settings)
        dry_until = datetime.fromisoformat(complete["dry_until"])
        self.assertEqual(dry_until, runs[-1].end + timedelta(minutes=150))
        self.assertEqual(dry_until - (self.now + self.scenario.need.duration + timedelta(minutes=150)), timedelta(minutes=18))
        self.assertTrue(complete["water_need_fulfilled"])
        self.assertEqual(self.journal.original_disposition(self.scenario.need.need_id), "SUPPRESSED_IN_SIMULATION")

    def test_partial_water_delivery_is_not_reported_as_fulfilled_and_not_retried(self):
        running = self.running()
        result = self.journal.confirm_zone_ends(self.scenario.need.need_id, running["revision"], self.runs(first_short=True), settings=self.settings)
        self.assertEqual(result["status"], "PARTIAL_NEEDS_REVIEW")
        self.assertFalse(result["water_need_fulfilled"])
        with self.assertRaises(JournalConflict):
            self.claim(revision=result["revision"])

    def test_missing_zone_end_is_unknown_not_complete(self):
        running = self.running()
        with self.assertRaises(JournalConflict):
            self.journal.confirm_zone_ends(self.scenario.need.need_id, running["revision"], self.runs()[:-1], settings=self.settings)
        self.assertEqual(self.journal.load(self.scenario.need.need_id)["status"], "RUNNING_CONFIRMED")

    def test_manual_stop_survives_replanning_and_restart(self):
        stopped = self.journal.latch_manual_stop(self.scenario.need.need_id, self.reservation["revision"])
        restarted = self.open()
        same = restarted.reserve(self.scenario.need, self.proposal)
        self.assertTrue(same["manual_stop_latched"])
        with self.assertRaisesRegex(JournalConflict, "MANUAL_STOP_LATCHED"):
            self.claim(restarted, revision=stopped["revision"], evidence=replace(self.evidence, manual_stop=False))


class DryingFailureReplayTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(enabled=True)
        self.end = datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc)
        self.state = DryingEvidence(last_clear_observed_at=self.end).irrigation_ended(self.end, self.settings)

    def test_one_failed_poll_changes_confidence_but_not_physical_drying_deadline(self):
        failed = self.state.status_failed(self.end + timedelta(minutes=1))
        recovered = failed.clear_observed(self.end + timedelta(minutes=2), self.settings, gap_water_impossible=True)
        self.assertEqual(failed.dry_until, self.state.dry_until)
        self.assertEqual(recovered.dry_until, self.state.dry_until)
        self.assertIsNone(recovered.unresolved_since)
        self.assertEqual(recovered.release_blockers(self.end + timedelta(minutes=2), self.settings), ("PHYSICAL_DRYING",))

    def test_long_gap_with_possible_irrigation_cannot_unlock_on_single_off_status(self):
        now = self.end + timedelta(hours=4)
        recovered = self.state.status_failed(self.end + timedelta(minutes=1)).clear_observed(now, self.settings)
        self.assertEqual(recovered.dry_until, self.state.dry_until)
        self.assertIn("POSSIBLE_UNOBSERVED_IRRIGATION", recovered.release_blockers(now, self.settings))

    def test_complete_history_distinguishes_no_event_from_an_event_in_the_gap(self):
        now = self.end + timedelta(hours=4)
        failed = self.state.status_failed(self.end + timedelta(minutes=1))
        no_event = failed.clear_observed(now, self.settings, complete_history_since=self.end)
        self.assertEqual(no_event.dry_until, self.state.dry_until)
        self.assertFalse(no_event.release_blockers(now, self.settings))
        actual_end = now - timedelta(minutes=30)
        event = failed.clear_observed(now, self.settings, complete_history_since=self.end,
                                      observed_irrigation_ends=(actual_end,))
        self.assertEqual(event.dry_until, actual_end + timedelta(minutes=150))
        self.assertEqual(event.release_blockers(now, self.settings), ("PHYSICAL_DRYING",))

    def test_unknown_history_can_only_use_a_conservative_explicit_last_possible_end(self):
        now = self.end + timedelta(hours=4)
        failed = self.state.status_failed(self.end + timedelta(minutes=1))
        with self.assertRaises(ValueError):
            failed.conservatively_bound_unknown_end(now, no_pending_commands=False, settings=self.settings)
        bounded = failed.conservatively_bound_unknown_end(now, no_pending_commands=True, settings=self.settings)
        self.assertEqual(bounded.dry_until, now + timedelta(minutes=150))
        self.assertIsNone(bounded.unresolved_since)

    def test_process_without_any_irrigation_history_stays_unknown(self):
        new = DryingEvidence().clear_observed(self.end, self.settings)
        self.assertIn("POSSIBLE_UNOBSERVED_IRRIGATION", new.release_blockers(self.end, self.settings))

    def test_physical_150_minutes_cross_both_dst_transitions_without_wall_clock_error(self):
        for value in ("2026-03-29T01:30:00", "2026-10-25T01:30:00"):
            end = local_instant(value)
            state = DryingEvidence().irrigation_ended(end, self.settings)
            self.assertEqual(state.dry_until - end, timedelta(minutes=150))


if __name__ == "__main__":
    unittest.main()
