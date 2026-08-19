from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from mower.husqvarna import MowerSnapshot, fetch_mowers, parse_snapshot, select_mower
from mower.hydrawise import (
    HydrawiseSafetySnapshot,
    evaluate_safety_status,
    fetch_status,
    parse_relay_id_allowlist,
)
from mower.runtime import ControlMode, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, StateConflictError, StateStore


RESET_CONFIRMATION = "SSV53-RESET-FAILED-IRRIGATION"
PARKED_ACTIVITIES = frozenset({"PARKED_IN_CS", "CHARGING"})
ERROR_STATES = frozenset(
    {"ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP", "WAIT_UPDATING", "WAIT_POWER_UP"}
)

StateStoreFactory = Callable[[Mapping[str, str]], StateStore]
MowerFetcher = Callable[[str, str], list[dict[str, Any]]]
MowerSelector = Callable[[list[dict[str, Any]]], dict[str, Any]]
MowerParser = Callable[[dict[str, Any]], MowerSnapshot]
HydrawiseFetcher = Callable[[str, str | int | None], dict[str, Any]]


class IrrigationRecoveryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class IrrigationRecoveryResult:
    code: str
    message: str
    previous_revision: int
    revision: int
    reset_at_utc: str
    previous_failure_reason: str
    mower: dict[str, Any]
    hydrawise: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_utc(value: str | None, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise IrrigationRecoveryError(
            "RESET_PARK_CONFIRMATION_MISSING",
            f"{field_name} fehlt oder ist ungültig.",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IrrigationRecoveryError(
            "RESET_PARK_CONFIRMATION_MISSING",
            f"{field_name} muss eine Zeitzone enthalten.",
        )
    return parsed.astimezone(timezone.utc)


def _reset_state(
    state: AutomationState,
    *,
    now_utc: datetime,
    hydrawise_observed_utc: str | None,
) -> AutomationState:
    return replace(
        state,
        revision=state.revision + 1,
        last_decision_code="IRRIGATION_FAILED_RESET",
        last_hydrawise_success_utc=now_utc.isoformat(),
        last_hydrawise_observed_utc=hydrawise_observed_utc,
        hydrawise_clear_since_utc=now_utc.isoformat(),
        last_hydrawise_active_count=0,
        next_irrigation_start_utc=None,
        irrigation_phase=None,
        irrigation_plan_id=None,
        irrigation_plan_json=None,
        irrigation_suspended_relay_ids_json=None,
        irrigation_suspension_completed_utc=None,
        irrigation_completed_relay_ids_json=None,
        irrigation_current_relay_id=None,
        irrigation_zone_start_reserved_utc=None,
        irrigation_zone_started_utc=None,
        irrigation_zone_clear_since_utc=None,
        irrigation_completed_utc=None,
        irrigation_failed_reason=None,
    )


def reset_failed_irrigation(
    *,
    now_utc: datetime,
    environment: Mapping[str, str],
    expected_revision: int,
    confirmation: str,
    state_store_factory: StateStoreFactory = AzureTableStateStore.from_environment,
    mower_fetcher: MowerFetcher = fetch_mowers,
    mower_selector: MowerSelector = select_mower,
    mower_parser: MowerParser = parse_snapshot,
    hydrawise_fetcher: HydrawiseFetcher = fetch_status,
) -> IrrigationRecoveryResult:
    """Setzt ausschließlich einen sicher geprüften FAILED-Zustand zurück.

    Diese Funktion sendet weder Husqvarna- noch Hydrawise-Befehle. Der Reset
    startet die konfigurierte Freigabekette neu und lässt den Mäher geparkt.
    """

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    now = now_utc.astimezone(timezone.utc)
    if confirmation != RESET_CONFIRMATION:
        raise IrrigationRecoveryError(
            "RESET_CONFIRMATION_INVALID",
            "Die exakte manuelle Reset-Bestätigung fehlt.",
            status_code=400,
        )
    if int(expected_revision) <= 0:
        raise IrrigationRecoveryError(
            "RESET_REVISION_INVALID",
            "expected_revision muss positiv sein.",
            status_code=400,
        )

    settings = RuntimeSettings.from_mapping(environment)
    if (
        settings.control_mode is not ControlMode.FULL_FAILSAFE
        or not settings.enable_live_reads
        or not settings.full_failsafe_write_gate_enabled
    ):
        raise IrrigationRecoveryError(
            "RESET_RUNTIME_LOCKED",
            "Der Reset ist nur im vollständig bestätigten FULL_FAILSAFE-Betrieb möglich.",
        )

    store = state_store_factory(environment)
    state = store.load()
    if state.revision != int(expected_revision):
        raise IrrigationRecoveryError(
            "RESET_REVISION_CONFLICT",
            f"Zustandsrevision ist {state.revision}, erwartet wurde {expected_revision}.",
        )
    if state.irrigation_phase != "FAILED" or not state.irrigation_failed_reason:
        raise IrrigationRecoveryError(
            "RESET_NOT_FAILED",
            "Es liegt kein gespeicherter Beregnungsfehler vor.",
        )
    park_sources = {
        part.strip().lower()
        for part in str(state.automation_park_source or "").split("+")
        if part.strip()
    }
    if not state.parked_by_automation or "irrigation" not in park_sources:
        raise IrrigationRecoveryError(
            "RESET_AUTOMATION_PARK_MISSING",
            "Der Mäher besitzt keinen bestätigbaren Automationspark aus der Beregnung.",
        )
    park_confirmed = _parse_utc(state.park_confirmed_utc, "park_confirmed_utc")
    confirmation_minutes = int(environment.get("MOWER_PARK_CONFIRMATION_MINUTES", "1"))
    if not 1 <= confirmation_minutes <= 15:
        raise IrrigationRecoveryError(
            "RESET_CONFIGURATION_INVALID",
            "MOWER_PARK_CONFIRMATION_MINUTES ist ungültig.",
            status_code=500,
        )
    if now - park_confirmed < timedelta(minutes=confirmation_minutes):
        raise IrrigationRecoveryError(
            "RESET_PARK_NOT_STABLE",
            "Der Dockzustand ist noch nicht lange genug bestätigt.",
        )

    client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
    client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
    snapshot = mower_parser(mower_selector(mower_fetcher(client_id, client_secret)))
    if (
        not snapshot.mower_id
        or snapshot.activity not in PARKED_ACTIVITIES
        or snapshot.error_code != 0
        or snapshot.state in ERROR_STATES
    ):
        raise IrrigationRecoveryError(
            "RESET_MOWER_NOT_SAFE",
            "Husqvarna bestätigt keinen fehlerfreien Mäher im Dock.",
        )

    expected_count = int(environment.get("HYDRAWISE_EXPECTED_ZONE_COUNT", "7"))
    expected_relay_ids = parse_relay_id_allowlist(
        environment.get("HYDRAWISE_EXPECTED_RELAY_IDS"),
        expected_count=expected_count,
        required=True,
    )
    api_key = str(environment.get("HYDRAWISE_API_KEY", "")).strip()
    controller_id = str(environment.get("HYDRAWISE_CONTROLLER_ID", "")).strip() or None
    status = hydrawise_fetcher(api_key, controller_id)
    reset_horizon_minutes = max(
        90,
        int(environment.get("HYDRAWISE_CLEAR_CONFIRMATION_MINUTES", "120")),
    )
    safety: HydrawiseSafetySnapshot = evaluate_safety_status(
        status,
        {
            "enabled": True,
            "include_all_zones": True,
            "before_minutes": reset_horizon_minutes,
            "expected_relay_ids": list(expected_relay_ids),
        },
        now_utc=now,
        max_age_seconds=int(environment.get("HYDRAWISE_STATUS_MAX_AGE_SECONDS", "180")),
    )
    if (
        not safety.available
        or not safety.fresh
        or not safety.relay_set_valid
        or safety.selected_zone_count != expected_count
        or not safety.clear_now
        or safety.active_zone_count != 0
        or safety.imminent_zone_count != 0
    ):
        raise IrrigationRecoveryError(
            "RESET_HYDRAWISE_NOT_SAFE",
            "Hydrawise bestätigt nicht alle sieben freigegebenen Zonen als frei: "
            f"{safety.reason}",
        )

    previous_reason = state.irrigation_failed_reason
    reset = _reset_state(
        state,
        now_utc=now,
        hydrawise_observed_utc=safety.observed_at_utc,
    )
    try:
        store.save(reset, expected_revision=state.revision)
    except StateConflictError as exc:
        raise IrrigationRecoveryError(
            "RESET_REVISION_CONFLICT",
            "Der Zustand wurde während der Reset-Prüfung parallel verändert.",
        ) from exc

    return IrrigationRecoveryResult(
        code="IRRIGATION_FAILED_RESET",
        message=(
            "Beregnungsfehler kontrolliert zurückgesetzt; der Mäher bleibt geparkt "
            "und die konfigurierte Freigabekette beginnt neu."
        ),
        previous_revision=state.revision,
        revision=reset.revision,
        reset_at_utc=now.isoformat(),
        previous_failure_reason=previous_reason,
        mower={
            "mower_id": snapshot.mower_id,
            "activity": snapshot.activity,
            "state": snapshot.state,
            "error_code": snapshot.error_code,
            "battery_percent": snapshot.battery_percent,
        },
        hydrawise={
            "observed_at_utc": safety.observed_at_utc,
            "relay_set_valid": safety.relay_set_valid,
            "observed_relay_ids": list(safety.observed_relay_ids),
            "active_zone_count": safety.active_zone_count,
            "imminent_zone_count": safety.imminent_zone_count,
            "reset_horizon_minutes": reset_horizon_minutes,
        },
    )
