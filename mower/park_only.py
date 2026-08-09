from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping

from mower.dry_run import run_read_only_cycle
from mower.husqvarna_actions import park_until_further_notice
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.safety import CommandIntent, evaluate_command_gate
from mower.state_store import AzureTableStateStore, StateConflictError, StateStore


ReadOnlyRunner = Callable[..., CycleResult]
StateStoreFactory = Callable[[Mapping[str, str]], StateStore]
ParkSender = Callable[[str, str, str], dict[str, Any]]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Zeitangaben aus dem Mähplan müssen eine Zeitzone enthalten.")
    return parsed.astimezone(timezone.utc)


def _state_details(state: Any, *, persisted: bool, error: str | None = None) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "parked_by_automation": state.parked_by_automation,
        "park_command_sent_utc": state.park_command_sent_utc,
        "automation_park_until_utc": state.automation_park_until_utc,
        "persisted": persisted,
        "error": error,
    }


def _record_cycle_state(*, state: Any, result: CycleResult, now_utc: datetime) -> Any:
    details = _as_dict(result.details)
    mower = _as_dict(details.get("mower"))
    hydrawise = _as_dict(details.get("hydrawise"))
    current_plan = _as_dict(details.get("current_plan"))
    next_block = _as_dict(current_plan.get("next_block"))

    hydrawise_success = (
        now_utc
        if str(hydrawise.get("status", "")).casefold().startswith("live")
        else None
    )
    next_irrigation = None
    if str(next_block.get("source", "")).casefold() == "irrigation":
        next_irrigation = _parse_time(next_block.get("start"))

    return state.record_cycle(
        started_utc=now_utc,
        success=True,
        decision_code=result.decision_code,
        mower_activity=str(mower.get("activity") or "") or None,
        mower_state=str(mower.get("state") or "") or None,
        error_code=(int(mower.get("error_code")) if mower.get("error_code") is not None else None),
        hydrawise_success_utc=hydrawise_success,
        next_irrigation_start_utc=next_irrigation,
    )


def _replace_safety(details: dict[str, Any], *, command_sent: bool, park_gate_enabled: bool) -> dict[str, Any]:
    updated = dict(details)
    safety = dict(_as_dict(updated.get("safety")))
    safety.update(
        {
            "read_only": not command_sent,
            "command_functions_present": True,
            "park_command_function_present": True,
            "start_command_functions_present": False,
            "park_gate_enabled": park_gate_enabled,
            "command_sent": command_sent,
        }
    )
    updated["safety"] = safety
    return updated


def run_park_only_cycle(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    environment: Mapping[str, str],
    past_due: bool,
    source: str,
    read_only_runner: ReadOnlyRunner = run_read_only_cycle,
    state_store_factory: StateStoreFactory = AzureTableStateStore.from_environment,
    park_sender: ParkSender = park_until_further_notice,
) -> CycleResult:
    """Live-Zyklus mit genau einer erlaubten Schreibaktion: PARK."""

    if settings.control_mode is not ControlMode.PARK_ONLY:
        raise RuntimeError("run_park_only_cycle darf nur in PARK_ONLY ausgeführt werden.")
    if not settings.enable_live_reads:
        raise RuntimeError("PARK_ONLY benötigt ENABLE_LIVE_READS=true.")

    result = read_only_runner(
        now_utc=now_utc,
        settings=settings,
        environment=environment,
        past_due=past_due,
        source=source,
    )
    details = dict(result.details)

    if not settings.enable_park_commands:
        details["mode"] = "park_only_capable_locked"
        details = _replace_safety(details, command_sent=False, park_gate_enabled=False)
        return replace(
            result,
            command_sent=False,
            message=(
                f"{result.message} PARK_ONLY-Code ist vorhanden, aber "
                "ENABLE_PARK_COMMANDS=false hält echte Parkbefehle gesperrt."
            ),
            details=details,
        )

    store = state_store_factory(environment)
    state = store.load()
    cycle_state = _record_cycle_state(state=state, result=result, now_utc=now_utc)

    decision = _as_dict(details.get("decision"))
    mower = _as_dict(details.get("mower"))
    current_plan = _as_dict(details.get("current_plan"))
    parking_block = _as_dict(current_plan.get("parking_block"))

    if str(decision.get("hypothetical_command", "")).upper() != "PARK":
        try:
            store.save(cycle_state, expected_revision=state.revision)
            persisted, persist_error = True, None
        except StateConflictError as exc:
            persisted, persist_error = False, str(exc)
        details["mode"] = "park_only_live"
        details["automation_state"] = _state_details(cycle_state, persisted=persisted, error=persist_error)
        details = _replace_safety(details, command_sent=False, park_gate_enabled=True)
        return replace(result, details=details)

    mower_id = str(mower.get("mower_id") or "").strip()
    error_code = int(mower.get("error_code") or 0)
    mower_state = str(mower.get("state") or "").strip().upper()
    mower_activity = str(mower.get("activity") or "").strip().upper()
    if not mower_id or error_code != 0 or mower_state in {
        "ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP", "WAIT_UPDATING", "WAIT_POWER_UP"
    }:
        details["mode"] = "park_only_live"
        details["park_gate"] = {
            "allowed": False,
            "code": "MOWER_NOT_SAFE_FOR_COMMAND",
            "reason": f"Mäherzustand {mower_state}/{mower_activity}, Fehlercode {error_code}; kein Befehl.",
        }
        details = _replace_safety(details, command_sent=False, park_gate_enabled=True)
        return replace(result, decision_code="MOWER_NOT_SAFE_FOR_COMMAND", command_sent=False, details=details)

    block_end = _parse_time(parking_block.get("end"))
    block_start = str(parking_block.get("start") or "").strip()
    block_title = str(parking_block.get("title") or "Sperrfenster").strip()
    if block_end is None or block_end <= now_utc.astimezone(timezone.utc):
        details["mode"] = "park_only_live"
        details["park_gate"] = {
            "allowed": False,
            "code": "PARK_INTENT_INVALID",
            "reason": "Das Sperrfenster besitzt kein gültiges zukünftiges Ende.",
        }
        details = _replace_safety(details, command_sent=False, park_gate_enabled=True)
        return replace(result, decision_code="PARK_INTENT_INVALID", command_sent=False, details=details)

    intent = CommandIntent(
        action="PARK",
        target=mower_id,
        reason=f"{block_title}|{block_start}|{parking_block.get('end', '')}",
        valid_until_utc=block_end,
    )
    gate = evaluate_command_gate(state=state, intent=intent, now_utc=now_utc)
    details["park_gate"] = {
        "allowed": gate.allowed,
        "code": gate.code,
        "reason": gate.reason,
        "fingerprint": intent.fingerprint,
    }

    if not gate.allowed:
        try:
            store.save(cycle_state, expected_revision=state.revision)
            persisted, persist_error = True, None
        except StateConflictError as exc:
            persisted, persist_error = False, str(exc)
        details["mode"] = "park_only_live"
        details["automation_state"] = _state_details(cycle_state, persisted=persisted, error=persist_error)
        details = _replace_safety(details, command_sent=False, park_gate_enabled=True)
        return replace(result, decision_code=gate.code, command_sent=False, message=gate.reason, details=details)

    client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
    client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
    if not client_id or not client_secret:
        raise RuntimeError("PARK_ONLY ist aktiv, aber Husqvarna-Zugangsdaten fehlen.")

    # Fail-closed: Dedupe/Ownership wird vor dem externen Side-Effect
    # persistent reserviert. Scheitert die Persistenz, geht kein Befehl raus.
    command_state = cycle_state.record_command(
        fingerprint=intent.fingerprint,
        sent_utc=now_utc,
        action="PARK",
        park_until_utc=block_end,
    )
    try:
        store.save(command_state, expected_revision=state.revision)
    except Exception as exc:
        details["mode"] = "park_only_live"
        details["park_gate"] = {
            **details["park_gate"],
            "allowed": False,
            "code": "STATE_RESERVATION_FAILED",
            "reason": f"Zustandsreservierung fehlgeschlagen: {type(exc).__name__}: {exc}",
        }
        details["automation_state"] = _state_details(
            cycle_state,
            persisted=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        details = _replace_safety(
            details,
            command_sent=False,
            park_gate_enabled=True,
        )
        return replace(
            result,
            decision_code="STATE_RESERVATION_FAILED",
            command_sent=False,
            message="Parkbefehl wegen fehlender persistenter Dedupe-Reservierung nicht gesendet.",
            details=details,
        )

    # Erst nach erfolgreicher persistenter Reservierung wird genau ein PARK gesendet.
    action_response = park_sender(client_id, client_secret, mower_id)
    persisted, persist_error = True, None
    decision_code = "PARK_COMMAND_SENT"

    details["mode"] = "park_only_live"
    details["park_action"] = {
        "type": "ParkUntilFurtherNotice",
        "mower_id": mower_id,
        "accepted": True,
        "response": action_response,
        "automatic_start_possible": False,
    }
    details["automation_state"] = _state_details(command_state, persisted=persisted, error=persist_error)
    details = _replace_safety(details, command_sent=True, park_gate_enabled=True)
    return replace(
        result,
        decision_code=decision_code,
        command_sent=True,
        message=(
            f"ParkUntilFurtherNotice wurde für „{block_title}“ gesendet. "
            "Ein automatischer Start ist in PARK_ONLY technisch nicht vorhanden."
        ),
        details=details,
    )
