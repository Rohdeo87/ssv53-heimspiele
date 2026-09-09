from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime, time, timedelta, timezone
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
from mower.planner import (
    create_plan,
    load_json,
    read_match_blocks,
    resolve_training_cancellation_keys,
    Block,
)
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.state_store import AzureTableStateStore, StateStore
from mower.adaptive_planner import build_adaptive_plan
from mower.coordination_shadow import capture_planning_inputs
from mower.coordination_inputs import capture_enabled, prepare_coordination_inputs
from mower.status_cache import cache_mode, read_status_cached
from mower.dashboard_observations import (
    dashboard_observation_store_from_environment,
    publish_dashboard_observation,
    read_dashboard_observation,
)
from mower.weather_service import resolve_weather
from occupancy.training_runtime import resolve_runtime_training, training_mode
from occupancy.training_control import TrainingControlSnapshot
from occupancy.training_control import resolve_training_control
from training_cancellations import AzureTableCancellationStore
from special_occupancy import (
    AzureTableSpecialOccupancyStore,
    enabled as special_occupancy_enabled,
    event_to_mower_block,
    relocated_training_occurrence_keys,
)


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
        "details": block.details,
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


def _safe_mowing_windows(
    day_plans: list[Any],
    now: datetime,
    *,
    park_lookahead_minutes: int,
    minimum_mowing_minutes: int,
) -> list[dict[str, Any]]:
    windows = sorted(
        (
            window
            for plan in day_plans
            for window in plan.mowing_windows
            if window.end > now
        ),
        key=lambda window: (window.start, window.end),
    )
    merged: list[Any] = []
    for window in windows:
        if merged and window.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = type(previous)(
                start=previous.start,
                end=max(previous.end, window.end),
            )
        else:
            merged.append(window)

    safe: list[dict[str, Any]] = []
    for window in merged:
        available_start = max(window.start, now)
        command_deadline = window.end - timedelta(minutes=park_lookahead_minutes)
        available_minutes = int((command_deadline - available_start).total_seconds() // 60)
        if available_minutes < minimum_mowing_minutes:
            continue
        safe.append(
            {
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "command_deadline": command_deadline.isoformat(),
                "minimum_mowing_minutes": minimum_mowing_minutes,
            }
        )
        if len(safe) >= 4:
            break
    return safe


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
    special_store_factory=AzureTableSpecialOccupancyStore.from_environment,
    persist_observations: bool = True,
    publish_dashboard_snapshot: bool = False,
    dashboard_snapshot_only: bool = False,
    dashboard_store_factory=dashboard_observation_store_from_environment,
    dashboard_observation_clock=None,
    training_control_snapshot: TrainingControlSnapshot | None = None,
) -> CycleResult:
    """Führt die komplette Live-Abfrage aus, sendet aber keinerlei Befehle."""

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss eine zeitzonenbewusste UTC-Zeit sein.")
    if dashboard_snapshot_only and (persist_observations or publish_dashboard_snapshot):
        raise ValueError("Dashboard-Beobachtungen dürfen ausschließlich gelesen werden.")

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
    planning_horizon_hours = int(
        environment.get("PLANNING_HORIZON_HOURS", "48")
    )
    if not 24 <= planning_horizon_hours <= 168:
        raise RuntimeError(
            "PLANNING_HORIZON_HOURS muss zwischen 24 und 168 liegen."
        )
    planning_horizon_days = max(
        2,
        (planning_horizon_hours + 23) // 24,
    )

    weather_resolution = resolve_weather(
        environment,
        now_utc=now_utc,
    )

    hydrawise_status: dict[str, Any] | None = None
    hydrawise_label = "nicht verbunden"
    hydrawise_error: str | None = None
    hydrawise_cache = None
    dashboard_observation = None
    confirmation_observed_until_utc = None
    hydrawise_key = environment.get("HYDRAWISE_API_KEY", "").strip()
    controller_id = environment.get("HYDRAWISE_CONTROLLER_ID", "").strip() or None
    if dashboard_snapshot_only:
        # The app reads the control reader's immutable observation. It never
        # falls back to another vendor request or earns new confirmation time.
        try:
            dashboard_store = dashboard_store_factory(environment)
            if dashboard_store is None:
                raise RuntimeError("Dashboard observation is disabled")
            observation = read_dashboard_observation(
                dashboard_store, controller_id, expected_relay_ids=expected_relay_ids,
                now_utc=now_utc,
            )
            hydrawise_status = observation.status
            dashboard_observation = {
                "quality": observation.quality,
                "source_observed_at_utc": observation.source_observed_at_utc,
                "fetched_at_utc": observation.fetched_at_utc,
                "age_seconds": observation.age_seconds,
                "read_only_snapshot": True,
            }
        except Exception:
            dashboard_observation = {"quality": "CACHE_UNAVAILABLE", "read_only_snapshot": True}
        hydrawise_label = "Aktueller Bewässerungsstand" if hydrawise_status is not None else "Aktueller Stand fehlt"
        if hydrawise_status is None:
            hydrawise_error = "Bewässerungsstand fehlt. Bitte aktualisieren und die Anlage prüfen."
    elif hydrawise_key:
        try:
            if cache_mode(environment) == "AZURE_TABLE":
                cached = read_status_cached(
                    hydrawise_key, controller_id, environment=environment,
                    hydrawise_config=hydrawise_config, now_utc=now_utc, fetcher=fetch_status,
                )
                hydrawise_status = cached.status
                hydrawise_cache = cached.metadata()
                confirmation_observed_until_utc = cached.confirmation_observed_until_utc or ""
                if hydrawise_status is None:
                    hydrawise_error = "Aktueller Bewässerungsstand fehlt. Nächsten erlaubten Abruf abwarten."
            else:
                hydrawise_status = fetch_status(hydrawise_key, controller_id)
                if publish_dashboard_snapshot:
                    # Optional display publication must never prevent the
                    # controller from evaluating its direct vendor evidence.
                    try:
                        dashboard_store = dashboard_store_factory(environment)
                        if dashboard_store is not None:
                            quality = publish_dashboard_observation(
                                dashboard_store, controller_id, hydrawise_status,
                                expected_relay_ids=expected_relay_ids,
                                clock=dashboard_observation_clock,
                            )
                            dashboard_observation = {"quality": quality, "read_only_snapshot": False}
                    except Exception:
                        dashboard_observation = {"quality": "PUBLISH_FAILED", "read_only_snapshot": False}
            relays = (hydrawise_status or {}).get("relays")
            hydrawise_label = (
                (f"Gemeinsamer Status ({len(relays)} Zonen)" if isinstance(relays, list) else "Aktueller Stand fehlt")
                if hydrawise_cache is not None else
                (f"live ({len(relays)} Zonen)" if isinstance(relays, list) else "live (unvollständige Zonenantwort)")
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
    # A later cache read or timer tick cannot extend the source's evidence.
    # This applies to direct reads too: vendors may repeat the same status.
    confirmation_observed_until_utc = (
        hydrawise_safety.observed_at_utc
        if hydrawise_safety.available and hydrawise_safety.fresh else ""
    )
    # The safety assessment owns validation. Malformed or incomplete data must
    # remain an explicit unknown and must not crash the less strict display /
    # legacy calendar parsers after it has already been rejected for control.
    planning_hydrawise_status = (
        hydrawise_status
        if hydrawise_safety.available and hydrawise_safety.relay_set_valid
        else None
    )
    hydrawise_zones = selected_zone_schedule(
        planning_hydrawise_status,
        hydrawise_config,
    )
    hydrawise_zone_observations = selected_zone_observations(
        planning_hydrawise_status,
        hydrawise_config,
    )

    cancellation_error: str | None = None
    effective_cancellations: set[tuple[str, str]] = set()
    unresolved_cancellations: list[str] = []
    cancellations = []
    try:
        cancellation_store = cancellation_store_factory(environment)
        cancellations = cancellation_store.list_active(
            now_local.date() - timedelta(days=int(training_mode(environment) != "OFF")),
            now_local.date() + timedelta(days=planning_horizon_days),
        )
        effective_cancellations, unresolved_cancellations = (
            resolve_training_cancellation_keys(
                _as_dict(config.get("training")),
                [item for item in cancellations if item.is_effective(now_utc)],
                tz,
            )
        )
    except Exception as exc:
        # Fail closed: Ohne verlässliche Absagen bleiben alle Trainings gesperrt.
        cancellation_error = f"{type(exc).__name__}: {exc}"

    special_enabled = special_occupancy_enabled(environment)
    special_error: str | None = None
    special_events = []
    special_blocks = []
    special_horizon_start = datetime.combine(
        now_local.date(),
        time.min,
        tzinfo=tz,
    )
    special_horizon_end = special_horizon_start + timedelta(
        days=planning_horizon_days
    )
    if special_enabled:
        try:
            special_store = special_store_factory(environment)
            special_events = special_store.list_active(
                special_horizon_start,
                special_horizon_end,
            )
            effective_cancellations.update(
                relocated_training_occurrence_keys(special_events)
            )
            special_blocks = [
                block
                for event in special_events
                if (block := event_to_mower_block(event, Block)) is not None
            ]
        except Exception as exc:
            # Fail closed: Sind Sonderbelegungen nicht lesbar, bleibt der
            # Rasen für den gesamten Planungshorizont gesperrt.
            special_error = f"{type(exc).__name__}: {exc}"
            special_blocks = [
                Block(
                    start=special_horizon_start,
                    end=special_horizon_end,
                    source="special",
                    title="Sonderbelegungsstatus unklar",
                    details={"fail_closed": True},
                )
            ]

    training_control = (
        training_control_snapshot
        if training_control_snapshot is not None
        else resolve_training_control(
            environment,
            now_utc=now_utc,
            state_store_factory=state_store_factory,
        )
    )
    training = resolve_runtime_training(
        config, consumer="mower", environment=environment, legacy_config=config,
        range_start=special_horizon_start, range_end=special_horizon_end, now_utc=now_utc,
        cancellations=cancellations,
        relocated_keys=relocated_training_occurrence_keys(special_events),
        source_fresh=not runtime_inputs.fallback_used,
        control_snapshot=training_control,
    )
    if training.blocking_required:
        # Continue into telemetry and the normal parking path. Raising here
        # would leave a currently moving mower without a parking decision.
        special_blocks.append(Block(
            start=special_horizon_start, end=special_horizon_end, source="training",
            title="Trainingstermine unklar – Platzfreigabe prüfen",
            details={"fail_closed": True, "reason": "SHARED_TRAINING_UNAVAILABLE"},
        ))
    match_blocks = read_match_blocks(matches_path, tz)
    match_blocks.extend(special_blocks)
    plans, merged_blocks = create_plan(
        config,
        match_blocks,
        planning_hydrawise_status,
        now_local.date(),
        planning_horizon_days,
        effective_cancellations,
        training_batch=training.batch,
    )
    adaptive_plan = build_adaptive_plan(
        now_utc=now_utc,
        timezone_name=settings.timezone_name,
        blocks=merged_blocks,
        zones=hydrawise_zones,
        weather_snapshot=weather_resolution.snapshot,
        weather_fresh=weather_resolution.fresh,
        environment=environment,
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
    original_state = None
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
                    _parse_utc(confirmation_observed_until_utc)
                    if hydrawise_safety.available and hydrawise_safety.fresh else None
                ),
                hydrawise_observed_utc=_parse_utc(
                    hydrawise_safety.observed_at_utc
                ),
                hydrawise_clear=(
                    hydrawise_safety.available
                    and hydrawise_safety.fresh
                    and hydrawise_safety.clear_now
                ),
                hydrawise_active_count=(
                    hydrawise_safety.active_zone_count
                    if hydrawise_safety.available and hydrawise_safety.fresh else None
                ),
            )
            release_confirmation = evaluate_continuous_clear_confirmation(
                confirmation_observed_until_utc=confirmation_observed_until_utc,
                available=hydrawise_safety.available,
                fresh=hydrawise_safety.fresh,
                clear_now=hydrawise_safety.clear_now,
                physical_reason=hydrawise_safety.reason,
                clear_since_utc=projected_state.hydrawise_clear_since_utc,
                now_utc=now_utc,
                required_clear_minutes=(
                    max(150, int(environment.get("POST_IRRIGATION_DRYING_MINUTES", "150")))
                    if projected_state.hydrawise_drying_since_utc else required_clear_minutes
                ),
                persistent_state_available=True,
                drying_since_utc=projected_state.hydrawise_drying_since_utc,
                telemetry_confirmation_minutes=int(environment.get("HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES", "2")),
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
            if persist_observations:
                store.save(
                    projected_state,
                    expected_revision=original_state.revision,
                )
            state_persisted = bool(persist_observations)
        except Exception as exc:
            state_persisted = False
            state_error = f"{type(exc).__name__}: {exc}"
            release_confirmation = evaluate_continuous_clear_confirmation(
                confirmation_observed_until_utc=confirmation_observed_until_utc,
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
            "hydrawise_drying_since_utc": (
                projected_state.hydrawise_drying_since_utc if projected_state is not None else None
            ),
            "persisted": state_persisted,
            "error": state_error,
        }
    else:
        if settings.control_mode in {ControlMode.FULL_FAILSAFE, ControlMode.FULL_MOWER}:
            # Read-only callers (including the app) need the persisted physical
            # hold as well as the current response. Project the observation with
            # the same transition rules as the controller, but never advance a
            # confirmation chain, a revision or a device intent in storage.
            projected_state = None
            original_state = None
            state_error = None
            telemetry_minutes = int(environment.get(
                "FULL_MOWER_HYDRAWISE_CLEAR_CONFIRMATION_MINUTES"
                if settings.control_mode is ControlMode.FULL_MOWER
                else "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES",
                "10" if settings.control_mode is ControlMode.FULL_MOWER else "2",
            ))
            try:
                original_state = state_store_factory(environment).load()
                trusted_hydrawise = (
                    hydrawise_safety.available and hydrawise_safety.fresh
                    and hydrawise_safety.relay_set_valid
                )
                projected_state = original_state.record_cycle(
                    started_utc=now_utc,
                    success=True,
                    decision_code=base_decision.code,
                    hydrawise_success_utc=now_utc if trusted_hydrawise else None,
                    hydrawise_observed_utc=_parse_utc(hydrawise_safety.observed_at_utc),
                    hydrawise_clear=bool(trusted_hydrawise and hydrawise_safety.clear_now),
                    hydrawise_active_count=(
                        hydrawise_safety.active_zone_count if trusted_hydrawise else None
                    ),
                )
            except Exception as exc:
                state_error = f"{type(exc).__name__}: {exc}"
            release_confirmation = evaluate_continuous_clear_confirmation(
                confirmation_observed_until_utc=confirmation_observed_until_utc,
                available=hydrawise_safety.available,
                fresh=hydrawise_safety.fresh,
                clear_now=hydrawise_safety.clear_now,
                physical_reason=hydrawise_safety.reason,
                clear_since_utc=projected_state.hydrawise_clear_since_utc if projected_state else None,
                now_utc=now_utc,
                required_clear_minutes=(
                    max(150, int(environment.get("POST_IRRIGATION_DRYING_MINUTES", "150")))
                    if projected_state and projected_state.hydrawise_drying_since_utc
                    else telemetry_minutes
                ),
                persistent_state_available=projected_state is not None,
                drying_since_utc=projected_state.hydrawise_drying_since_utc if projected_state else None,
                telemetry_confirmation_minutes=telemetry_minutes,
            )
            automation_state_details = {
                "revision": original_state.revision if original_state else None,
                "hydrawise_clear_since_utc": projected_state.hydrawise_clear_since_utc if projected_state else None,
                "hydrawise_drying_since_utc": projected_state.hydrawise_drying_since_utc if projected_state else None,
                "read_only": True,
                "projection_only": True,
                "persisted": False,
                "error": state_error,
            }
        # Keep the device controller's instantaneous planning input separate:
        # FULL modes recompute the authoritative release against their own CAS
        # state immediately before deciding or issuing any device action.
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

    result = CycleResult(
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
                "upcoming_blocks": [
                    _block_to_dict(block)
                    for block in merged_blocks
                    if block.end > now_local
                ][:6],
                "safe_mowing_windows": _safe_mowing_windows(
                    plans,
                    now_local,
                    park_lookahead_minutes=settings.park_lookahead_minutes,
                    minimum_mowing_minutes=minimum_remaining,
                ),
                "parking_lookahead_minutes": (
                    settings.park_lookahead_minutes
                ),
            },
            "hydrawise": {
                "cache": hydrawise_cache,
                "dashboard_observation": dashboard_observation,
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
            "weather": weather_resolution.to_dict(),
            "adaptive_planning": adaptive_plan.to_dict(),
            # Complete source copies serve replay and the optional coordinator.
            # The latter still requires independent persistent admission gates.
            "coordination_shadow_input": (
                capture_planning_inputs(
                    now_utc=now_utc, blocks=merged_blocks, runtime_inputs=runtime_inputs,
                    complete_from=special_horizon_start, complete_until=special_horizon_end,
                    special_available=special_enabled and special_error is None,
                    state=original_state,
                ) if capture_enabled(environment)
                else None
            ),
            "automation_state": automation_state_details,
            "training_calendar": training.metadata(),
            "training_cancellations": {
                "available": cancellation_error is None,
                "effective_count": len(effective_cancellations),
                "unresolved_event_ids": unresolved_cancellations,
                "error": cancellation_error,
                "fail_closed": cancellation_error is not None,
            },
            "special_occupancy": {
                "enabled": special_enabled,
                "available": (not special_enabled) or special_error is None,
                "event_count": len(special_events),
                "rasen_block_count": len(special_blocks),
                "error": special_error,
                "fail_closed": special_enabled and special_error is not None,
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
                    settings.control_mode is ControlMode.DRY_RUN and persist_observations
                ),
            },
        },
    )
    coordination = prepare_coordination_inputs(
        cycle=result.to_dict(), config=config, environment=environment,
        source_fresh=(runtime_inputs.source_kind in {"azure_blob", "azure_blob_cache"}
                      and bool(runtime_inputs.manifest_etag and runtime_inputs.published_at_utc)
                      and not runtime_inputs.fallback_used),
    )
    if coordination is not None:
        result = replace(result, details={**result.details, "coordination_execution_input": coordination})
    return result
