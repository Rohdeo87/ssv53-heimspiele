from __future__ import annotations

import unittest
from datetime import datetime, timezone

from mower.config_source import RuntimeInputPaths
from mower.controller import run_control_cycle
from mower.runtime import CycleResult, RuntimeSettings


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class RuntimeSettingsPhase2Tests(unittest.TestCase):
    def test_live_reads_default_to_off(self) -> None:
        settings = RuntimeSettings.from_mapping({})
        self.assertFalse(settings.enable_live_reads)
        self.assertEqual(settings.park_lookahead_minutes, 10)

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

    def test_dynamic_config_probe_is_not_called_when_disabled(self) -> None:
        called = False

        def resolver(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("darf nicht aufgerufen werden")

        result = run_control_cycle(
            now_utc=NOW,
            environment={
                "CONTROL_MODE": "DRY_RUN",
                "ENABLE_LIVE_READS": "false",
                "SSV53_DYNAMIC_CONFIG_ENABLED": "false",
            },
            past_due=False,
            runtime_input_resolver=resolver,
        )

        self.assertFalse(called)
        self.assertEqual(result.decision_code, "HEARTBEAT_ONLY")
        self.assertNotIn("runtime_config", result.details)

    def test_dynamic_config_probe_runs_without_live_reads(self) -> None:
        live_called = False
        resolver_called = False

        def runner(**_kwargs):
            nonlocal live_called
            live_called = True
            raise AssertionError("Live Runner darf nicht aufgerufen werden")

        def resolver(environment, *, now_utc):
            nonlocal resolver_called
            resolver_called = True
            self.assertEqual(now_utc, NOW)
            self.assertEqual(
                environment["SSV53_DYNAMIC_CONFIG_ENABLED"],
                "true",
            )
            return RuntimeInputPaths(
                config_path="/tmp/ssv53-config/mower-config.json",
                matches_path="/tmp/ssv53-config/rasen.ics",
                source_kind="azure_blob",
                manifest_etag='"etag-1"',
                manifest_path="current/manifest.json",
                published_at_utc="2026-08-02T11:55:00+00:00",
                fallback_used=False,
            )

        result = run_control_cycle(
            now_utc=NOW,
            environment={
                "CONTROL_MODE": "DRY_RUN",
                "ENABLE_LIVE_READS": "false",
                "SSV53_DYNAMIC_CONFIG_ENABLED": "true",
            },
            past_due=False,
            live_cycle_runner=runner,
            runtime_input_resolver=resolver,
        )

        self.assertTrue(resolver_called)
        self.assertFalse(live_called)
        self.assertFalse(result.command_sent)
        self.assertEqual(result.decision_code, "HEARTBEAT_ONLY")
        self.assertEqual(
            result.details["runtime_config"]["source_kind"],
            "azure_blob",
        )
        self.assertEqual(
            result.details["runtime_config"]["manifest_path"],
            "current/manifest.json",
        )
        self.assertFalse(
            result.details["runtime_config"]["fallback_used"]
        )
        self.assertEqual(
            result.details["runtime_config"]["probe"],
            "config_only",
        )

    def test_dynamic_config_probe_fails_closed(self) -> None:
        def resolver(*_args, **_kwargs):
            raise RuntimeError(
                "Keine frische Laufzeitkonfiguration; fail-closed."
            )

        with self.assertRaisesRegex(RuntimeError, "fail-closed"):
            run_control_cycle(
                now_utc=NOW,
                environment={
                    "CONTROL_MODE": "DRY_RUN",
                    "ENABLE_LIVE_READS": "false",
                    "SSV53_DYNAMIC_CONFIG_ENABLED": "true",
                },
                past_due=False,
                runtime_input_resolver=resolver,
            )

    def test_off_mode_never_probes_dynamic_config(self) -> None:
        called = False

        def resolver(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("OFF darf keine Runtime-Config lesen")

        result = run_control_cycle(
            now_utc=NOW,
            environment={
                "CONTROL_MODE": "OFF",
                "ENABLE_LIVE_READS": "false",
                "SSV53_DYNAMIC_CONFIG_ENABLED": "true",
            },
            past_due=False,
            runtime_input_resolver=resolver,
        )

        self.assertFalse(called)
        self.assertEqual(result.decision_code, "AUTOMATION_OFF")

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

    def test_park_only_is_available_but_write_gate_defaults_locked(self) -> None:
        def park_runner(**kwargs):
            self.assertEqual(kwargs["settings"].control_mode.value, "PARK_ONLY")
            self.assertFalse(kwargs["settings"].enable_park_commands)
            return CycleResult(
                schema_version=2,
                executed_at_utc=NOW.isoformat(),
                source=kwargs["source"],
                control_mode="PARK_ONLY",
                past_due=False,
                decision_code="TEST_PARK_ONLY_LOCKED",
                command_sent=False,
                message="locked",
            )

        result = run_control_cycle(
            now_utc=NOW,
            environment={
                "CONTROL_MODE": "PARK_ONLY",
                "ENABLE_LIVE_READS": "true",
            },
            past_due=False,
            park_only_runner=park_runner,
        )
        self.assertEqual(result.decision_code, "TEST_PARK_ONLY_LOCKED")
        self.assertFalse(result.command_sent)

    def test_full_mower_runner_is_available_but_write_gates_default_locked(self) -> None:
        def full_runner(**kwargs):
            self.assertFalse(kwargs["settings"].enable_start_commands)
            self.assertFalse(
                kwargs["settings"].full_mower_write_gate_enabled
            )
            return CycleResult(
                schema_version=2,
                executed_at_utc=NOW.isoformat(),
                source=kwargs["source"],
                control_mode="FULL_MOWER",
                past_due=False,
                decision_code="TEST_FULL_MOWER_LOCKED",
                command_sent=False,
                message="locked",
            )

        result = run_control_cycle(
            now_utc=NOW,
            environment={
                "CONTROL_MODE": "FULL_MOWER",
                "ENABLE_LIVE_READS": "true",
            },
            past_due=False,
            full_mower_runner=full_runner,
        )
        self.assertEqual(result.decision_code, "TEST_FULL_MOWER_LOCKED")

    def test_irrigation_control_mode_uses_explicit_runner(self) -> None:
        def failsafe_runner(**kwargs):
            self.assertEqual(kwargs["settings"].control_mode.value, "FULL_FAILSAFE")
            return CycleResult(
                schema_version=2,
                executed_at_utc=NOW.isoformat(),
                source=kwargs["source"],
                control_mode="FULL_FAILSAFE",
                past_due=False,
                decision_code="TEST_FULL_FAILSAFE_LOCKED",
                command_sent=False,
                message="locked",
            )

        result = run_control_cycle(
            now_utc=NOW,
            environment={
                "CONTROL_MODE": "FULL_FAILSAFE",
                "ENABLE_LIVE_READS": "true",
            },
            past_due=False,
            full_failsafe_runner=failsafe_runner,
        )
        self.assertEqual(result.decision_code, "TEST_FULL_FAILSAFE_LOCKED")


if __name__ == "__main__":
    unittest.main()
