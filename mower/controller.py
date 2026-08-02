from __future__ import annotations

from datetime import datetime
from typing import Mapping

from mower.runtime import (
    CycleResult,
    RuntimeSettings,
    build_heartbeat_result,
    ensure_heartbeat_only_mode,
)


def run_control_cycle(
    *,
    now_utc: datetime,
    environment: Mapping[str, str],
    past_due: bool,
    source: str = "azure-timer",
) -> CycleResult:
    """Führt genau einen sicheren Steuerungszyklus aus.

    In Phase 1 ist absichtlich nur der Azure-Heartbeat implementiert. Die
    Funktion bildet bereits den stabilen Einstiegspunkt, an den später die
    bestehende Planungs-, Husqvarna- und Hydrawise-Logik angeschlossen wird.
    """

    settings = RuntimeSettings.from_mapping(environment)
    ensure_heartbeat_only_mode(settings.control_mode)
    return build_heartbeat_result(
        now_utc=now_utc,
        settings=settings,
        past_due=past_due,
        source=source,
    )
