from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

import mower.husqvarna as husqvarna
from mower.husqvarna import (
    HusqvarnaError,
    clear_access_token_cache,
    get_access_token,
    parse_snapshot,
    select_mower,
)


def mower_item(
    mower_id: str,
    *,
    name: str,
    model: str,
) -> dict:
    return {
        "id": mower_id,
        "attributes": {
            "system": {"name": name, "model": model},
            "battery": {"batteryPercent": 97},
            "metadata": {"connected": True, "statusTimestamp": 1785600000123},
            "settings": {"cuttingHeight": 5},
            "statistics": {
                "cuttingBladeUsageTime": 7200,
                "numberOfChargingCycles": 41,
                "numberOfCollisions": 3,
                "totalChargingTime": 54000,
                "totalCuttingTime": 360000,
                "totalDriveDistance": 123456,
                "totalRunningTime": 410000,
                "totalSearchingTime": 8000,
            },
            "mower": {
                "activity": "PARKED_IN_CS",
                "inactiveReason": "NONE",
                "state": "RESTRICTED",
                "mode": "MAIN_AREA",
                "errorCode": 0,
            },
            "planner": {
                "override": {"action": "FORCE_PARK"},
                "restrictedReason": "EXTERNAL",
                "externalReason": 253053,
                "nextStartTimestamp": 1785600000000,
            },
            "workAreas": [
                {
                    "id": 17,
                    "attributes": {
                        "name": "Rasenfläche",
                        "enable": True,
                        "type": "SYSTEMATIC",
                        "progress": 65,
                        "lastTimeCompleted": 1787184000,
                        "lastTimeAbandoned": 1787170000,
                        "cuttingHeight": 12,
                        "useGlobalCuttingHeight": False,
                    },
                }
            ],
        },
    }


class HusqvarnaParsingTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_access_token_cache()

    def test_snapshot_contains_required_safety_data(self) -> None:
        snapshot = parse_snapshot(
            mower_item("abc", name="Schaf", model="AUTOMOWER 580 EPOS")
        )
        self.assertEqual(snapshot.mower_id, "abc")
        self.assertEqual(snapshot.battery_percent, 97)
        self.assertEqual(snapshot.activity, "PARKED_IN_CS")
        self.assertEqual(snapshot.inactive_reason, "NONE")
        self.assertEqual(snapshot.external_reason_id, 253053)
        self.assertTrue(snapshot.connected)
        self.assertEqual(snapshot.status_timestamp_ms, 1785600000123)
        self.assertEqual(snapshot.work_areas[0]["name"], "Rasenfläche")
        self.assertEqual(snapshot.work_areas[0]["type"], "SYSTEMATIC")
        self.assertEqual(snapshot.work_areas[0]["progress"], 65)
        self.assertEqual(snapshot.work_areas[0]["last_time_completed"], 1787184000)
        self.assertEqual(snapshot.work_areas[0]["cutting_height_percent"], 12)
        self.assertFalse(snapshot.work_areas[0]["use_global_cutting_height"])
        self.assertEqual(snapshot.global_cutting_height_percent, 5)
        self.assertEqual(snapshot.statistics["cutting_blade_usage_seconds"], 7200)
        self.assertEqual(snapshot.statistics["charging_cycles"], 41)
        self.assertEqual(snapshot.statistics["total_cutting_seconds"], 360000)

    def test_epos_satellite_search_reason_is_preserved(self) -> None:
        item = mower_item("abc", name="Schaf", model="AUTOMOWER 580 EPOS")
        item["attributes"]["mower"]["activity"] = "MOWING"
        item["attributes"]["mower"]["inactiveReason"] = "SEARCHING_FOR_SATELLITES"
        snapshot = parse_snapshot(item)
        self.assertEqual(snapshot.activity, "MOWING")
        self.assertEqual(snapshot.inactive_reason, "SEARCHING_FOR_SATELLITES")

    def test_named_mower_is_selected(self) -> None:
        selected = select_mower(
            [
                mower_item("1", name="Anderer", model="Modell A"),
                mower_item("2", name="Schaf", model="AUTOMOWER 580 EPOS"),
            ]
        )
        self.assertEqual(selected["id"], "2")

    def test_ambiguous_selection_is_rejected(self) -> None:
        with self.assertRaises(HusqvarnaError):
            select_mower(
                [
                    mower_item("1", name="A", model="Modell A"),
                    mower_item("2", name="B", model="Modell B"),
                ]
            )

    def test_read_only_module_contains_no_action_endpoint(self) -> None:
        source = inspect.getsource(husqvarna)
        self.assertNotIn("/actions", source)
        self.assertFalse(hasattr(husqvarna, "send_mower_action"))

    @patch("mower.husqvarna._request_json")
    def test_status_and_followup_action_share_one_access_token(self, request) -> None:
        request.return_value = {
            "access_token": "shared-token",
            "expires_in": 3600,
        }
        first = get_access_token("client", "secret")
        second = get_access_token("client", "secret")
        self.assertEqual(first, "shared-token")
        self.assertEqual(second, "shared-token")
        self.assertEqual(request.call_count, 1)

    def test_action_modules_use_shared_token_provider(self) -> None:
        import mower.husqvarna_actions as park_actions
        import mower.husqvarna_start_actions as start_actions
        import mower.husqvarna_cutting_height_actions as height_actions
        import mower.husqvarna_statistics_actions as statistics_actions

        for module in (park_actions, start_actions, height_actions, statistics_actions):
            source = inspect.getsource(module)
            self.assertIn("get_access_token", source)
            self.assertNotIn("AUTH_URL", source)


if __name__ == "__main__":
    unittest.main()
