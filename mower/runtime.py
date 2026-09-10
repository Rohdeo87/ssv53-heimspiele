from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


FULL_MOWER_CONFIRMATION = "SSV53-TRAINING-MATCH-PARK-START"
FULL_FAILSAFE_CONFIRMATION = "SSV53-MOWER-HYDRAWISE-7-ZONES-150-MINUTES-ADAPTIVE-V1"
OPERATOR_CONTROL_CONFIRMATION = "SSV53-OPERATOR-PARK-HEIGHT-V1"


class ControlMode(str, Enum):
    """Schrittweise freischaltbare Betriebsarten der Platzpflege-Automatik."""

    OFF = "OFF"
    DRY_RUN = "DRY_RUN"
    OPERATOR_ONLY = "OPERATOR_ONLY"
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
            ControlMode.OPERATOR_ONLY,
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
    enable_irrigation_commands: bool
    full_mower_confirmation: str
    full_failsafe_confirmation: str
    park_lookahead_minutes: int
    # Kept optional at the end so direct construction by older readers stays
    # command-free until this opt-in mode is explicitly configured.
    enable_operator_cutting_height_commands: bool = False
    operator_control_confirmation: str = ""
    enable_operator_safety_guard: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "RuntimeSettings":
        control_mode = ControlMode.parse(values.get("CONTROL_MODE"))
        status_cache_mode = str(values.get("HYDRAWISE_STATUS_CACHE_MODE") or "OFF").strip().upper()
        if status_cache_mode not in {"OFF", "AZURE_TABLE"}:
            raise ValueError("HYDRAWISE_STATUS_CACHE_MODE muss OFF oder AZURE_TABLE sein.")
        if status_cache_mode != "OFF" and control_mode.allows_park:
            # The remaining device confirmation chains count independent
            # cycles; they must not count a reused read as new physical proof.
            raise ValueError("Der gemeinsame Statuscache ist bislang nur für befehlsfreie Betriebsarten geprüft.")
        timer_schedule = values.get("TIMER_SCHEDULE", "0 * * * * *").strip()
        timezone_name = values.get("SSV53_TIMEZONE", "Europe/Berlin").strip()
        if not timer_schedule:
            raise ValueError("TIMER_SCHEDULE darf nicht leer sein.")
        if not timezone_name:
            raise ValueError("SSV53_TIMEZONE darf nicht leer sein.")

        try:
            park_lookahead_minutes = int(
                values.get("PARK_LOOKAHEAD_MINUTES", "10").strip()
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
            control_mode=control_mode,
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
            enable_irrigation_commands=_parse_bool(
                values.get("ENABLE_IRRIGATION_COMMANDS"),
                default=False,
            ),
            enable_operator_cutting_height_commands=_parse_bool(
                values.get("ENABLE_OPERATOR_CUTTING_HEIGHT_COMMANDS"),
                default=False,
            ),
            full_mower_confirmation=str(
                values.get("FULL_MOWER_CONFIRMATION", "")
            ).strip(),
            full_failsafe_confirmation=str(
                values.get("FULL_FAILSAFE_CONFIRMATION", "")
            ).strip(),
            operator_control_confirmation=str(
                values.get("OPERATOR_CONTROL_CONFIRMATION", "")
            ).strip(),
            enable_operator_safety_guard=_parse_bool(
                values.get("ENABLE_OPERATOR_SAFETY_GUARD"),
                default=False,
            ),
            park_lookahead_minutes=park_lookahead_minutes,
        )

    @property
    def full_mower_write_gate_enabled(self) -> bool:
        return (
            self.enable_park_commands
            and self.enable_start_commands
            and self.full_mower_confirmation == FULL_MOWER_CONFIRMATION
        )

    @property
    def full_failsafe_write_gate_enabled(self) -> bool:
        return (
            self.full_mower_write_gate_enabled
            and self.enable_irrigation_commands
            and self.full_failsafe_confirmation == FULL_FAILSAFE_CONFIRMATION
        )

    @property
    def operator_control_gate_enabled(self) -> bool:
        return (
            self.control_mode is ControlMode.OPERATOR_ONLY
            and self.enable_live_reads
            and self.operator_control_confirmation
            == OPERATOR_CONTROL_CONFIRMATION
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
    """Kompatibilitätsprüfung; Schreibrechte werden separat gegatet."""

    if not isinstance(mode, ControlMode):
        raise TypeError("mode muss ein ControlMode sein.")


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
