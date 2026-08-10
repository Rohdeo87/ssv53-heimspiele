from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import azure.functions as func

from mower.controller import run_control_cycle
from mower.config_source import resolve_runtime_inputs
from occupancy.service import build_occupancy_payload


app = func.FunctionApp()
LOGGER = logging.getLogger("ssv53.azure.platzpflege")


@app.timer_trigger(
    schedule="%TIMER_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
@app.retry(
    strategy="fixed_delay",
    max_retry_count="3",
    delay_interval="00:00:10",
)
def ssv53_mower_timer(
    timer: func.TimerRequest,
    context: func.Context,
) -> None:
    """Sicherer Azure-Heartbeat als erste Migrationsstufe."""

    result = run_control_cycle(
        now_utc=datetime.now(timezone.utc),
        environment=os.environ,
        past_due=bool(timer.past_due),
    )
    payload = result.to_dict()
    payload["invocation_id"] = context.invocation_id

    retry_context = getattr(context, "retry_context", None)
    payload["retry_count"] = (
        getattr(retry_context, "retry_count", 0)
        if retry_context is not None
        else 0
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    if timer.past_due:
        LOGGER.warning("SSV53_CONTROL_CYCLE_PAST_DUE %s", serialized)
    else:
        LOGGER.info("SSV53_CONTROL_CYCLE %s", serialized)

def _occupancy_truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _occupancy_matches_path(now_utc: datetime) -> tuple[str, str]:
    """Bevorzugt die aktuelle Azure-Blob-Spielplandatei, fällt für die öffentliche
    Anzeige aber auf das mitdeployte Paket zurück. Die Mäherlogik bleibt davon
    unberührt und weiterhin fail-closed.
    """
    packaged = (
        os.environ.get("MOWER_MATCHES_PATH", "public/rasen.ics").strip()
        or "public/rasen.ics"
    )
    if not _occupancy_truthy(os.environ.get("SSV53_DYNAMIC_CONFIG_ENABLED")):
        return packaged, "package"

    try:
        runtime_inputs = resolve_runtime_inputs(
            os.environ,
            now_utc=now_utc,
        )
        return runtime_inputs.matches_path, runtime_inputs.source_kind
    except Exception as exc:
        LOGGER.warning(
            "SSV53_OCCUPANCY_MATCH_SOURCE_FALLBACK reason=%s",
            type(exc).__name__,
        )
        return packaged, "package_fallback"


def _occupancy_headers(*, cache: bool) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Cache-Control": "public, max-age=60" if cache else "no-store",
        "X-Content-Type-Options": "nosniff",
    }


@app.route(
    route="occupancy",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ssv53_occupancy(req: func.HttpRequest) -> func.HttpResponse:
    """Öffentliche, read-only Platzbelegung für die SSV53-App."""

    if req.method.upper() == "OPTIONS":
        return func.HttpResponse(
            status_code=204,
            headers=_occupancy_headers(cache=False),
        )

    now_utc = datetime.now(timezone.utc)
    local_now = now_utc.astimezone(ZoneInfo("Europe/Berlin"))
    start = req.params.get("start") or local_now.date().isoformat()
    end = req.params.get("end") or (
        local_now.date() + timedelta(days=14)
    ).isoformat()
    season = req.params.get("season") or "Sommer"

    try:
        matches_path, match_source = _occupancy_matches_path(now_utc)
        payload = build_occupancy_payload(
            config_path=os.environ.get(
                "OCCUPANCY_CONFIG_PATH",
                "occupancy/config.json",
            ),
            matches_path=matches_path,
            start=start,
            end=end,
            season=season,
            generated_at=now_utc,
        )
        payload["data_source"] = "azure"
        payload["match_source"] = match_source
        return func.HttpResponse(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
            status_code=200,
            mimetype="application/json",
            charset="utf-8",
            headers=_occupancy_headers(cache=True),
        )
    except ValueError as exc:
        return func.HttpResponse(
            json.dumps(
                {"error": str(exc)},
                ensure_ascii=False,
            ),
            status_code=400,
            mimetype="application/json",
            charset="utf-8",
            headers=_occupancy_headers(cache=False),
        )
    except Exception:
        LOGGER.exception("SSV53_OCCUPANCY_ERROR")
        return func.HttpResponse(
            json.dumps(
                {"error": "Belegungsdaten konnten nicht geladen werden."},
                ensure_ascii=False,
            ),
            status_code=500,
            mimetype="application/json",
            charset="utf-8",
            headers=_occupancy_headers(cache=False),
        )

