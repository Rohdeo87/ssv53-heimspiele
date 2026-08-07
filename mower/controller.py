from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from mower.config_source import resolve_runtime_inputs
from mower.dry_run import run_read_only_cycle
from mower.runtime import (
    ControlMode,
    CycleResult,
    RuntimeSettings,
    build_heartbeat_result,
    ensure_heartbeat_only_mode,
)


LiveCycleRunner = Callable[..., CycleResult]
RuntimeInputResolver = Callable[..., Any]


def _dynamic_config_enabled(environment: Mapping[str, str]) -> bool:
    return str(
        environment.get("SSV53_DYNAMIC_CONFIG_ENABLED", "false")
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _heartbeat_with_runtime_config_probe(
    *,
    result: CycleResult,
    environment: Mapping[str, str],
    now_utc: datetime,
    runtime_input_resolver: RuntimeInputResolver,
) -> CycleResult:
    """Validiert dynamische Laufzeitdaten ohne Gerätezugriff.

    Der Probe läuft ausschließlich im DRY_RUN bei deaktivierten Live Reads.
    Er verwendet dieselbe fail-closed Runtime-Config-Auflösung wie der spätere
    Read-only-Lauf, greift aber weder auf Husqvarna noch auf Hydrawise zu.
    """

    runtime_inputs = runtime_input_resolver(
        environment,
        now_utc=now_utc,
    )
    details = dict(result.details)
    details["runtime_config"] = {
        "probe": "config_only",
        "source_kind": runtime_inputs.source_kind,
        "manifest_etag": runtime_inputs.manifest_etag,
        "manifest_path": runtime_inputs.manifest_path,
        "published_at_utc": runtime_inputs.published_at_utc,
        "fallback_used": runtime_inputs.fallback_used,
    }
    return replace(result, details=details)


def run_control_cycle(
    *,
    now_utc: datetime,
    environment: Mapping[str, str],
    past_due: bool,
    source: str = "azure-timer",
    live_cycle_runner: LiveCycleRunner = run_read_only_cycle,
    runtime_input_resolver: RuntimeInputResolver = resolve_runtime_inputs,
) -> CycleResult:
    """Führt genau einen sicheren Azure-Steuerungszyklus aus.

    Phase 2 kann optional Husqvarna, Hydrawise und den Platzplan live lesen.
    Echte Steuerbefehle bleiben technisch ausgeschlossen, weil weiterhin nur
    OFF und DRY_RUN zugelassen sind und das Lesemodul keine Aktionsfunktionen
    enthält.

    Solange Live Reads deaktiviert sind, kann im DRY_RUN die dynamische
    Runtime-Config separat validiert werden. Dieser Config-only-Probe nutzt
    ausschließlich Blob Storage über Managed Identity und keinerlei
    Gerätezugänge.
    """

    settings = RuntimeSettings.from_mapping(environment)
    ensure_heartbeat_only_mode(settings.control_mode)

    if settings.control_mode is ControlMode.OFF:
        return build_heartbeat_result(
            now_utc=now_utc,
            settings=settings,
            past_due=past_due,
            source=source,
        )

    if not settings.enable_live_reads:
        result = build_heartbeat_result(
            now_utc=now_utc,
            settings=settings,
            past_due=past_due,
            source=source,
        )
        if _dynamic_config_enabled(environment):
            return _heartbeat_with_runtime_config_probe(
                result=result,
                environment=environment,
                now_utc=now_utc,
                runtime_input_resolver=runtime_input_resolver,
            )
        return result

    return live_cycle_runner(
        now_utc=now_utc,
        settings=settings,
        environment=environment,
        past_due=past_due,
        source=source,
    )
