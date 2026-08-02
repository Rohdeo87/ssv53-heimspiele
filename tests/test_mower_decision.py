from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from mower.decision import (
    classify_decision,
    parking_block_for,
)


TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 2, 10, 0, tzinfo=TZ)


def block(start_minutes: int, end_minutes: int, title: str = "Sperre"):
    return SimpleNamespace(
        start=NOW + timedelta(minutes=start_minutes),
        end=NOW + timedelta(minutes=end_minutes),
        title=title,
        source="test",
    )


def window(start_minutes: int, end_minutes: int):
    return SimpleNamespace(
        start=NOW + timedelta(minutes=start_minutes),
        end=NOW + timedelta(minutes=end_minutes),
    )


class ParkingLookaheadTests(unittest.TestCase):
    def test_next_block_inside_lookahead_is_used(self) -> None:
        candidate = block(10, 70, "Beregnung")
        self.assertIs(
            parking_block_for(
                active_block=None,
                next_block=candidate,
                now=NOW,
                lookahead_minutes=15,
            ),
            candidate,
        )

    def test_next_block_outside_lookahead_is_ignored(self) -> None:
        self.assertIsNone(
            parking_block_for(
                active_block=None,
                next_block=block(16, 70),
                now=NOW,
                lookahead_minutes=15,
            )
        )


class DecisionTests(unittest.TestCase):
    def test_active_block_and_mowing_means_would_park(self) -> None:
        active = block(-5, 60, "Beregnung")
        decision = classify_decision(
            now=NOW,
            active_block=active,
            parking_block=active,
            active_window=None,
            activity="MOWING",
            state="IN_OPERATION",
            error_code=0,
            override_action="NOT_ACTIVE",
            automation_owned_park=False,
            battery=80,
            minimum_remaining_minutes=30,
        )
        self.assertEqual(decision.code, "WOULD_PARK")
        self.assertEqual(decision.hypothetical_command, "PARK")

    def test_error_always_requires_manual_attention(self) -> None:
        decision = classify_decision(
            now=NOW,
            active_block=None,
            parking_block=None,
            active_window=window(-5, 120),
            activity="PARKED_IN_CS",
            state="ERROR",
            error_code=42,
            override_action="NOT_ACTIVE",
            automation_owned_park=False,
            battery=100,
            minimum_remaining_minutes=30,
        )
        self.assertEqual(decision.code, "MANUAL_ATTENTION")

    def test_manual_stop_is_never_started(self) -> None:
        decision = classify_decision(
            now=NOW,
            active_block=None,
            parking_block=None,
            active_window=window(-5, 120),
            activity="STOPPED_IN_GARDEN",
            state="STOPPED",
            error_code=0,
            override_action="NOT_ACTIVE",
            automation_owned_park=False,
            battery=100,
            minimum_remaining_minutes=30,
        )
        self.assertEqual(decision.code, "MANUAL_LOCK")
        self.assertIsNone(decision.hypothetical_command)

    def test_external_override_is_not_removed(self) -> None:
        decision = classify_decision(
            now=NOW,
            active_block=None,
            parking_block=None,
            active_window=window(-5, 120),
            activity="PARKED_IN_CS",
            state="RESTRICTED",
            error_code=0,
            override_action="FORCE_PARK",
            automation_owned_park=False,
            battery=100,
            minimum_remaining_minutes=30,
        )
        self.assertEqual(decision.code, "EXTERNAL_OVERRIDE")

    def test_own_park_can_only_be_released_when_safe(self) -> None:
        decision = classify_decision(
            now=NOW,
            active_block=None,
            parking_block=None,
            active_window=window(-5, 120),
            activity="PARKED_IN_CS",
            state="RESTRICTED",
            error_code=0,
            override_action="FORCE_PARK",
            automation_owned_park=True,
            battery=100,
            minimum_remaining_minutes=30,
        )
        self.assertEqual(
            decision.code,
            "WOULD_START_AFTER_AUTOMATION_PARK",
        )
        self.assertGreater(
            decision.hypothetical_duration_minutes or 0,
            30,
        )

    def test_existing_mowing_is_left_running_in_free_window(self) -> None:
        decision = classify_decision(
            now=NOW,
            active_block=None,
            parking_block=None,
            active_window=window(-5, 120),
            activity="MOWING",
            state="IN_OPERATION",
            error_code=0,
            override_action="NOT_ACTIVE",
            automation_owned_park=False,
            battery=60,
            minimum_remaining_minutes=30,
        )
        self.assertEqual(decision.code, "ALREADY_MOWING")


if __name__ == "__main__":
    unittest.main()
