from __future__ import annotations

import unittest
from unittest.mock import patch

from mower.hydrawise import HydrawiseError
from mower.hydrawise_actions import start_zone_for, stop_zone_now, suspend_zone_until


class HydrawiseActionTests(unittest.TestCase):
    @patch("mower.hydrawise_actions._get_json")
    def test_suspend_targets_exactly_one_zone_until_epoch(self, request) -> None:
        request.return_value = {"message_type": "info"}
        suspend_zone_until("secret", 9104894, 1786600000, "123")
        endpoint, parameters = request.call_args.args[:2]
        self.assertEqual(endpoint, "setzone.php")
        self.assertEqual(parameters["action"], "suspend")
        self.assertEqual(parameters["relay_id"], 9104894)
        self.assertEqual(parameters["custom"], 1786600000)
        self.assertEqual(parameters["controller_id"], "123")

    @patch("mower.hydrawise_actions._get_json")
    def test_start_uses_planned_zone_runtime(self, request) -> None:
        request.return_value = {"message_type": "info"}
        start_zone_for("secret", 9104921, 1800, "123")
        endpoint, parameters = request.call_args.args[:2]
        self.assertEqual(endpoint, "setzone.php")
        self.assertEqual(parameters["action"], "run")
        self.assertEqual(parameters["relay_id"], 9104921)
        self.assertEqual(parameters["custom"], 1800)

    @patch("mower.hydrawise_actions._get_json")
    def test_stop_targets_exactly_the_running_zone(self, request) -> None:
        request.return_value = {"message_type": "info"}
        stop_zone_now("secret", 9104921, "123")
        endpoint, parameters = request.call_args.args[:2]
        self.assertEqual(endpoint, "setzone.php")
        self.assertEqual(parameters["action"], "stop")
        self.assertEqual(parameters["relay_id"], 9104921)
        self.assertEqual(parameters["controller_id"], "123")
        self.assertNotIn("custom", parameters)

    def test_invalid_zone_or_runtime_is_never_sent(self) -> None:
        with self.assertRaises(HydrawiseError):
            start_zone_for("secret", 0, 1200)
        with self.assertRaises(HydrawiseError):
            start_zone_for("secret", 1, 30)
        with self.assertRaises(HydrawiseError):
            suspend_zone_until("secret", 0, 1786600000)
        with self.assertRaises(HydrawiseError):
            stop_zone_now("secret", 0)


if __name__ == "__main__":
    unittest.main()
