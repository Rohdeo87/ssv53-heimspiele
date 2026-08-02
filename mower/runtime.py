from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class ControlMode(str, Enum):
    """Schrittweise freischaltbare Betriebsarten der Platzpflege-Automatik."""

    OFF = "OFF"
    DRY_RUN = "DRY_RUN"
    PARK_ONLY = "PARK_ONLY"
    FULL_MOWER = "FULL_MOWER"
    FULL_FAILSAFE = "FULL_FAILSAFE"

    @classmethod
    def parse(cls, value: str | None) -> "ControlMode":
        normalized = (value or cls.DRY_RUN.value).strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in cls)
            raise ValueError(
                f"Unbekannter CONTROL_MODE {value!r}. Erlaubt sind: {allowed}."
            ) from exc

    @property
    def allows_park(self) -> bool:
        return self in {
            ControlMode.PARK_ONLY,
            ControlMode.FULL_MOWER,
            ControlMode.FULL_FAILSAFE,
        }

    @property
    def allows_start(self) -> bool:
        return self in {
            ControlMode.FULL_MOWER,
            ControlMode.FULL_FAILSAFE,
        }

    @property
    def allows_irrigation_control(self) -> bool:
        return self is ControlMode.FULL_FAILSAFE


@dataclass(frozen=True)
class RuntimeSettings:
    control_mode: ControlMode
    timer_schedule: str
    timezone_name: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "RuntimeSettings":
        timer_schedule = values.get("TIMER_SCHEDULE", "0 * * * * *").strip()
        timezone_name = values.get("SSV53_TIMEZONE", "Europe/Berlin").strip()
        if not timer_schedule:
            raise ValueError("TIMER_SCHEDULE darf nicht leer sein.")
        if not timezone_name:
            raise ValueError("SSV53_TIMEZONE darf nicht leer sein.")
        return cls(
            control_mode=ControlMode.parse(values.get("CONTROL_MODE")),
            timer_schedule=timer_schedule,
            timezone_name=timezone_name,
        )


@dataclass(frozen=True)
class CycleResult:
    schema_version: int
    executed_at_utc: str
    source: str
    control_mode: str
    past_due: bool
    decision_code: str
    command_sent: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_heartbeat_only_mode(mode: ControlMode) -> None:
    """Verhindert echte Steuerbefehle, solange nur das Azure-Gerüst aktiv ist."""

    if mode not in {ControlMode.OFF, ControlMode.DRY_RUN}:
        raise RuntimeError(
            "Die Azure-Vorbereitung ist noch im Heartbeat-Stadium. "
            f"CONTROL_MODE={mode.value} ist deshalb absichtlich gesperrt."
        )


def build_heartbeat_result(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    past_due: bool,
    source: str = "azure-timer",
) -> CycleResult:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss eine zeitzonenbewusste UTC-Zeit sein.")

    if settings.control_mode is ControlMode.OFF:
        decision_code = "AUTOMATION_OFF"
        message = "Azure-Timer lief; die Automatik ist deaktiviert."
    else:
        decision_code = "HEARTBEAT_ONLY"
        message = (
            "Azure-Timer lief im sicheren Dry Run. "
            "Es wurden keine externen APIs aufgerufen und keine Befehle gesendet."
        )

    return CycleResult(
        schema_version=1,
        executed_at_utc=now_utc.isoformat(),
        source=source,
        control_mode=settings.control_mode.value,
        past_due=bool(past_due),
        decision_code=decision_code,
        command_sent=False,
        message=message,
    )
