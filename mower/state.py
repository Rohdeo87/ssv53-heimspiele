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
    last_hydrawise_observed_utc: str | None = None
    hydrawise_clear_since_utc: str | None = None
    last_hydrawise_active_count: int | None = None
    next_irrigation_start_utc: str | None = None
    parked_by_automation: bool = False
    automation_park_source: str | None = None
    automation_restart_allowed: bool = False
    park_command_sent_utc: str | None = None
    park_confirmed_utc: str | None = None
    automation_park_until_utc: str | None = None
    last_start_command_utc: str | None = None
    continuous_mowing_owned: bool = False
    continuous_mowing_work_area_id: int | None = None
    continuous_mowing_window_end_utc: str | None = None
    irrigation_phase: str | None = None
    irrigation_plan_id: str | None = None
    irrigation_plan_json: str | None = None
    irrigation_suspended_relay_ids_json: str | None = None
    irrigation_suspension_until_utc: str | None = None
    irrigation_suspension_completed_utc: str | None = None
    irrigation_completed_relay_ids_json: str | None = None
    irrigation_current_relay_id: int | None = None
    irrigation_zone_start_reserved_utc: str | None = None
    irrigation_zone_started_utc: str | None = None
    irrigation_zone_clear_since_utc: str | None = None
    irrigation_completed_utc: str | None = None
    irrigation_failed_reason: str | None = None
    irrigation_change_candidate_hash: str | None = None
    irrigation_change_candidate_since_utc: str | None = None
    irrigation_cancelled_without_run_utc: str | None = None
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
            "last_hydrawise_observed_utc",
            "hydrawise_clear_since_utc",
            "next_irrigation_start_utc",
            "park_command_sent_utc",
            "park_confirmed_utc",
            "automation_park_until_utc",
            "last_start_command_utc",
            "continuous_mowing_window_end_utc",
            "irrigation_zone_start_reserved_utc",
            "irrigation_suspension_until_utc",
            "irrigation_suspension_completed_utc",
            "irrigation_zone_started_utc",
            "irrigation_zone_clear_since_utc",
            "irrigation_completed_utc",
            "irrigation_change_candidate_since_utc",
            "irrigation_cancelled_without_run_utc",
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
            last_hydrawise_observed_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("last_hydrawise_observed_utc")
                ),
                "last_hydrawise_observed_utc",
            ),
            hydrawise_clear_since_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("hydrawise_clear_since_utc")
                ),
                "hydrawise_clear_since_utc",
            ),
            last_hydrawise_active_count=_normalize_optional_int(
                values.get("last_hydrawise_active_count")
            ),
            next_irrigation_start_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("next_irrigation_start_utc")
                ),
                "next_irrigation_start_utc",
            ),
            parked_by_automation=bool(values.get("parked_by_automation", False)),
            automation_park_source=_normalize_optional_text(
                values.get("automation_park_source")
            ),
            automation_restart_allowed=bool(
                values.get("automation_restart_allowed", False)
            ),
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
            continuous_mowing_owned=bool(
                values.get("continuous_mowing_owned", False)
            ),
            continuous_mowing_work_area_id=_normalize_optional_int(
                values.get("continuous_mowing_work_area_id")
            ),
            continuous_mowing_window_end_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("continuous_mowing_window_end_utc")
                ),
                "continuous_mowing_window_end_utc",
            ),
            irrigation_phase=_normalize_optional_text(
                values.get("irrigation_phase")
            ),
            irrigation_plan_id=_normalize_optional_text(
                values.get("irrigation_plan_id")
            ),
            irrigation_plan_json=_normalize_optional_text(
                values.get("irrigation_plan_json")
            ),
            irrigation_suspended_relay_ids_json=_normalize_optional_text(
                values.get("irrigation_suspended_relay_ids_json")
            ),
            irrigation_suspension_until_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("irrigation_suspension_until_utc")
                ),
                "irrigation_suspension_until_utc",
            ),
            irrigation_suspension_completed_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("irrigation_suspension_completed_utc")
                ),
                "irrigation_suspension_completed_utc",
            ),
            irrigation_completed_relay_ids_json=_normalize_optional_text(
                values.get("irrigation_completed_relay_ids_json")
            ),
            irrigation_current_relay_id=_normalize_optional_int(
                values.get("irrigation_current_relay_id")
            ),
            irrigation_zone_start_reserved_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("irrigation_zone_start_reserved_utc")
                ),
                "irrigation_zone_start_reserved_utc",
            ),
            irrigation_zone_started_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("irrigation_zone_started_utc")
                ),
                "irrigation_zone_started_utc",
            ),
            irrigation_zone_clear_since_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("irrigation_zone_clear_since_utc")
                ),
                "irrigation_zone_clear_since_utc",
            ),
            irrigation_completed_utc=_require_utc_iso(
                _normalize_optional_text(values.get("irrigation_completed_utc")),
                "irrigation_completed_utc",
            ),
            irrigation_failed_reason=_normalize_optional_text(
                values.get("irrigation_failed_reason")
            ),
            irrigation_change_candidate_hash=_normalize_optional_text(
                values.get("irrigation_change_candidate_hash")
            ),
            irrigation_change_candidate_since_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("irrigation_change_candidate_since_utc")
                ),
                "irrigation_change_candidate_since_utc",
            ),
            irrigation_cancelled_without_run_utc=_require_utc_iso(
                _normalize_optional_text(
                    values.get("irrigation_cancelled_without_run_utc")
                ),
                "irrigation_cancelled_without_run_utc",
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
        hydrawise_observed_utc: datetime | None = None,
        hydrawise_clear: bool | None = None,
        hydrawise_active_count: int | None = None,
        next_irrigation_start_utc: datetime | None = None,
        hydrawise_continuity_max_gap_seconds: int = 180,
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
        hydrawise_observed = (
            _as_utc(hydrawise_observed_utc, "hydrawise_observed_utc")
            if hydrawise_observed_utc is not None
            else None
        )
        if hydrawise_active_count is not None and hydrawise_active_count < 0:
            raise ValueError("hydrawise_active_count darf nicht negativ sein.")
        if not 60 <= hydrawise_continuity_max_gap_seconds <= 900:
            raise ValueError(
                "hydrawise_continuity_max_gap_seconds muss zwischen 60 und 900 liegen."
            )

        if hydrawise_clear is True:
            # Die Bestätigung beginnt mit dem tatsächlichen Abrufzyklus und
            # niemals rückdatiert mit dem Zeitstempel des API-Payloads. Eine
            # Lücke in den Kontrollzyklen unterbricht die Kette ebenfalls.
            previous_success = (
                datetime.fromisoformat(
                    self.last_hydrawise_success_utc.replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                if self.last_hydrawise_success_utc
                else None
            )
            continuity_preserved = (
                self.hydrawise_clear_since_utc is not None
                and previous_success is not None
                and 0
                <= (started - previous_success).total_seconds()
                <= hydrawise_continuity_max_gap_seconds
            )
            clear_since = (
                self.hydrawise_clear_since_utc
                if continuity_preserved
                else started.isoformat()
            )
        else:
            # Auch ein fehlender Abruf unterbricht die Bestätigungskette.
            clear_since = None

        parked_activity = str(mower_activity or "").strip().upper()
        park_confirmed = self.park_confirmed_utc
        if (
            self.parked_by_automation
            and park_confirmed is None
            and parked_activity in {"PARKED_IN_CS", "CHARGING"}
        ):
            park_confirmed = started.isoformat()
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
            last_hydrawise_observed_utc=(
                hydrawise_observed.isoformat()
                if hydrawise_observed is not None
                else self.last_hydrawise_observed_utc
            ),
            hydrawise_clear_since_utc=clear_since,
            last_hydrawise_active_count=hydrawise_active_count,
            next_irrigation_start_utc=(
                next_irrigation.isoformat()
                if next_irrigation is not None
                else None
            ),
            park_confirmed_utc=park_confirmed,
        )

    def record_command(
        self,
        *,
        fingerprint: str,
        sent_utc: datetime,
        action: str,
        park_until_utc: datetime | None = None,
        park_source: str = "unknown",
        restart_allowed: bool = False,
        work_area_id: int | None = None,
        mowing_window_end_utc: datetime | None = None,
        continuous_mowing: bool = False,
    ) -> "AutomationState":
        sent = _as_utc(sent_utc, "sent_utc")
        normalized_action = action.strip().upper()
        if not fingerprint.strip():
            raise ValueError("fingerprint darf nicht leer sein.")
        if normalized_action not in {"PARK", "START"}:
            raise ValueError("action muss PARK oder START sein.")
        normalized_source = park_source.strip().lower()
        if normalized_action == "PARK" and not normalized_source:
            raise ValueError("park_source darf bei PARK nicht leer sein.")

        changes: dict[str, Any] = {
            "revision": self.revision + 1,
            "last_command_fingerprint": fingerprint.strip(),
            "last_command_utc": sent.isoformat(),
        }
        if normalized_action == "PARK":
            changes.update(
                parked_by_automation=True,
                automation_park_source=normalized_source,
                automation_restart_allowed=bool(restart_allowed),
                park_command_sent_utc=sent.isoformat(),
                park_confirmed_utc=None,
                automation_park_until_utc=(
                    _as_utc(park_until_utc, "park_until_utc").isoformat()
                    if park_until_utc is not None
                    else None
                ),
                continuous_mowing_owned=False,
                continuous_mowing_work_area_id=None,
                continuous_mowing_window_end_utc=None,
            )
        else:
            if continuous_mowing and (work_area_id is None or int(work_area_id) <= 0):
                raise ValueError("work_area_id muss für kontinuierliches Mähen positiv sein.")
            changes.update(
                parked_by_automation=False,
                automation_park_source=None,
                automation_restart_allowed=False,
                last_start_command_utc=sent.isoformat(),
                park_command_sent_utc=None,
                park_confirmed_utc=None,
                automation_park_until_utc=None,
                continuous_mowing_owned=bool(continuous_mowing),
                continuous_mowing_work_area_id=(
                    int(work_area_id) if continuous_mowing else None
                ),
                continuous_mowing_window_end_utc=(
                    _as_utc(
                        mowing_window_end_utc,
                        "mowing_window_end_utc",
                    ).isoformat()
                    if continuous_mowing and mowing_window_end_utc is not None
                    else None
                ),
            )
        return replace(self, **changes)

    def record_failed_park(self) -> "AutomationState":
        """Gibt eine fehlgeschlagene PARK-Reservierung fail-closed frei.

        Ein eventuell doch angekommenes PARK darf erneut gesendet werden; das
        ist sicherer als eine fälschlich angenommene Automationsparkierung.
        """

        return replace(
            self,
            revision=self.revision + 1,
            parked_by_automation=False,
            automation_park_source=None,
            automation_restart_allowed=False,
            park_command_sent_utc=None,
            park_confirmed_utc=None,
            automation_park_until_utc=None,
            last_command_fingerprint=None,
            last_command_utc=None,
        )


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} muss zeitzonenbewusst sein.")
    return value.astimezone(timezone.utc)
