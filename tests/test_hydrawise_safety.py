from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.hydrawise import (
    evaluate_continuous_clear_confirmation,
    evaluate_safety_status,
)


NOW = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)
CONFIG = {
    "enabled": True,
    "include_all_zones": True,
    "before_minutes": 30,
}


def status(*relays: dict, observed: datetime = NOW) -> dict:
    return {
        "time": int(observed.timestamp()),
        "relays": list(relays),
    }


class HydrawiseSafetyTests(unittest.TestCase):
    def test_running_zone_is_never_clear(self) -> None:
        result = evaluate_safety_status(
            status(
                {
                    "relay_id": 1,
                    "name": "Rasen Nord",
                    "time": 1,
                    "run": 600,
                }
            ),
            CONFIG,
            now_utc=NOW,
        )
        self.assertFalse(result.clear_now)
        self.assertEqual(result.active_zone_count, 1)

    def test_imminent_zone_inside_buffer_is_not_clear(self) -> None:
        result = evaluate_safety_status(
            status(
                {
                    "relay_id": 2,
                    "name": "Rasen Süd",
                    "time": 20 * 60,
                    "run": 900,
                }
            ),
            CONFIG,
            now_utc=NOW,
        )
        self.assertFalse(result.clear_now)
        self.assertEqual(result.imminent_zone_count, 1)

    def test_future_zone_outside_buffer_can_be_clear_now(self) -> None:
        result = evaluate_safety_status(
            status(
                {
                    "relay_id": 3,
                    "name": "Rasen Mitte",
                    "time": 60 * 60,
                    "run": 900,
                }
            ),
            CONFIG,
            now_utc=NOW,
        )
        self.assertTrue(result.clear_now)
        self.assertTrue(result.fresh)

    def test_stale_status_fails_closed(self) -> None:
        result = evaluate_safety_status(
            status(
                {
                    "relay_id": 1,
                    "name": "Rasen Nord",
                    "time": 60 * 60,
                    "run": 600,
                },
                observed=NOW - timedelta(minutes=10),
            ),
            CONFIG,
            now_utc=NOW,
        )
        self.assertFalse(result.clear_now)
        self.assertFalse(result.fresh)

    def test_missing_or_empty_status_fails_closed(self) -> None:
        for payload in (None, status()):
            with self.subTest(payload=payload):
                result = evaluate_safety_status(
                    payload,
                    CONFIG,
                    now_utc=NOW,
                )
                self.assertFalse(result.clear_now)
                self.assertFalse(result.available)

    def test_release_stays_locked_until_ten_continuous_minutes(self) -> None:
        result = evaluate_continuous_clear_confirmation(
            available=True,
            fresh=True,
            clear_now=True,
            physical_reason="Hydrawise ist frei.",
            clear_since_utc=(NOW - timedelta(minutes=9, seconds=59)).isoformat(),
            now_utc=NOW,
            required_clear_minutes=10,
            persistent_state_available=True,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.confirmed_for_seconds, 599)
        self.assertEqual(
            result.release_at_utc,
            (NOW + timedelta(seconds=1)).isoformat(),
        )

    def test_release_opens_after_ten_continuous_minutes(self) -> None:
        result = evaluate_continuous_clear_confirmation(
            available=True,
            fresh=True,
            clear_now=True,
            physical_reason="Hydrawise ist frei.",
            clear_since_utc=(NOW - timedelta(minutes=10)).isoformat(),
            now_utc=NOW,
            required_clear_minutes=10,
            persistent_state_available=True,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.confirmed_for_seconds, 600)

    def test_missing_persistent_state_fails_closed(self) -> None:
        result = evaluate_continuous_clear_confirmation(
            available=True,
            fresh=True,
            clear_now=True,
            physical_reason="Hydrawise ist frei.",
            clear_since_utc=(NOW - timedelta(hours=1)).isoformat(),
            now_utc=NOW,
            required_clear_minutes=10,
            persistent_state_available=False,
        )
        self.assertFalse(result.allowed)
        self.assertIn("gespeichert", result.reason)

    def test_new_irrigation_interrupts_an_existing_release_chain(self) -> None:
        result = evaluate_continuous_clear_confirmation(
            available=True,
            fresh=True,
            clear_now=False,
            physical_reason="Mindestens eine Hydrawise-Zone läuft aktuell.",
            clear_since_utc=(NOW - timedelta(hours=1)).isoformat(),
            now_utc=NOW,
            required_clear_minutes=10,
            persistent_state_available=True,
        )
        self.assertFalse(result.allowed)
        self.assertFalse(result.physical_clear_now)
        self.assertIn("läuft", result.reason)


if __name__ == "__main__":
    unittest.main()
