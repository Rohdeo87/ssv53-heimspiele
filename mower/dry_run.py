from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from mower.config_source import resolve_runtime_inputs
from mower.decision import (
    AUTOMATION_EXTERNAL_REASON,
    PARKABLE_ACTIVITIES,
    Decision,
    classify_decision,
    current_context,
    next_block_after,
    parking_block_for,
)
from mower.husqvarna import (
    HusqvarnaError,
    fetch_mowers,
    parse_snapshot,
    select_mower,
)
from mower.hydrawise import (
    HydrawiseError,
    HydrawiseContinuousClearSnapshot,
    evaluate_continuous_clear_confirmation,
    evaluate_safety_status,
    parse_relay_id_allowlist,
    fetch_status,
    selected_zone_observations,
    selected_zone_schedule,
)
from mower.planner import create_plan, load_json, read_match_blocks
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.state_store import AzureTableStateStore, StateStore
from training_cancellations import AzureTableCancellationStore


StateStoreFactory = Callable[[Mapping[str, str]], StateStore]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _block_to_dict(block: Any | None) -> dict[str, Any] | None:
    if block is None:
        return None
    return {
        "start": block.start.isoformat(),
        "end": block.end.isoformat(),
        "title": block.title,
        "source": block.source,
    }


def _window_to_dict(
    window: Any | None,
    now: datetime,
) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "remaining_minutes": int(
            (window.end - now).total_seconds() // 60
        ),
    }


def _target_work_area(
    work_areas: tuple[dict[str, Any], ...],
) -> dict[str, Any] | None:
    named = next(
        (
            area
            for area in work_areas
            if str(area.get("name", "")).casefold() == "rasenfläche"
        ),
        None,
    )
    if named is not None:
        return named
    return work_areas[0] if len(work_areas) == 1 else None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Zeitangaben müssen eine Zeitzone enthalten.")
    return parsed.astimezone(timezone.utc)


def _apply_hydrawise_gate(
    *,
    decision: Decision,
    hydrawise_clear: bool,
    hydrawise_reason: str,
    parking_block: Any | None,
    mower_activity: str,
    automation_owned_park: bool,
) -> Decision:
    if hydrawise_clear or parking_block is not None:
        return decision
    if mower_activity in PARKABLE_ACTIVITIES:
        return Decision(
            code="HYDRAWISE_UNCONFIRMED_WOULD_PARK",
            title="Beregnungssicherheit nicht bestätigt",
            reason=(
                f"{hydrawise_reason} Der laufende Mäher muss vorsorglich "
                "geparkt bleiben, bis Hydrawise eindeutig frei meldet."
            ),
            hypothetical_command="PARK",
        )
    if (
        decision.hypothetical_command == "START_IN_WORK_AREA"
        or automation_owned_park
    ):
        return Decision(
            code="HYDRAWISE_UNCONFIRMED_HOLD",
            title="Automatischen Start gesperrt halten",
            reason=(
                f"{hydrawise_reason} Ohne bestätigtes Beregnungsende darf der "
                "Mäher nicht auf den Platz fahren."
            ),
        )
    return decision


def run_read_only_cycle(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    environment: Mapping[str, str],
    past_due: bool,
    source: str,
    state_store_factory: StateStoreFactory = AzureTableStateStore.from_environment,
    cancellation_store_factory=AzureTableCancellationStore.from_environment,
) -> CycleResult:
    """Führt die komplette Live-Abfrage aus, sendet aber keinerlei Befehle."""

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss eine zeitzonenbewusste UTC-Zeit sein.")

    client_id = environment.get("HUSQVARNA_CLIENT_ID", "").strip()
    client_secret = environment.get(
        "HUSQVARNA_CLIENT_SECRET",
        "",
    ).strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "ENABLE_LIVE_READS ist aktiv, aber die Husqvarna-Zugangsdaten fehlen."
        )

    tz = ZoneInfo(settings.timezone_name)
    now_local = now_utc.astimezone(tz)
    runtime_inputs = resolve_runtime_inputs(
        environment,
        now_utc=now_utc,
    )
    config_path = runtime_inputs.config_path
    matches_path = runtime_inputs.matches_path

    config = load_json(config_path)
    planning = _as_dict(config.get("planning"))
    hydrawise_config = _as_dict(config.get("hydrawise"))
    expected_zone_count = int(
        environment.get(
            "HYDRAWISE_EXPECTED_ZONE_COUNT",
            hydrawise_config.get("expected_zone_count", 7),
        )
    )
    expected_relay_ids = parse_relay_id_allowlist(
        environment.get("HYDRAWISE_EXPECTED_RELAY_IDS"),
        expected_count=expected_zone_count,
        required=settings.control_mode is ControlMode.FULL_FAILSAFE,
    )
    hydrawise_config = {
        **hydrawise_config,
        "expected_relay_ids": list(expected_relay_ids),
    }
    minimum_remaining = int(
        planning.get("minimum_mowing_window_minutes", 30)
    )

    hydrawise_status: dict[str, Any] | None = None
    hydrawise_label = "nicht verbunden"
    hydrawise_error: str | None = None
    hydrawise_key = environment.get("HYDRAWISE_API_KEY", "").strip()
    if hydrawise_key:
        try:
            hydrawise_status = fetch_status(
                hydrawise_key,
                environment.get(
                    "HYDRAWISE_CONTROLLER_ID",
                    "",
                ).strip()
                or None,
            )
            hydrawise_label = (
                f"live ({len(hydrawise_status.get('relays', []))} Zonen)"
            )
        except HydrawiseError as exc:
            hydrawise_label = "Abruf fehlgeschlagen"
            hydrawise_error = str(exc)

    hydrawise_safety = evaluate_safety_status(
        hydrawise_status,
        hydrawise_config,
        now_utc=now_utc,
        max_age_seconds=int(
            environment.get("HYDRAWISE_STATUS_MAX_AGE_SECONDS", "180")
        ),
    )
    hydrawise_zones = selected_zone_schedule(
        hydrawise_status,
        hydrawise_config,
    )
    hydrawise_zone_observations = selected_zone_observations(
        hydrawise_status,
        hydrawise_config,
    )

    cancellation_error: str | None = None
    effective_cancellations: set[tuple[str, str]] = set()
    try:
        cancellation_store = cancellation_store_factory(environment)
        cancellations = cancellation_store.list_active(
            now_local.date(),
            now_local.date() + timedelta(days=2),
        )
        effective_cancellations = {
            item.occurrence_key
            for item in cancellations
            if item.is_effective(now_utc)
        }
    except Exception as exc:
        # Fail closed: Ohne verlässliche Absagen bleiben alle Trainings gesperrt.
        cancellation_error = f"{type(exc).__name__}: {exc}"

    match_blocks = read_match_blocks(matches_path, tz)
    plans, _merged = create_plan(
        config,
        match_blocks,
        hydrawise_status,
        now_local.date(),
        2,
        effective_cancellations,
    )
    active_block, active_window = current_context(plans, now_local)
    next_block = next_block_after(plans, now_local)
    parking_block = parking_block_for(
        active_block=active_block,
        next_block=next_block,
        now=now_local,
        lookahead_minutes=settings.park_lookahead_minutes,
    )

    try:
        mower_items = fetch_mowers(client_id, client_secret)
        mower_item = select_mower(mower_items)
        snapshot = parse_snapshot(mower_item)
    except HusqvarnaError as exc:
        raise RuntimeError(str(exc)) from exc

    automation_owned_park = (
        snapshot.external_reason_id == AUTOMATION_EXTERNAL_REASON
        and snapshot.override_action == "FORCE_PARK"
    )
    base_decision = classify_decision(
        now=now_local,
        active_block=active_block,
        parking_block=parking_block,
        active_window=active_window,
        activity=snapshot.activity,
        state=snapshot.state,
        error_code=snapshot.error_code,
        override_action=snapshot.override_action,
        automation_owned_park=automation_owned_park,
        battery=snapshot.battery_percent,
        minimum_remaining_minutes=minimum_remaining,
    )

    release_confirmation: HydrawiseContinuousClearSnapshot | None = None
    automation_state_details: dict[str, Any] | None = None
    if settings.control_mode is ControlMode.DRY_RUN:
        # Der verriegelte Dry Run speichert ausschließlich die binäre
        # Hydrawise-Freigabekette. So bleibt die Nachlaufsperre auch sichtbar,
        # nachdem eine beendete Zone aus der Live-Antwort verschwunden ist.
        required_clear_minutes = int(
            environment.get("HYDRAWISE_CLEAR_CONFIRMATION_MINUTES", "10")
        )
        projected_state = None
        state_error: str | None = None
        try:
            store = state_store_factory(environment)
            original_state = store.load()
            projected_state = original_state.record_cycle(
                started_utc=now_utc,
                success=True,
                decision_code=base_decision.code,
                mower_activity=snapshot.activity,
                mower_state=snapshot.state,
                error_code=snapshot.error_code,
                hydrawise_success_utc=(
                    now_utc if hydrawise_safety.fresh else None
                ),
                hydrawise_observed_utc=_parse_utc(
                    hydrawise_safety.observed_at_utc
                ),
                hydrawise_clear=(
                    hydrawise_safety.available
                    and hydrawise_safety.fresh
                    and hydrawise_safety.clear_now
                ),
                hydrawise_active_count=hydrawise_safety.active_zone_count,
            )
            release_confirmation = evaluate_continuous_clear_confirmation(
                available=hydrawise_safety.available,
                fresh=hydrawise_safety.fresh,
                clear_now=hydrawise_safety.clear_now,
                physical_reason=hydrawise_safety.reason,
                clear_since_utc=projected_state.hydrawise_clear_since_utc,
                now_utc=now_utc,
                required_clear_minutes=required_clear_minutes,
                persistent_state_available=True,
            )
            decision = _apply_hydrawise_gate(
                decision=base_decision,
                hydrawise_clear=release_confirmation.allowed,
                hydrawise_reason=release_confirmation.reason,
                parking_block=parking_block,
                mower_activity=snapshot.activity,
                automation_owned_park=automation_owned_park,
            )
            projected_state = replace(
                projected_state,
                last_decision_code=decision.code,
            )
            store.save(
                projected_state,
                expected_revision=original_state.revision,
            )
            state_persisted = True
        except Exception as exc:
            state_persisted = False
            state_error = f"{type(exc).__name__}: {exc}"
            release_confirmation = evaluate_continuous_clear_confirmation(
                available=hydrawise_safety.available,
                fresh=hydrawise_safety.fresh,
                clear_now=hydrawise_safety.clear_now,
                physical_reason=hydrawise_safety.reason,
                clear_since_utc=(
                    projected_state.hydrawise_clear_since_utc
                    if projected_state is not None
                    else None
                ),
                now_utc=now_utc,
                required_clear_minutes=required_clear_minutes,
                persistent_state_available=False,
            )
            decision = _apply_hydrawise_gate(
                decision=base_decision,
                hydrawise_clear=False,
                hydrawise_reason=release_confirmation.reason,
                parking_block=parking_block,
                mower_activity=snapshot.activity,
                automation_owned_park=automation_owned_park,
            )
        automation_state_details = {
            "revision": (
                projected_state.revision
                if projected_state is not None
                else None
            ),
            "hydrawise_clear_since_utc": (
                projected_state.hydrawise_clear_since_utc
                if projected_state is not None
                else None
            ),
            "persisted": state_persisted,
            "error": state_error,
        }
    else:
        decision = _apply_hydrawise_gate(
            decision=base_decision,
            hydrawise_clear=hydrawise_safety.clear_now,
            hydrawise_reason=hydrawise_safety.reason,
            parking_block=parking_block,
            mower_activity=snapshot.activity,
            automation_owned_park=automation_owned_park,
        )

    mower_details = snapshot.to_dict()
    mower_details["automation_owned_park"] = automation_owned_park
    mower_details["target_work_area"] = _target_work_area(
        snapshot.work_areas
    )

    return CycleResult(
        schema_version=2,
        executed_at_utc=now_utc.astimezone(timezone.utc).isoformat(),
        source=source,
        control_mode=settings.control_mode.value,
        past_due=bool(past_due),
        decision_code=decision.code,
        command_sent=False,
        message=decision.reason,
        details={
            "mode": "read_only_live_dry_run",
            "decision": asdict(decision),
            "current_plan": {
                "blocked_now": _block_to_dict(active_block),
                "mowing_window_now": _window_to_dict(
                    active_window,
                    now_local,
                ),
                "next_block": _block_to_dict(next_block),
                "parking_block": _block_to_dict(parking_block),
                "parking_lookahead_minutes": (
                    settings.park_lookahead_minutes
                ),
            },
            "hydrawise": {
                "status": hydrawise_label,
                "error": hydrawise_error,
                "safety": hydrawise_safety.to_dict(),
                "zones": hydrawise_zones,
                "zone_observations": hydrawise_zone_observations,
                "release_confirmation": (
                    release_confirmation.to_dict()
                    if release_confirmation is not None
                    else None
                ),
            },
            "automation_state": automation_state_details,
            "training_cancellations": {
                "available": cancellation_error is None,
                "effective_count": len(effective_cancellations),
                "error": cancellation_error,
                "fail_closed": cancellation_error is not None,
            },
            "mower": mower_details,
            "input_files": {
                "config": str(Path(config_path)),
                "matches": str(Path(matches_path)),
                "matches_found": Path(matches_path).exists(),
                "matches_loaded": len(match_blocks),
                "source_kind": runtime_inputs.source_kind,
                "manifest_etag": runtime_inputs.manifest_etag,
                "manifest_path": runtime_inputs.manifest_path,
                "published_at_utc": runtime_inputs.published_at_utc,
                "fallback_used": runtime_inputs.fallback_used,
            },
            "safety": {
                "read_only": True,
                "command_functions_present": False,
                "command_sent": False,
                "persistent_safety_state_write": (
                    settings.control_mode is ControlMode.DRY_RUN
                ),
            },
        },
    )
