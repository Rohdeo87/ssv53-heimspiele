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
