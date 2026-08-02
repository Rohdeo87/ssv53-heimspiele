from __future__ import annotations

import unittest
from datetime import datetime, timezone

from mower.controller import run_control_cycle
from mower.runtime import CycleResult, RuntimeSettings


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class RuntimeSettingsPhase2Tests(unittest.TestCase):
    def test_live_reads_default_to_off(self) -> None:
        settings = RuntimeSettings.from_mapping({})
        self.assertFalse(settings.enable_live_reads)
        self.assertEqual(settings.park_lookahead_minutes, 15)

    def test_invalid_live_read_value_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "boolescher"):
            RuntimeSettings.from_mapping(
                {"ENABLE_LIVE_READS": "vielleicht"}
            )


class ControllerPhase2Tests(unittest.TestCase):
    def test_live_runner_is_not_called_by_default(self) -> None:
        called = False

        def runner(**_kwargs):
            nonlocal called
            called = True
            raise AssertionError("darf nicht aufgerufen werden")

        result = run_control_cycle(
            now_utc=NOW,
            environment={"CONTROL_MODE": "DRY_RUN"},
            past_due=False,
            live_cycle_runner=runner,
        )
        self.assertFalse(called)
        self.assertEqual(result.decision_code, "HEARTBEAT_ONLY")

    def test_live_runner_is_called_only_after_explicit_opt_in(self) -> None:
        def runner(**kwargs):
            self.assertEqual(
                kwargs["settings"].control_mode.value,
                "DRY_RUN",
            )
            return CycleResult(
                schema_version=2,
                executed_at_utc=NOW.isoformat(),
                source=kwargs["source"],
                control_mode="DRY_RUN",
                past_due=False,
                decision_code="TEST_READ_ONLY",
                command_sent=False,
                message="ok",
            )

        result = run_control_cycle(
            now_utc=NOW,
            environment={
                "CONTROL_MODE": "DRY_RUN",
                "ENABLE_LIVE_READS": "true",
            },
            past_due=False,
            live_cycle_runner=runner,
        )
        self.assertEqual(result.decision_code, "TEST_READ_ONLY")
        self.assertFalse(result.command_sent)

    def test_live_control_modes_remain_blocked(self) -> None:
        for mode in ("PARK_ONLY", "FULL_MOWER", "FULL_FAILSAFE"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Heartbeat-Stadium",
                ):
                    run_control_cycle(
                        now_utc=NOW,
                        environment={
                            "CONTROL_MODE": mode,
                            "ENABLE_LIVE_READS": "true",
                        },
                        past_due=False,
                    )


if __name__ == "__main__":
    unittest.main()
