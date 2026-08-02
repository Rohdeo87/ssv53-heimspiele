from __future__ import annotations

import inspect
import unittest

import mower.husqvarna as husqvarna
from mower.husqvarna import (
    HusqvarnaError,
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
            "mower": {
                "activity": "PARKED_IN_CS",
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
                    },
                }
            ],
        },
    }


class HusqvarnaParsingTests(unittest.TestCase):
    def test_snapshot_contains_required_safety_data(self) -> None:
        snapshot = parse_snapshot(
            mower_item("abc", name="Schaf", model="AUTOMOWER 580 EPOS")
        )
        self.assertEqual(snapshot.mower_id, "abc")
        self.assertEqual(snapshot.battery_percent, 97)
        self.assertEqual(snapshot.activity, "PARKED_IN_CS")
        self.assertEqual(snapshot.external_reason_id, 253053)
        self.assertEqual(snapshot.work_areas[0]["name"], "Rasenfläche")

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


if __name__ == "__main__":
    unittest.main()
