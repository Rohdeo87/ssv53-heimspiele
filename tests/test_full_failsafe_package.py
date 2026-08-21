from __future__ import annotations

import ast
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_azure_full_failsafe_package import build_package


ROOT = Path(__file__).resolve().parents[1]


class FullFailsafePackageTests(unittest.TestCase):
    def test_package_contains_only_scoped_write_modules_and_locked_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "full-failsafe.zip"
            result = build_package(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("package-manifest.json"))
                sources = {
                    name: archive.read(name)
                    for name in names
                    if name.endswith(".py")
                }
                joined = b"\n".join(sources.values())
            self.assertEqual(
                manifest["safety_stage"],
                "FULL_FAILSAFE_7_ZONES_120_MIN_CAPABLE_LOCKED",
            )
            self.assertEqual(manifest["expected_hydrawise_zone_count"], 7)
            self.assertEqual(
                manifest["expected_hydrawise_relay_ids"],
                [9104894, 9104906, 9104909, 9104911, 9104913, 9104920, 9104921],
            )
            self.assertEqual(manifest["hydrawise_continuous_clear_minutes"], 120)
            self.assertEqual(manifest["irrigation_plan_lease_minutes"], 3)
            self.assertEqual(
                manifest["irrigation_plan_change_confirmation_minutes"],
                2,
            )
            self.assertTrue(
                manifest["hydrawise_app_suspension_releases_unused_window"]
            )
            self.assertTrue(
                manifest["hydrawise_future_zone_duration_changes_supported"]
            )
            self.assertTrue(
                manifest["hydrawise_active_zone_command_remains_immutable"]
            )
            self.assertTrue(
                manifest[
                    "hydrawise_confirmed_early_stop_cancels_remaining_zones"
                ]
            )
            self.assertTrue(manifest["manual_failed_irrigation_reset_implemented"])
            self.assertTrue(manifest["manual_reset_requires_function_auth"])
            self.assertFalse(manifest["manual_reset_sends_device_commands"])
            self.assertTrue(manifest["work_area_cutting_height_control_implemented"])
            self.assertEqual(manifest["work_area_cutting_height_mm_range"], [20, 60])
            self.assertTrue(manifest["central_collision_notifications_implemented"])
            self.assertTrue(manifest["collision_notification_recipients_server_configured"])
            self.assertTrue(manifest["trainer_contact_registration_removed"])
            self.assertTrue(manifest["notification_timer_separate_from_mower"])
            self.assertTrue(manifest["irrigation_schedule_dashboard_controls_implemented"])
            self.assertTrue(manifest["irrigation_schedule_skip_next_implemented"])
            self.assertTrue(manifest["irrigation_schedule_pause_until_implemented"])
            self.assertTrue(manifest["irrigation_schedule_safe_resume_implemented"])
            self.assertTrue(manifest["irrigation_schedule_custom_next_run_implemented"])
            self.assertTrue(manifest["irrigation_schedule_per_zone_selection_implemented"])
            self.assertTrue(manifest["irrigation_schedule_per_zone_duration_implemented"])
            self.assertTrue(manifest["irrigation_schedule_two_cycle_confirmation_required"])
            self.assertTrue(manifest["irrigation_schedule_mower_park_precedence_required"])
            self.assertIn("occupancy_notifications.py", names)
            self.assertIn("mower/full_failsafe.py", names)
            self.assertIn("mower/irrigation_recovery.py", names)
            self.assertIn("mower/hydrawise_actions.py", names)
            self.assertIn("mower/husqvarna_statistics_actions.py", names)
            local_modules = {
                name[:-3].replace("/", ".")
                for name in names
                if name.endswith(".py")
            }
            missing_local_imports: list[tuple[str, str]] = []
            for name in names:
                if not name.endswith(".py"):
                    continue
                tree = ast.parse(sources[name], filename=name)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom) or not node.module:
                        continue
                    is_local = node.module.startswith(("mower", "occupancy")) or node.module in {
                        "order_mail",
                        "occupancy_notifications",
                        "training_cancellations",
                        "special_occupancy",
                    }
                    if (
                        is_local
                        and node.module not in local_modules
                        and node.module + ".__init__" not in local_modules
                    ):
                        missing_local_imports.append((name, node.module))
            self.assertEqual(missing_local_imports, [])
            self.assertIn(b"setzone.php", joined)
            self.assertNotIn(b"ResumeSchedule", joined)
            self.assertTrue(result["sha256"])


if __name__ == "__main__":
    unittest.main()
