from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.hydrawise import (
    HydrawiseError,
    evaluate_continuous_clear_confirmation,
    evaluate_safety_status,
    parse_relay_id_allowlist,
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
    def test_exact_relay_allowlist_is_required_even_when_count_is_unchanged(self) -> None:
        allowlist = [1, 2, 3, 4, 5, 6, 7]
        config = {**CONFIG, "expected_relay_ids": allowlist}
        exact = evaluate_safety_status(
            status(
                *(
                    {"relay_id": relay_id, "name": f"Zone {relay_id}", "time": 3600, "run": 600}
                    for relay_id in allowlist
                )
            ),
            config,
            now_utc=NOW,
        )
        substituted = evaluate_safety_status(
            status(
                *(
                    {"relay_id": relay_id, "name": f"Zone {relay_id}", "time": 3600, "run": 600}
                    for relay_id in [1, 2, 3, 4, 5, 6, 999]
                )
            ),
            config,
            now_utc=NOW,
        )
        self.assertTrue(exact.relay_set_valid)
        self.assertTrue(exact.clear_now)
        self.assertFalse(substituted.relay_set_valid)
        self.assertFalse(substituted.clear_now)
        self.assertEqual(substituted.selected_zone_count, 7)
        self.assertEqual(substituted.expected_relay_ids, tuple(allowlist))
        self.assertIn(999, substituted.observed_relay_ids)

    def test_duplicate_or_extra_relay_fails_exact_allowlist(self) -> None:
        config = {**CONFIG, "expected_relay_ids": [1, 2, 3, 4, 5, 6, 7]}
        for relay_ids in ([1, 2, 3, 4, 5, 6, 7, 8], [1, 2, 3, 4, 5, 6, 6]):
            with self.subTest(relay_ids=relay_ids):
                snapshot = evaluate_safety_status(
                    status(
                        *(
                            {"relay_id": relay_id, "name": str(relay_id), "time": 3600, "run": 600}
                            for relay_id in relay_ids
                        )
                    ),
                    config,
                    now_utc=NOW,
                )
                self.assertFalse(snapshot.relay_set_valid)
                self.assertFalse(snapshot.clear_now)

    def test_relay_allowlist_parser_rejects_missing_duplicate_and_wrong_count(self) -> None:
        self.assertEqual(
            parse_relay_id_allowlist("3,1,2", expected_count=3, required=True),
            (1, 2, 3),
        )
        for value in (None, "1,1,2", "1,2", "1,x,3", "1,,3"):
            with self.subTest(value=value):
                with self.assertRaises(HydrawiseError):
                    parse_relay_id_allowlist(value, expected_count=3, required=True)

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

    def test_release_opens_only_after_ninety_continuous_minutes(self) -> None:
        before = evaluate_continuous_clear_confirmation(
            available=True,
            fresh=True,
            clear_now=True,
            physical_reason="Hydrawise ist frei.",
            clear_since_utc=(NOW - timedelta(minutes=89, seconds=59)).isoformat(),
            now_utc=NOW,
            required_clear_minutes=90,
            persistent_state_available=True,
        )
        after = evaluate_continuous_clear_confirmation(
            available=True,
            fresh=True,
            clear_now=True,
            physical_reason="Hydrawise ist frei.",
            clear_since_utc=(NOW - timedelta(minutes=90)).isoformat(),
            now_utc=NOW,
            required_clear_minutes=90,
            persistent_state_available=True,
        )
        self.assertFalse(before.allowed)
        self.assertTrue(after.allowed)

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
