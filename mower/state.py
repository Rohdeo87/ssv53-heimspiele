from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping


STATE_KEY = "ssv53-mower-control"


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _require_utc_iso(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} muss eine zeitzonenbewusste Zeit enthalten.")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class AutomationState:
    schema_version: int = 1
    key: str = STATE_KEY
    revision: int = 0
    last_cycle_started_utc: str | None = None
    last_success_utc: str | None = None
    last_decision_code: str | None = None
    last_mower_activity: str | None = None
    last_mower_state: str | None = None
    last_error_code: int | None = None
    last_hydrawise_success_utc: str | None = None
    next_irrigation_start_utc: str | None = None
    parked_by_automation: bool = False
    park_command_sent_utc: str | None = None
    park_confirmed_utc: str | None = None
    automation_park_until_utc: str | None = None
    last_start_command_utc: str | None = None
    last_command_fingerprint: str | None = None
    last_command_utc: str | None = None
    maintenance_mode: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unbekannte Zustandsversion.")
        if self.key != STATE_KEY:
            raise ValueError("Unerwarteter Zustands-Schlüssel.")
        if self.revision < 0:
            raise ValueError("revision darf nicht negativ sein.")
        for field_name in (
            "last_cycle_started_utc",
            "last_success_utc",
            "last_hydrawise_success_utc",
            "next_irrigation_start_utc",
            "park_command_sent_utc",
            "park_confirmed_utc",
            "automation_park_until_utc",
            "last_start_command_utc",
            "last_command_utc",
        ):
            _require_utc_iso(getattr(self, field_name), field_name)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AutomationState":
        return cls(
            schema_version=int(values.get("schema_version", 1)),
            key=str(values.get("key", STATE_KEY)),
            revision=int(values.get("revision", 0)),
            last_cycle_started_utc=_require_utc_iso(
                _normalize_optional_text(values.get("last_cycle_started_utc")),
                "last_cycle_started_utc",
            ),
            last_success_utc=_require_utc_iso(
                _normalize_optional_text(values.get("last_success_utc")),
                "last_success_utc",
            ),
            last_decision_code=_normalize_optional_text(
                values.get("last_decision_code")
            ),
            last_mower_activity=_normalize_optional_text(
                values.get("last_mower_activity")
            ),
            last_mower_state=_normalize_optional_text(
                values.get("last_mower_state")
            ),
            last_error_code=_normalize_optional_int(
                values.get("last_error_code")
            ),
            last_hydrawise_success_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("last_hydrawise_success_utc")
                ),
                "last_hydrawise_success_utc",
            ),
            next_irrigation_start_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("next_irrigation_start_utc")
                ),
                "next_irrigation_start_utc",
            ),
            parked_by_automation=bool(values.get("parked_by_automation", False)),
            park_command_sent_utc=_require_utc_iso(
                _normalize_optional_text(values.get("park_command_sent_utc")),
                "park_command_sent_utc",
            ),
            park_confirmed_utc=_require_utc_iso(
                _normalize_optional_text(values.get("park_confirmed_utc")),
                "park_confirmed_utc",
            ),
            automation_park_until_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("automation_park_until_utc")
                ),
                "automation_park_until_utc",
            ),
            last_start_command_utc=_require_utc_iso(
                _normalize_optional_text(values.get("last_start_command_utc")),
                "last_start_command_utc",
            ),
            last_command_fingerprint=_normalize_optional_text(
                values.get("last_command_fingerprint")
            ),
            last_command_utc=_require_utc_iso(
                _normalize_optional_text(values.get("last_command_utc")),
                "last_command_utc",
            ),
            maintenance_mode=bool(values.get("maintenance_mode", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def record_cycle(
        self,
        *,
        started_utc: datetime,
        success: bool,
        decision_code: str,
        mower_activity: str | None = None,
        mower_state: str | None = None,
        error_code: int | None = None,
        hydrawise_success_utc: datetime | None = None,
        next_irrigation_start_utc: datetime | None = None,
    ) -> "AutomationState":
        started = _as_utc(started_utc, "started_utc")
        hydrawise = (
            _as_utc(hydrawise_success_utc, "hydrawise_success_utc")
            if hydrawise_success_utc is not None
            else None
        )
        next_irrigation = (
            _as_utc(next_irrigation_start_utc, "next_irrigation_start_utc")
            if next_irrigation_start_utc is not None
            else None
        )
        return replace(
            self,
            revision=self.revision + 1,
            last_cycle_started_utc=started.isoformat(),
            last_success_utc=(
                started.isoformat() if success else self.last_success_utc
            ),
            last_decision_code=decision_code,
            last_mower_activity=mower_activity,
            last_mower_state=mower_state,
            last_error_code=error_code,
            last_hydrawise_success_utc=(
                hydrawise.isoformat()
                if hydrawise is not None
                else self.last_hydrawise_success_utc
            ),
            next_irrigation_start_utc=(
                next_irrigation.isoformat()
                if next_irrigation is not None
                else None
            ),
        )

    def record_command(
        self,
        *,
        fingerprint: str,
        sent_utc: datetime,
        action: str,
        park_until_utc: datetime | None = None,
    ) -> "AutomationState":
        sent = _as_utc(sent_utc, "sent_utc")
        normalized_action = action.strip().upper()
        if not fingerprint.strip():
            raise ValueError("fingerprint darf nicht leer sein.")
        if normalized_action not in {"PARK", "START"}:
            raise ValueError("action muss PARK oder START sein.")

        changes: dict[str, Any] = {
            "revision": self.revision + 1,
            "last_command_fingerprint": fingerprint.strip(),
            "last_command_utc": sent.isoformat(),
        }
        if normalized_action == "PARK":
            changes.update(
                parked_by_automation=True,
                park_command_sent_utc=sent.isoformat(),
                automation_park_until_utc=(
                    _as_utc(park_until_utc, "park_until_utc").isoformat()
                    if park_until_utc is not None
                    else None
                ),
            )
        else:
            changes.update(
                parked_by_automation=False,
                last_start_command_utc=sent.isoformat(),
                automation_park_until_utc=None,
            )
        return replace(self, **changes)


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} muss zeitzonenbewusst sein.")
    return value.astimezone(timezone.utc)
