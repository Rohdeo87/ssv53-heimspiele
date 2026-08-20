from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from mower.cutting_height import (
    cutting_height_mm_to_percent,
    cutting_height_percent_to_mm,
)
from mower.husqvarna import HusqvarnaError
from mower.husqvarna_cutting_height_actions import set_work_area_cutting_height


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b""


class CuttingHeightTests(unittest.TestCase):
    def test_verified_580_epos_mapping_uses_millimetres(self) -> None:
        self.assertEqual(cutting_height_percent_to_mm(12), 25)
        self.assertEqual(cutting_height_mm_to_percent(20), 0)
        self.assertEqual(cutting_height_mm_to_percent(25), 13)
        self.assertEqual(cutting_height_mm_to_percent(30), 25)
        self.assertEqual(cutting_height_mm_to_percent(60), 100)

    def test_work_area_patch_contains_only_cutting_height(self) -> None:
        captured = {}

        def open_request(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response()

        with patch(
            "mower.husqvarna_cutting_height_actions.get_access_token",
            return_value="token",
        ), patch(
            "mower.husqvarna_cutting_height_actions.urlopen",
            side_effect=open_request,
        ):
            result = set_work_area_cutting_height(
                "client", "secret", "mower-1", 849199, 25
            )

        request = captured["request"]
        self.assertEqual(request.method, "PATCH")
        self.assertEqual(
            request.full_url,
            "https://api.amc.husqvarna.dev/v1/mowers/mower-1/workAreas/849199",
        )
        self.assertEqual(
            json.loads(request.data),
            {
                "data": {
                    "type": "workArea",
                    "id": 849199,
                    "attributes": {"cuttingHeight": 25},
                }
            },
        )
        self.assertEqual(result, {"status_code": 202, "accepted": True})

    def test_invalid_percent_is_rejected_before_network(self) -> None:
        with self.assertRaises(HusqvarnaError):
            set_work_area_cutting_height("client", "secret", "mower", 1, 101)


if __name__ == "__main__":
    unittest.main()
