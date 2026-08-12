from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from mower.dry_run import run_read_only_cycle
from mower.husqvarna_actions import park_until_further_notice
from mower.husqvarna_start_actions import start_in_work_area
from mower.hydrawise import (
    HydrawiseContinuousClearSnapshot,
    evaluate_continuous_clear_confirmation,
)
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.safety import CommandIntent, evaluate_command_gate
from mower.state import AutomationState
from mower.state_store import (
    AzureTableStateStore,
    StateConflictError,
    StateStore,
)


ReadOnlyRunner = Callable[..., CycleResult]
StateStoreFactory = Callable[[Mapping[str, str]], StateStore]
ParkSender = Callable[[str, str, str], dict[str, Any]]
StartSender = Callable[[str, str, str, int, int], dict[str, Any]]

SAFE_AUTOSTART_PARK_SOURCES = frozenset({"training", "match"})
PARKED_ACTIVITIES = frozenset({"PARKED_IN_CS", "CHARGING"})
PARKABLE_ACTIVITIES = frozenset({"MOWING", "LEAVING"})
MOWER_ERROR_STATES = frozenset(
    {
        "ERROR",
        "FATAL_ERROR",
        "ERROR_AT_POWER_UP",
        "WAIT_UPDATING",
        "WAIT_POWER_UP",
    }
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Zeitangaben müssen eine Zeitzone enthalten.")
    return parsed.astimezone(timezone.utc)


def _positive_int(
    environment: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(environment.get(name, default)).strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} muss eine ganze Zahl sein.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} muss zwischen {minimum} und {maximum} liegen."
        )
    return value


def _source_parts(source: Any) -> frozenset[str]:
    return frozenset(
        part.strip().lower()
        for part in str(source or "").split("+")
        if part.strip()
    )


def _restart_allowed_for_source(source: str) -> bool:
    parts = _source_parts(source)
    return bool(parts) and parts.issubset(SAFE_AUTOSTART_PARK_SOURCES)


def _state_details(
    state: AutomationState,
    *,
    persisted: bool,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "parked_by_automation": state.parked_by_automation,
        "automation_park_source": state.automation_park_source,
        "automation_restart_allowed": state.automation_restart_allowed,
        "park_command_sent_utc": state.park_command_sent_utc,
        "park_confirmed_utc": state.park_confirmed_utc,
        "hydrawise_clear_since_utc": state.hydrawise_clear_since_utc,
        "last_hydrawise_active_count": state.last_hydrawise_active_count,
        "persisted": persisted,
        "error": error,
    }


def _replace_safety(
    details: dict[str, Any],
    *,
    command_sent: bool,
    settings: RuntimeSettings,
) -> dict[str, Any]:
    updated = dict(details)
    safety = dict(_as_dict(updated.get("safety")))
    safety.update(
        {
            "read_only": not command_sent,
            "command_functions_present": True,
            "park_command_function_present": True,
            "start_command_functions_present": True,
            "park_gate_enabled": settings.enable_park_commands,
            "start_gate_enabled": settings.enable_start_commands,
            "full_mower_confirmation_valid": (
                settings.full_mower_write_gate_enabled
            ),
            "irrigation_command_functions_present": False,
            "command_sent": command_sent,
        }
    )
    updated["safety"] = safety
    return updated


def _next_irrigation_start(details: dict[str, Any]) -> datetime | None:
    current_plan = _as_dict(details.get("current_plan"))
    for name in ("blocked_now", "parking_block", "next_block"):
        block = _as_dict(current_plan.get(name))
        if "irrigation" not in _source_parts(block.get("source")):
            continue
        start = _parse_time(block.get("start"))
        if start is not None:
            return start
    return None


def _record_cycle_state(
    *,
    state: AutomationState,
    result: CycleResult,
    now_utc: datetime,
) -> AutomationState:
    details = _as_dict(result.details)
    mower = _as_dict(details.get("mower"))
    hydrawise = _as_dict(details.get("hydrawise"))
    hydrawise_safety = _as_dict(hydrawise.get("safety"))
    observed = _parse_time(hydrawise_safety.get("observed_at_utc"))
    hydrawise_fresh = bool(hydrawise_safety.get("fresh"))
    clear_now = bool(hydrawise_safety.get("clear_now"))
    active_count = int(hydrawise_safety.get("active_zone_count") or 0)
    current_plan = _as_dict(details.get("current_plan"))
    irrigation_block_active = any(
        "irrigation" in _source_parts(
            _as_dict(current_plan.get(name)).get("source")
        )
        for name in ("blocked_now", "parking_block")
    )

    return state.record_cycle(
        started_utc=now_utc,
        success=True,
        decision_code=result.decision_code,
        mower_activity=str(mower.get("activity") or "") or None,
        mower_state=str(mower.get("state") or "") or None,
        error_code=(
            int(mower.get("error_code"))
            if mower.get("error_code") is not None
            else None
        ),
        hydrawise_success_utc=(now_utc if hydrawise_fresh else None),
        hydrawise_observed_utc=observed,
        hydrawise_clear=(
            hydrawise_fresh
            and clear_now
            and not irrigation_block_active
        ),
        hydrawise_active_count=active_count,
        next_irrigation_start_utc=_next_irrigation_start(details),
    )


def _persist_cycle_only(
    *,
    store: StateStore,
    original: AutomationState,
    cycle_state: AutomationState,
    details: dict[str, Any],
    settings: RuntimeSettings,
    result: CycleResult,
    decision_code: str | None = None,
    message: str | None = None,
) -> CycleResult:
    try:
        store.save(cycle_state, expected_revision=original.revision)
        persisted, error = True, None
    except StateConflictError as exc:
        persisted, error = False, str(exc)
    details["automation_state"] = _state_details(
        cycle_state,
        persisted=persisted,
        error=error,
    )
    details["mode"] = "full_mower_capable_locked_or_live"
    details = _replace_safety(
        details,
        command_sent=False,
        settings=settings,
    )
    return replace(
        result,
        decision_code=decision_code or result.decision_code,
        message=message or result.message,
        command_sent=False,
        details=details,
    )


def _hydrawise_release_confirmation(
    *,
    cycle_state: AutomationState,
    details: dict[str, Any],
    now_utc: datetime,
    confirmation_minutes: int,
) -> HydrawiseContinuousClearSnapshot:
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    return evaluate_continuous_clear_confirmation(
        available=bool(safety.get("available")),
        fresh=bool(safety.get("fresh")),
        clear_now=bool(safety.get("clear_now")),
        physical_reason=str(
            safety.get("reason") or "Hydrawise ist nicht frei."
        ),
        clear_since_utc=cycle_state.hydrawise_clear_since_utc,
        now_utc=now_utc,
        required_clear_minutes=confirmation_minutes,
        persistent_state_available=True,
    )


def run_full_mower_cycle(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    environment: Mapping[str, str],
    past_due: bool,
    source: str,
    read_only_runner: ReadOnlyRunner = run_read_only_cycle,
    state_store_factory: StateStoreFactory = AzureTableStateStore.from_environment,
    park_sender: ParkSender = park_until_further_notice,
    start_sender: StartSender = start_in_work_area,
) -> CycleResult:
    """Parkt sicher und startet nur nach eigener Trainings-/Spielparkierung.

    Beregnung ist ein separates, fail-closed Sicherheitsgate. Dieses Modul
    enthält keinerlei Hydrawise-Schreibfunktion.
    """

    if settings.control_mode is not ControlMode.FULL_MOWER:
        raise RuntimeError(
            "run_full_mower_cycle darf nur in FULL_MOWER ausgeführt werden."
        )
    if not settings.enable_live_reads:
        raise RuntimeError("FULL_MOWER benötigt ENABLE_LIVE_READS=true.")

    result = read_only_runner(
        now_utc=now_utc,
        settings=settings,
        environment=environment,
        past_due=past_due,
        source=source,
    )
    details = dict(result.details)
    decision = _as_dict(details.get("decision"))
    current_plan = _as_dict(details.get("current_plan"))
    parking_block = _as_dict(current_plan.get("parking_block"))
    mower = _as_dict(details.get("mower"))
    mower_id = str(mower.get("mower_id") or "").strip()
    activity = str(mower.get("activity") or "").strip().upper()
    mower_state = str(mower.get("state") or "").strip().upper()
    error_code = int(mower.get("error_code") or 0)

    store = state_store_factory(environment)
    state = store.load()
    cycle_state = _record_cycle_state(
        state=state,
        result=result,
        now_utc=now_utc,
    )

    wants_park = str(decision.get("hypothetical_command") or "").upper() == "PARK"
    if wants_park:
        park_source = str(
            parking_block.get("source")
            or (
                "hydrawise_unconfirmed"
                if result.decision_code.startswith("HYDRAWISE_")
                else "unknown"
            )
        ).strip().lower()
        restart_allowed = _restart_allowed_for_source(park_source)
        block_end = _parse_time(parking_block.get("end"))

        if not settings.enable_park_commands:
            return _persist_cycle_only(
                store=store,
                original=state,
                cycle_state=cycle_state,
                details=details,
                settings=settings,
                result=result,
                decision_code="FULL_MOWER_PARK_LOCKED",
                message=(
                    f"{result.message} ENABLE_PARK_COMMANDS=false hält den "
                    "echten Parkbefehl gesperrt."
                ),
            )
        if not mower_id or error_code != 0 or mower_state in MOWER_ERROR_STATES:
            return _persist_cycle_only(
                store=store,
                original=state,
                cycle_state=cycle_state,
                details=details,
                settings=settings,
                result=result,
                decision_code="MOWER_NOT_SAFE_FOR_COMMAND",
                message="Mäherzustand ist für einen Parkbefehl nicht sicher.",
            )
        if activity not in PARKABLE_ACTIVITIES:
            return _persist_cycle_only(
                store=store,
                original=state,
                cycle_state=cycle_state,
                details=details,
                settings=settings,
                result=result,
                decision_code="PARK_STATE_UNCLEAR",
                message="Der Mäher ist nicht eindeutig im parkbaren Fahrzustand.",
            )

        intent = CommandIntent(
            action="PARK",
            target=mower_id,
            reason=(
                f"{park_source}|{parking_block.get('start', '')}|"
                f"{parking_block.get('end', '')}"
            ),
            valid_until_utc=block_end,
        )
        gate = evaluate_command_gate(
            state=state,
            intent=intent,
            now_utc=now_utc,
        )
        details["park_gate"] = {
            "allowed": gate.allowed,
            "code": gate.code,
            "reason": gate.reason,
            "source": park_source,
            "automatic_restart_allowed": restart_allowed,
            "fingerprint": intent.fingerprint,
        }
        if not gate.allowed:
            return _persist_cycle_only(
                store=store,
                original=state,
                cycle_state=cycle_state,
                details=details,
                settings=settings,
                result=result,
                decision_code=gate.code,
                message=gate.reason,
            )

        command_state = cycle_state.record_command(
            fingerprint=intent.fingerprint,
            sent_utc=now_utc,
            action="PARK",
            park_until_utc=block_end,
            park_source=park_source,
            restart_allowed=restart_allowed,
        )
        client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
        client_secret = str(
            environment.get("HUSQVARNA_CLIENT_SECRET", "")
        ).strip()
        # PARK ist idempotent und sicher. Die Startberechtigung wird deshalb
        # bewusst erst nach einer bestätigten API-Antwort gespeichert. Ein
        # Absturz oder eine verlorene Antwort kann so höchstens einen doppelten
        # Parkbefehl, aber niemals eine falsche Startberechtigung erzeugen.
        response = park_sender(client_id, client_secret, mower_id)
        try:
            store.save(command_state, expected_revision=state.revision)
            state_persisted, state_error = True, None
        except Exception as exc:
            state_persisted = False
            state_error = f"{type(exc).__name__}: {exc}"

        details["park_action"] = {
            "type": "ParkUntilFurtherNotice",
            "accepted": True,
            "response": response,
            "source": park_source,
            "automatic_restart_allowed": restart_allowed,
        }
        details["automation_state"] = _state_details(
            command_state if state_persisted else cycle_state,
            persisted=state_persisted,
            error=state_error,
        )
        details["mode"] = "full_mower_live"
        details = _replace_safety(
            details,
            command_sent=True,
            settings=settings,
        )
        return replace(
            result,
            decision_code=(
                "PARK_COMMAND_SENT"
                if state_persisted
                else "PARK_SENT_STATE_NOT_OWNED"
            ),
            command_sent=True,
            message=(
                (
                    "ParkUntilFurtherNotice wurde gesendet. Automatischer Neustart "
                    + (
                        "ist nach bestätigtem Ende für Training/Spiel zulässig."
                        if restart_allowed
                        else "bleibt für diese Parkursache gesperrt."
                    )
                )
                if state_persisted
                else (
                    "ParkUntilFurtherNotice wurde gesendet, aber die sichere "
                    "Zustandsübernahme scheiterte. Automatischer Neustart bleibt gesperrt."
                )
            ),
            details=details,
        )

    if not cycle_state.parked_by_automation:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
        )

    if (
        not cycle_state.automation_restart_allowed
        or not _restart_allowed_for_source(
            str(cycle_state.automation_park_source or "")
        )
    ):
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="AUTOSTART_SOURCE_FORBIDDEN",
            message=(
                "Die Automationsparkierung stammt nicht ausschließlich von "
                "Training oder Spiel; automatischer Start bleibt gesperrt."
            ),
        )

    if _as_dict(current_plan.get("blocked_now")) or _as_dict(
        current_plan.get("parking_block")
    ):
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="AUTOMATION_PARK_WAIT",
            message="Der Platz ist noch gesperrt oder die nächste Sperre steht unmittelbar an.",
        )

    confirmation_minutes = _positive_int(
        environment,
        "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES",
        10,
        minimum=1,
        maximum=60,
    )
    hydrawise_release = _hydrawise_release_confirmation(
        cycle_state=cycle_state,
        details=details,
        now_utc=now_utc,
        confirmation_minutes=confirmation_minutes,
    )
    details["hydrawise_release_gate"] = hydrawise_release.to_dict()
    if not hydrawise_release.allowed:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="HYDRAWISE_RELEASE_NOT_CONFIRMED",
            message=hydrawise_release.reason,
        )

    park_confirmed = _parse_time(cycle_state.park_confirmed_utc)
    park_confirmation_minutes = _positive_int(
        environment,
        "MOWER_PARK_CONFIRMATION_MINUTES",
        1,
        minimum=1,
        maximum=15,
    )
    if (
        park_confirmed is None
        or (
            now_utc.astimezone(timezone.utc) - park_confirmed
        ) < timedelta(minutes=park_confirmation_minutes)
        or activity not in PARKED_ACTIVITIES
        or error_code != 0
        or mower_state in MOWER_ERROR_STATES
    ):
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="MOWER_PARK_NOT_CONFIRMED",
            message="Die sichere Parkposition ist noch nicht lang genug bestätigt.",
        )

    battery = int(mower.get("battery_percent") or 0)
    if battery < 90:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="AUTOMATION_PARK_CHARGING",
            message=f"Der Akku liegt erst bei {battery} %; Start bleibt gesperrt.",
        )

    target_area = _as_dict(mower.get("target_work_area"))
    try:
        work_area_id = int(target_area.get("id"))
    except (TypeError, ValueError):
        work_area_id = 0
    if work_area_id <= 0 or target_area.get("enabled") is False:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="WORK_AREA_NOT_SAFE",
            message="Der Arbeitsbereich Rasenfläche ist nicht eindeutig startbereit.",
        )

    window = _as_dict(current_plan.get("mowing_window_now"))
    window_end = _parse_time(window.get("end"))
    if window_end is None:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="NO_VALID_WINDOW",
            message="Es gibt kein bestätigtes freies Mähfenster.",
        )

    minimum_remaining = 30
    try:
        minimum_remaining = int(
            environment.get("MINIMUM_MOWING_WINDOW_MINUTES", "30")
        )
    except ValueError as exc:
        raise RuntimeError(
            "MINIMUM_MOWING_WINDOW_MINUTES muss eine ganze Zahl sein."
        ) from exc
    max_duration = _positive_int(
        environment,
        "MAX_AUTOMATIC_START_MINUTES",
        360,
        minimum=30,
        maximum=720,
    )
    remaining = int(
        (window_end - now_utc.astimezone(timezone.utc)).total_seconds() // 60
    )
    duration = min(max_duration, max(0, remaining - 5))
    if remaining < minimum_remaining or duration < minimum_remaining:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="AUTOMATION_PARK_WINDOW_TOO_SHORT",
            message="Das verbleibende sichere Mähfenster ist zu kurz.",
        )

    if not settings.full_mower_write_gate_enabled:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code="FULL_MOWER_START_LOCKED",
            message=(
                "Start wäre sicher möglich, bleibt aber durch die unabhängigen "
                "Start- und Bestätigungs-Gates verriegelt."
            ),
        )

    intent = CommandIntent(
        action="START",
        target=mower_id,
        reason=(
            f"{cycle_state.automation_park_source}|{window_end.isoformat()}|"
            f"hydrawise-clear:{cycle_state.hydrawise_clear_since_utc}"
        ),
        valid_until_utc=window_end,
    )
    gate = evaluate_command_gate(
        state=cycle_state,
        intent=intent,
        now_utc=now_utc,
    )
    details["start_gate"] = {
        "allowed": gate.allowed,
        "code": gate.code,
        "reason": gate.reason,
        "duration_minutes": duration,
        "work_area_id": work_area_id,
        "fingerprint": intent.fingerprint,
    }
    if not gate.allowed:
        return _persist_cycle_only(
            store=store,
            original=state,
            cycle_state=cycle_state,
            details=details,
            settings=settings,
            result=result,
            decision_code=gate.code,
            message=gate.reason,
        )

    command_state = cycle_state.record_command(
        fingerprint=intent.fingerprint,
        sent_utc=now_utc,
        action="START",
    )
    try:
        store.save(command_state, expected_revision=state.revision)
    except Exception as exc:
        details["automation_state"] = _state_details(
            cycle_state,
            persisted=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        details = _replace_safety(
            details,
            command_sent=False,
            settings=settings,
        )
        return replace(
            result,
            decision_code="STATE_RESERVATION_FAILED",
            command_sent=False,
            message=(
                "Startbefehl wegen fehlender persistenter Reservierung "
                "nicht gesendet."
            ),
            details=details,
        )

    client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
    client_secret = str(
        environment.get("HUSQVARNA_CLIENT_SECRET", "")
    ).strip()
    response = start_sender(
        client_id,
        client_secret,
        mower_id,
        work_area_id,
        duration,
    )
    details["start_action"] = {
        "type": "StartInWorkArea",
        "accepted": True,
        "response": response,
        "duration_minutes": duration,
        "work_area_id": work_area_id,
        "park_source": state.automation_park_source,
        "hydrawise_release_confirmed": True,
    }
    details["automation_state"] = _state_details(
        command_state,
        persisted=True,
    )
    details["mode"] = "full_mower_live"
    details = _replace_safety(
        details,
        command_sent=True,
        settings=settings,
    )
    return replace(
        result,
        decision_code="START_COMMAND_SENT_AFTER_TRAINING_OR_MATCH",
        command_sent=True,
        message=(
            "Der Mäher wurde nach eigener Trainings-/Spielparkierung und "
            "bestätigter Hydrawise-Freigabe zeitlich begrenzt gestartet."
        ),
        details=details,
    )
