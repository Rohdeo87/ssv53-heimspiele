from __future__ import annotations

import unittest
from datetime import datetime, timezone

from mower.controller import run_control_cycle
from mower.runtime import ControlMode, RuntimeSettings


class ControlModeTests(unittest.TestCase):
    def test_empty_mode_defaults_to_dry_run(self) -> None:
        self.assertIs(ControlMode.parse(None), ControlMode.DRY_RUN)
        self.assertIs(ControlMode.parse(""), ControlMode.DRY_RUN)

    def test_mode_is_case_insensitive(self) -> None:
        self.assertIs(ControlMode.parse("park_only"), ControlMode.PARK_ONLY)

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unbekannter CONTROL_MODE"):
            ControlMode.parse("LIVE")

    def test_permissions_are_strictly_staged(self) -> None:
        self.assertFalse(ControlMode.DRY_RUN.allows_park)
        self.assertTrue(ControlMode.PARK_ONLY.allows_park)
        self.assertFalse(ControlMode.PARK_ONLY.allows_start)
        self.assertTrue(ControlMode.FULL_MOWER.allows_start)
        self.assertFalse(ControlMode.FULL_MOWER.allows_irrigation_control)
        self.assertTrue(ControlMode.FULL_FAILSAFE.allows_irrigation_control)


class RuntimeSettingsTests(unittest.TestCase):
    def test_defaults_are_safe(self) -> None:
        settings = RuntimeSettings.from_mapping({})
        self.assertIs(settings.control_mode, ControlMode.DRY_RUN)
        self.assertEqual(settings.timer_schedule, "0 * * * * *")
        self.assertEqual(settings.timezone_name, "Europe/Berlin")


class ControlCycleTests(unittest.TestCase):
    def test_dry_run_never_sends_a_command(self) -> None:
        result = run_control_cycle(
            now_utc=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
            environment={"CONTROL_MODE": "DRY_RUN"},
            past_due=False,
        )
        self.assertEqual(result.decision_code, "HEARTBEAT_ONLY")
        self.assertFalse(result.command_sent)

    def test_off_mode_is_logged_without_command(self) -> None:
        result = run_control_cycle(
            now_utc=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
            environment={"CONTROL_MODE": "OFF"},
            past_due=True,
        )
        self.assertEqual(result.decision_code, "AUTOMATION_OFF")
        self.assertTrue(result.past_due)
        self.assertFalse(result.command_sent)

    def test_live_modes_are_blocked_in_phase_one(self) -> None:
        for mode in ("PARK_ONLY", "FULL_MOWER", "FULL_FAILSAFE"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(RuntimeError, "Heartbeat-Stadium"):
                    run_control_cycle(
                        now_utc=datetime(
                            2026,
                            8,
                            2,
                            12,
                            0,
                            tzinfo=timezone.utc,
                        ),
                        environment={"CONTROL_MODE": mode},
                        past_due=False,
                    )

    def test_naive_time_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "zeitzonenbewusste"):
            run_control_cycle(
                now_utc=datetime(2026, 8, 2, 12, 0),
                environment={"CONTROL_MODE": "DRY_RUN"},
                past_due=False,
            )


if __name__ == "__main__":
    unittest.main()
