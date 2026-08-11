from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


FULL_MOWER_CONFIRMATION = "SSV53-TRAINING-MATCH-PARK-START"


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


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Ungültiger boolescher Wert: {value!r}")


@dataclass(frozen=True)
class RuntimeSettings:
    control_mode: ControlMode
    timer_schedule: str
    timezone_name: str
    enable_live_reads: bool
    enable_park_commands: bool
    enable_start_commands: bool
    full_mower_confirmation: str
    park_lookahead_minutes: int

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "RuntimeSettings":
        timer_schedule = values.get("TIMER_SCHEDULE", "0 * * * * *").strip()
        timezone_name = values.get("SSV53_TIMEZONE", "Europe/Berlin").strip()
        if not timer_schedule:
            raise ValueError("TIMER_SCHEDULE darf nicht leer sein.")
        if not timezone_name:
            raise ValueError("SSV53_TIMEZONE darf nicht leer sein.")

        try:
            park_lookahead_minutes = int(
                values.get("PARK_LOOKAHEAD_MINUTES", "15").strip()
            )
        except ValueError as exc:
            raise ValueError(
                "PARK_LOOKAHEAD_MINUTES muss eine ganze Zahl sein."
            ) from exc
        if not 0 <= park_lookahead_minutes <= 120:
            raise ValueError(
                "PARK_LOOKAHEAD_MINUTES muss zwischen 0 und 120 liegen."
            )

        return cls(
            control_mode=ControlMode.parse(values.get("CONTROL_MODE")),
            timer_schedule=timer_schedule,
            timezone_name=timezone_name,
            enable_live_reads=_parse_bool(
                values.get("ENABLE_LIVE_READS"),
                default=False,
            ),
            enable_park_commands=_parse_bool(
                values.get("ENABLE_PARK_COMMANDS"),
                default=False,
            ),
            enable_start_commands=_parse_bool(
                values.get("ENABLE_START_COMMANDS"),
                default=False,
            ),
            full_mower_confirmation=str(
                values.get("FULL_MOWER_CONFIRMATION", "")
            ).strip(),
            park_lookahead_minutes=park_lookahead_minutes,
        )

    @property
    def full_mower_write_gate_enabled(self) -> bool:
        return (
            self.enable_park_commands
            and self.enable_start_commands
            and self.full_mower_confirmation == FULL_MOWER_CONFIRMATION
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
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_heartbeat_only_mode(mode: ControlMode) -> None:
    """Lässt höchstens PARK_ONLY zu; automatische Starts bleiben gesperrt."""

    if mode is ControlMode.FULL_FAILSAFE:
        raise RuntimeError(
            "Automatischer Start und Beregnungssteuerung sind noch gesperrt. "
            f"CONTROL_MODE={mode.value} ist deshalb nicht zulässig."
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
            "Live-Abfragen sind noch deaktiviert und es wurden keine Befehle gesendet."
        )

    return CycleResult(
        schema_version=2,
        executed_at_utc=now_utc.isoformat(),
        source=source,
        control_mode=settings.control_mode.value,
        past_due=bool(past_due),
        decision_code=decision_code,
        command_sent=False,
        message=message,
        details={
            "enable_live_reads": settings.enable_live_reads,
            "timer_schedule": settings.timer_schedule,
            "timezone": settings.timezone_name,
        },
    )
