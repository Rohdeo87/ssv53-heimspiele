from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from mower.config_source import resolve_runtime_inputs
from mower.decision import (
    AUTOMATION_EXTERNAL_REASON,
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
from mower.hydrawise import HydrawiseError, fetch_status
from mower.planner import create_plan, load_json, read_match_blocks
from mower.runtime import CycleResult, RuntimeSettings


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


def run_read_only_cycle(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    environment: Mapping[str, str],
    past_due: bool,
    source: str,
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

    match_blocks = read_match_blocks(matches_path, tz)
    plans, _merged = create_plan(
        config,
        match_blocks,
        hydrawise_status,
        now_local.date(),
        2,
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
    decision = classify_decision(
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
            },
        },
    )
