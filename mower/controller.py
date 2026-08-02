from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Mapping

from mower.dry_run import run_read_only_cycle
from mower.runtime import (
    ControlMode,
    CycleResult,
    RuntimeSettings,
    build_heartbeat_result,
    ensure_heartbeat_only_mode,
)


LiveCycleRunner = Callable[..., CycleResult]


def run_control_cycle(
    *,
    now_utc: datetime,
    environment: Mapping[str, str],
    past_due: bool,
    source: str = "azure-timer",
    live_cycle_runner: LiveCycleRunner = run_read_only_cycle,
) -> CycleResult:
    """Führt genau einen sicheren Azure-Steuerungszyklus aus.

    Phase 2 kann optional Husqvarna, Hydrawise und den Platzplan live lesen.
    Echte Steuerbefehle bleiben technisch ausgeschlossen, weil weiterhin nur
    OFF und DRY_RUN zugelassen sind und das Lesemodul keine Aktionsfunktionen
    enthält.
    """

    settings = RuntimeSettings.from_mapping(environment)
    ensure_heartbeat_only_mode(settings.control_mode)

    if (
        settings.control_mode is ControlMode.OFF
        or not settings.enable_live_reads
    ):
        return build_heartbeat_result(
            now_utc=now_utc,
            settings=settings,
            past_due=past_due,
            source=source,
        )

    return live_cycle_runner(
        now_utc=now_utc,
        settings=settings,
        environment=environment,
        past_due=past_due,
        source=source,
    )
