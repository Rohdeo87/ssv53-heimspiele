from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.irrigation_schedule import (
    IrrigationScheduleValidationError,
    append_history,
    load_history,
    validate_schedule_request,
)


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def requested_zones(*, selected: bool = True) -> list[dict]:
    return [
        {"zone": zone, "runSeconds": zone * 60 + 300, "selected": selected}
        for zone in range(1, 8)
    ]


class IrrigationScheduleValidationTests(unittest.TestCase):
    def test_pause_requires_safe_bounded_future_time(self) -> None:
        for until in (
            NOW - timedelta(minutes=1),
            NOW + timedelta(minutes=4),
            NOW + timedelta(days=31),
        ):
            with self.subTest(until=until), self.assertRaises(
                IrrigationScheduleValidationError
            ):
                validate_schedule_request(
                    "PAUSE_IRRIGATION_UNTIL",
                    {"pauseUntil": until.isoformat()},
                    now_utc=NOW,
                    expected_zone_count=7,
                )
        accepted = validate_schedule_request(
            "PAUSE_IRRIGATION_UNTIL",
            {"pauseUntil": (NOW + timedelta(days=3)).isoformat()},
            now_utc=NOW,
            expected_zone_count=7,
        )
        self.assertEqual(
            accepted["pauseUntil"], (NOW + timedelta(days=3)).isoformat()
        )

    def test_custom_plan_rejects_too_early_duplicate_missing_and_empty_selection(self) -> None:
        cases = [
            {
                "desiredStart": (NOW + timedelta(minutes=44)).isoformat(),
                "zones": requested_zones(),
            },
            {
                "desiredStart": (NOW + timedelta(hours=2)).isoformat(),
                "zones": requested_zones()[:-1],
            },
            {
                "desiredStart": (NOW + timedelta(hours=2)).isoformat(),
                "zones": [*requested_zones()[:-1], requested_zones()[0]],
            },
            {
                "desiredStart": (NOW + timedelta(hours=2)).isoformat(),
                "zones": requested_zones(selected=False),
            },
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(
                IrrigationScheduleValidationError
            ):
                validate_schedule_request(
                    "CUSTOMIZE_NEXT_IRRIGATION",
                    payload,
                    now_utc=NOW,
                    expected_zone_count=7,
                )

    def test_custom_plan_normalizes_zone_order_and_keeps_partial_selection(self) -> None:
        zones = list(reversed(requested_zones()))
        zones[3]["selected"] = False
        accepted = validate_schedule_request(
            "CUSTOMIZE_NEXT_IRRIGATION",
            {
                "desiredStart": (NOW + timedelta(hours=2)).isoformat(),
                "zones": zones,
            },
            now_utc=NOW,
            expected_zone_count=7,
        )
        self.assertEqual([item["zone"] for item in accepted["zones"]], list(range(1, 8)))
        self.assertEqual(sum(item["selected"] for item in accepted["zones"]), 6)

    def test_parameterless_actions_reject_stale_payload(self) -> None:
        for action in ("SKIP_NEXT_IRRIGATION", "RESUME_IRRIGATION_SCHEDULE"):
            with self.subTest(action=action), self.assertRaises(
                IrrigationScheduleValidationError
            ):
                validate_schedule_request(
                    action,
                    {"pauseUntil": (NOW + timedelta(days=1)).isoformat()},
                    now_utc=NOW,
                    expected_zone_count=7,
                )

    def test_history_is_bounded_and_contains_no_unbounded_input(self) -> None:
        value = None
        for index in range(20):
            value = append_history(
                value,
                now_utc=NOW + timedelta(minutes=index),
                action="A" * 100,
                status="S" * 100,
                summary="X" * 1000,
            )
        history = load_history(value)
        self.assertEqual(len(history), 12)
        self.assertEqual(len(history[0]["action"]), 48)
        self.assertEqual(len(history[0]["status"]), 24)
        self.assertEqual(len(history[0]["summary"]), 240)


if __name__ == "__main__":
    unittest.main()
