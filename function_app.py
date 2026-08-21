from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import azure.functions as func

from daily_safety_report import process_daily_report

from mower.controller import run_control_cycle
from mower.irrigation_recovery import (
    IrrigationRecoveryError,
    reset_failed_irrigation,
)
from occupancy.service import build_occupancy_payload, build_training_occurrences
from occupancy.runtime_source import (
    OccupancyMatchSource,
    resolve_occupancy_match_source,
)
from occupancy_notifications import (
    process_collision_notifications,
    send_collision_test_mail,
    send_real_collision_test_mail,
)
from training_cancellations import AzureTableCancellationStore
from special_occupancy import (
    AzureTableSpecialOccupancyStore,
    SpecialOccupancyError,
    enabled as special_occupancy_enabled,
    fail_closed_public_events,
    merge_public_special_events,
    parse_admin_request,
)
from order_mail import OrderMailError, check_smtp_connection, send_order_ready_mail
from platzwart_console import (
    PlatzwartError,
    enroll as platzwart_enroll,
    live_status as platzwart_live_status,
    unavailable_live_status as platzwart_unavailable_live_status,
    login as platzwart_login,
    request_action as platzwart_request_action,
    require_session as require_platzwart_session,
)


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



@app.timer_trigger(
    schedule="30 */2 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def ssv53_occupancy_notification_timer(timer: func.TimerRequest) -> None:
    """Eigenständiger Mailzyklus ohne Aufruf oder Befehlsweg zum Mäher."""
    try:
        notification_result = process_collision_notifications(
            datetime.now(timezone.utc),
            os.environ,
        )
        if notification_result["collisions"]:
            LOGGER.info(
                "SSV53_OCCUPANCY_NOTIFICATION_SUMMARY collisions=%s sent=%s",
                notification_result["collisions"],
                notification_result["sent"],
            )
    except Exception:
        # Keine Request-Bodys, Namen oder E-Mail-Adressen protokollieren.
        LOGGER.exception("SSV53_OCCUPANCY_COLLISION_NOTIFICATION_ERROR")

@app.timer_trigger(
    # UTC-Kandidaten decken Sommer- und Winterzeit ab. Die lokale Zeitprüfung
    # und der Tages-Claim verhindern Versand außerhalb von 07 Uhr und Duplikate.
    schedule="0 0 5,6,7,8 * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def ssv53_daily_safety_report_timer(timer: func.TimerRequest) -> None:
    """Täglicher, befehlsfreier Bericht aus vorhandener Telemetrie."""
    try:
        result = process_daily_report(datetime.now(timezone.utc), os.environ)
        LOGGER.info(
            "SSV53_DAILY_REPORT %s",
            json.dumps(result, ensure_ascii=False, sort_keys=True),
        )
    except Exception:
        # Keine Zugangsdaten oder Empfängeradressen protokollieren.
        LOGGER.exception("SSV53_DAILY_REPORT_ERROR")

def _occupancy_match_source(
    *,
    now_utc: datetime | None = None,
    environment: dict[str, str] | None = None,
) -> OccupancyMatchSource:
    """Öffentliche Belegung aus dem gemeinsamen strukturierten Matchmodell.

    Der App-Feed und die Mäher-ICS werden über dasselbe atomare Manifest
    veröffentlicht, bleiben aber fachlich getrennte und einzeln gehashte
    Dateien. Dadurch erreichen offizielle Spielverlegungen beide Verbraucher.
    """
    return resolve_occupancy_match_source(
        environment if environment is not None else os.environ,
        now_utc=now_utc or datetime.now(timezone.utc),
    )


def _occupancy_matches_path() -> tuple[str, str]:
    """Compatibility helper for internal callers and focused tests."""
    source = _occupancy_match_source()
    return source.matches_path, source.source_kind


def _occupancy_headers(*, cache: bool) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Cache-Control": "public, max-age=60" if cache else "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def _json_response(payload: dict, *, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        status_code=status_code,
        mimetype="application/json",
        charset="utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.route(
    route="irrigation/recover-failed",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def ssv53_irrigation_recover_failed(req: func.HttpRequest) -> func.HttpResponse:
    """Manueller, befehlsfreier Reset nach vollständiger Live-Sicherheitsprüfung."""

    try:
        body = req.get_json()
    except ValueError:
        return _json_response(
            {
                "code": "RESET_REQUEST_INVALID",
                "error": "Der Request muss ein JSON-Objekt enthalten.",
            },
            status_code=400,
        )
    if not isinstance(body, dict):
        return _json_response(
            {
                "code": "RESET_REQUEST_INVALID",
                "error": "Der Request muss ein JSON-Objekt enthalten.",
            },
            status_code=400,
        )
    try:
        expected_revision = int(body.get("expected_revision"))
    except (TypeError, ValueError):
        return _json_response(
            {
                "code": "RESET_REVISION_INVALID",
                "error": "expected_revision muss eine positive Ganzzahl sein.",
            },
            status_code=400,
        )

    try:
        result = reset_failed_irrigation(
            now_utc=datetime.now(timezone.utc),
            environment=os.environ,
            expected_revision=expected_revision,
            confirmation=str(body.get("confirmation") or ""),
        )
    except IrrigationRecoveryError as exc:
        LOGGER.warning(
            "SSV53_IRRIGATION_RESET_REJECTED %s",
            json.dumps(
                {"code": exc.code, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return _json_response(
            {"code": exc.code, "error": str(exc)},
            status_code=exc.status_code,
        )
    except Exception:
        LOGGER.exception("SSV53_IRRIGATION_RESET_ERROR")
        return _json_response(
            {
                "code": "RESET_INTERNAL_ERROR",
                "error": "Der sichere Beregnungsreset ist fehlgeschlagen.",
            },
            status_code=500,
        )

    payload = result.to_dict()
    LOGGER.warning(
        "SSV53_IRRIGATION_RESET %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    return _json_response(payload, status_code=200)


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
        match_source = _occupancy_match_source(now_utc=now_utc)
        matches_path = match_source.matches_path
        config_path = os.environ.get(
            "OCCUPANCY_CONFIG_PATH",
            "occupancy/config.json",
        )
        payload = build_occupancy_payload(
            config_path=config_path,
            matches_path=matches_path,
            start=start,
            end=end,
            season=season,
            generated_at=now_utc,
        )
        cancellation_error = None
        try:
            range_start = datetime.fromisoformat(payload["range"]["start"])
            range_end = datetime.fromisoformat(payload["range"]["end"])
            store = AzureTableCancellationStore.from_environment(os.environ)
            cancellations = store.list_active(range_start.date(), range_end.date())
            cancelled = {item.occurrence_key for item in cancellations}
            if cancelled:
                payload = build_occupancy_payload(
                    config_path=config_path,
                    matches_path=matches_path,
                    start=start,
                    end=end,
                    season=season,
                    generated_at=now_utc,
                    cancelled_occurrences=cancelled,
                )
        except Exception as exc:
            # Fail closed: Ein Speicherfehler darf niemals Trainingszeit freigeben.
            cancellation_error = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("SSV53_TRAINING_CANCELLATION_READ_ERROR")
        payload["training_cancellations"] = {
            "available": cancellation_error is None,
            "fail_closed": cancellation_error is not None,
        }
        special_enabled = special_occupancy_enabled(os.environ)
        special_error = None
        special_count = 0
        if special_enabled:
            special_start = datetime.fromisoformat(payload["range"]["start"])
            special_end = datetime.fromisoformat(payload["range"]["end"])
            try:
                special_store = AzureTableSpecialOccupancyStore.from_environment(
                    os.environ
                )
                special_events = special_store.list_active(
                    special_start,
                    special_end,
                )
                special_count = len(special_events)
                payload = merge_public_special_events(payload, special_events)
            except Exception as exc:
                special_error = f"{type(exc).__name__}: {exc}"
                LOGGER.exception("SSV53_SPECIAL_OCCUPANCY_READ_ERROR")
                payload = merge_public_special_events(
                    payload,
                    fail_closed_public_events(special_start, special_end),
                )
        payload["special_occupancy"] = {
            "enabled": special_enabled,
            "available": (not special_enabled) or special_error is None,
            "fail_closed": special_enabled and special_error is not None,
            "count": special_count,
            "error": special_error,
        }
        payload["data_source"] = "azure"
        payload["match_source"] = match_source.source_kind
        payload["match_source_generated_at_utc"] = (
            match_source.source_generated_at_utc
        )
        payload["match_source_fallback"] = match_source.fallback_used
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


@app.route(
    route="occupancy-admin",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def ssv53_occupancy_admin(req: func.HttpRequest) -> func.HttpResponse:
    """Geschützte Schreibschnittstelle für dynamische Sonderbelegungen."""

    try:
        body = req.get_json()
    except ValueError:
        return _json_response(
            {
                "ok": False,
                "code": "REQUEST_INVALID",
                "error": "Der Request muss gültiges JSON enthalten.",
            },
            status_code=400,
        )
    if not isinstance(body, dict):
        return _json_response(
            {
                "ok": False,
                "code": "REQUEST_INVALID",
                "error": "Der Request muss ein JSON-Objekt enthalten.",
            },
            status_code=400,
        )

    try:
        command = parse_admin_request(
            body,
            allowed_sender=os.environ.get(
                "SSV53_OCCUPANCY_COMMAND_ALLOWED_SENDER",
                "",
            ),
        )
        store = AzureTableSpecialOccupancyStore.from_environment(os.environ)
        result = store.apply(
            command,
            now_utc=datetime.now(timezone.utc),
        )
        LOGGER.warning(
            "SSV53_SPECIAL_OCCUPANCY_COMMAND command_id=%s action=%s duplicate=%s",
            result.get("commandId"),
            result.get("action"),
            result.get("duplicate"),
        )
        return _json_response(result, status_code=200)
    except SpecialOccupancyError as exc:
        LOGGER.warning(
            "SSV53_SPECIAL_OCCUPANCY_REJECTED code=%s",
            exc.code,
        )
        return _json_response(
            {
                "ok": False,
                "code": exc.code,
                "error": str(exc),
            },
            status_code=exc.status_code,
        )
    except Exception:
        LOGGER.exception("SSV53_SPECIAL_OCCUPANCY_ADMIN_ERROR")
        return _json_response(
            {
                "ok": False,
                "code": "SPECIAL_OCCUPANCY_INTERNAL_ERROR",
                "error": "Die Sonderbelegung konnte nicht gespeichert werden.",
            },
            status_code=503,
        )


def _trainer_occupancy_response(
    payload: dict,
    status_code: int = 200,
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        status_code=status_code,
        mimetype="application/json",
        charset="utf-8",
        headers=_occupancy_headers(cache=False),
    )


def _platzwart_headers(req: func.HttpRequest) -> dict[str, str]:
    origin = str(req.headers.get("Origin") or "").strip()
    allowed = {
        item.strip()
        for item in os.environ.get("SSV53_PLATZWART_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    headers = {
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Vary": "Origin",
    }
    if origin and origin in allowed:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _platzwart_response(
    req: func.HttpRequest,
    payload: dict,
    status_code: int = 200,
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        status_code=status_code,
        mimetype="application/json",
        charset="utf-8",
        headers=_platzwart_headers(req),
    )


def _platzwart_token(req: func.HttpRequest) -> str:
    authorization = str(req.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        raise PlatzwartError(
            "SESSION_MISSING",
            "Bitte mit der Platzwart-PIN anmelden.",
            401,
        )
    return authorization[7:].strip()


@app.route(
    route="platzwart/enroll",
    methods=["POST", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ssv53_platzwart_enroll(req: func.HttpRequest) -> func.HttpResponse:
    if req.method.upper() == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_platzwart_headers(req))
    try:
        body = req.get_json()
        if not isinstance(body, dict):
            raise ValueError
        result = platzwart_enroll(
            str(body.get("activationCode") or ""),
            os.environ,
            datetime.now(timezone.utc),
        )
        return _platzwart_response(req, result)
    except ValueError:
        return _platzwart_response(
            req,
            {"code": "REQUEST_INVALID", "error": "Der Aktivierungscode fehlt."},
            400,
        )
    except PlatzwartError as exc:
        return _platzwart_response(req, {"code": exc.code, "error": str(exc)}, exc.status_code)
    except Exception:
        LOGGER.exception("SSV53_PLATZWART_ENROLL_ERROR")
        return _platzwart_response(
            req,
            {"code": "ENROLL_ERROR", "error": "Das Gerät konnte nicht aktiviert werden."},
            503,
        )


@app.route(
    route="platzwart/login",
    methods=["POST", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ssv53_platzwart_login(req: func.HttpRequest) -> func.HttpResponse:
    if req.method.upper() == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_platzwart_headers(req))
    now = datetime.now(timezone.utc)
    try:
        body = req.get_json()
        if not isinstance(body, dict):
            raise ValueError
        result = platzwart_login(
            str(body.get("pin") or ""),
            str(body.get("deviceId") or ""),
            str(body.get("deviceToken") or ""),
            str(req.headers.get("X-Forwarded-For") or "unknown"),
            os.environ,
            now,
        )
        return _platzwart_response(req, result)
    except ValueError:
        return _platzwart_response(
            req,
            {"code": "REQUEST_INVALID", "error": "Bitte vier Ziffern eingeben."},
            400,
        )
    except PlatzwartError as exc:
        return _platzwart_response(req, {"code": exc.code, "error": str(exc)}, exc.status_code)
    except Exception:
        LOGGER.exception("SSV53_PLATZWART_LOGIN_ERROR")
        return _platzwart_response(
            req,
            {"code": "LOGIN_ERROR", "error": "Die Anmeldung ist gerade nicht möglich."},
            503,
        )


@app.route(
    route="platzwart/status",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ssv53_platzwart_status(req: func.HttpRequest) -> func.HttpResponse:
    if req.method.upper() == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_platzwart_headers(req))
    now = datetime.now(timezone.utc)
    try:
        require_platzwart_session(_platzwart_token(req), os.environ, now)
        return _platzwart_response(req, platzwart_live_status(os.environ, now))
    except PlatzwartError as exc:
        return _platzwart_response(req, {"code": exc.code, "error": str(exc)}, exc.status_code)
    except Exception:
        LOGGER.exception("SSV53_PLATZWART_STATUS_ERROR")
        # Die Seite bleibt auch bei einem unerwarteten Lesefehler nutzbar. Die
        # Ersatzantwort enthält bewusst keine freigegebene Bedienaktion.
        return _platzwart_response(req, platzwart_unavailable_live_status(now))


@app.route(
    route="platzwart/action",
    methods=["POST", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ssv53_platzwart_action(req: func.HttpRequest) -> func.HttpResponse:
    if req.method.upper() == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_platzwart_headers(req))
    now = datetime.now(timezone.utc)
    try:
        session = require_platzwart_session(_platzwart_token(req), os.environ, now)
        body = req.get_json()
        if not isinstance(body, dict):
            raise ValueError
        result = platzwart_request_action(
            str(body.get("action") or ""),
            str(body.get("requestId") or ""),
            str(body.get("confirmation") or ""),
            os.environ,
            now,
            zone=(int(body["zone"]) if body.get("zone") is not None else None),
            run_seconds=(
                int(body["runSeconds"])
                if body.get("runSeconds") is not None
                else None
            ),
            cutting_height_mm=(
                int(body["cuttingHeightMm"])
                if body.get("cuttingHeightMm") is not None
                else None
            ),
            occupancy_override_key=str(body.get("occupancyOverrideKey") or ""),
            irrigation_schedule=(
                body.get("schedule")
                if isinstance(body.get("schedule"), dict)
                else None
            ),
        )
        LOGGER.warning(
            "SSV53_PLATZWART_ACTION_ACCEPTED action=%s request_id=%s device=%s",
            str(body.get("action") or "")[:48],
            str(body.get("requestId") or "")[:64],
            str(session.get("did") or "")[:24],
        )
        return _platzwart_response(req, result, 202)
    except ValueError:
        return _platzwart_response(
            req,
            {"code": "REQUEST_INVALID", "error": "Die Anfrage ist unvollständig."},
            400,
        )
    except PlatzwartError as exc:
        return _platzwart_response(req, {"code": exc.code, "error": str(exc)}, exc.status_code)
    except Exception:
        LOGGER.exception("SSV53_PLATZWART_ACTION_ERROR")
        return _platzwart_response(
            req,
            {"code": "ACTION_ERROR", "error": "Die Bedienaktion konnte nicht sicher gespeichert werden."},
            503,
        )


def _trainer_occupancy_conflicts(
    *,
    start: datetime,
    end: datetime,
    resource_id: str,
    store,
    ignored_event_ids: set[str] | None = None,
) -> list[dict]:
    """Prüft denselben physischen Platz gegen Plan, Spiele und Sondertermine."""
    config_path = os.environ.get("OCCUPANCY_CONFIG_PATH", "occupancy/config.json")
    matches_path = _occupancy_match_source(
        now_utc=datetime.now(timezone.utc),
    ).matches_path
    candidates: dict[str, dict] = {}
    ignored = {
        str(value or "").strip().lower()
        for value in (ignored_event_ids or set())
    }
    # Dieselbe Saisonwahl wie im Appack-Kalender verhindert Warnungen durch
    # einen lediglich alternativ dargestellten Winterplan im Sommer (und
    # umgekehrt). Sondertermine bleiben ohnehin saisonunabhängig sichtbar.
    season = "Winter" if start.month in {11, 12, 1, 2} else "Sommer"
    for season in (season,):
        payload = build_occupancy_payload(
            config_path=config_path,
            matches_path=matches_path,
            start=start.date().isoformat(),
            end=(end.date() + timedelta(days=1)).isoformat(),
            season=season,
            generated_at=datetime.now(timezone.utc),
        )
        for item in payload.get("events", []):
            if str(item.get("id") or "").strip().lower() in ignored:
                continue
            if str(item.get("resourceId") or "") != resource_id:
                continue
            event_start = datetime.fromisoformat(str(item.get("occupancyStart") or item["start"]))
            event_end = datetime.fromisoformat(str(item.get("occupancyEnd") or item["end"]))
            if event_end > start and event_start < end:
                # Derselbe physische Termin kann in beiden Saisonansichten
                # vorkommen. Für die Warnung zählt er trotzdem nur einmal.
                key = "|".join(
                    (
                        resource_id,
                        event_start.isoformat(),
                        event_end.isoformat(),
                        str(item.get("title") or "").strip().casefold(),
                        str(item.get("source") or "").strip().casefold(),
                    )
                )
                candidates[key] = dict(item, start=event_start.isoformat(), end=event_end.isoformat())
    for event in store.list_active(start, end):
        if event.event_id in ignored or ("one-off:" + event.event_id) in ignored:
            continue
        if event.resource_id == resource_id and event.end > start and event.start < end:
            item = event.to_public_event()
            candidates[item["id"]] = item
    return [
        {
            "id": item.get("id"),
            "title": item.get("title") or "Belegung",
            "start": item.get("start"),
            "end": item.get("end"),
            "source": item.get("source"),
        }
        for item in sorted(candidates.values(), key=lambda value: value["start"])
    ]


def _trainer_move_source(
    body: dict,
    store,
    *,
    now_utc: datetime,
) -> tuple[dict, str, str]:
    """Löst eine Verlegung serverseitig auf; Client-Zeiten sind nie maßgeblich."""

    source_id = str(body.get("eventId") or "").strip().lower()
    requester_id = str(body.get("requesterId") or "").strip()
    is_admin = bool(body.get("isAppAdministrator"))
    if source_id.startswith("training:"):
        occurrence = _trainer_training_occurrence(source_id)
        event_id = "trainer-move-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:32]
        moved_by = body.get("creator") if isinstance(body.get("creator"), dict) else {}
        event = {
            "id": event_id,
            "title": occurrence.get("title") or "Training",
            "start": occurrence.get("start"),
            "end": occurrence.get("end"),
            "resourceId": occurrence.get("resourceId"),
            "area": occurrence.get("area") or "vorne & hinten",
            "description": occurrence.get("description") or "",
            "team": occurrence.get("team") or occurrence.get("title") or "",
            "creator": {},
            "movedBy": moved_by,
            "replacesTrainingEventId": source_id,
        }
        return event, source_id, str(occurrence.get("resourceId") or "").lower()

    event_id = source_id.removeprefix("one-off:")
    existing = store.get_active(event_id)
    if existing is None:
        raise SpecialOccupancyError("EVENT_NOT_FOUND", "Die Belegung wurde nicht gefunden.", status_code=404)
    if (
        not existing.replaced_training_event_id
        and not is_admin
        and (not requester_id or requester_id != existing.creator_id)
    ):
        raise SpecialOccupancyError(
            "MOVE_FORBIDDEN",
            "Diese Belegung darf nur vom Ersteller oder App-Administrator verschoben werden.",
            status_code=403,
        )
    existing_creator = {
        "id": existing.creator_id,
        "name": existing.creator_name,
        "phone": existing.creator_phone,
        "mobile": existing.creator_mobile,
        "email": existing.creator_email,
        "chatId": existing.creator_chat_id,
        "image": existing.creator_image,
        "instagram": existing.creator_instagram,
        "website": existing.creator_website,
        "facebook": existing.creator_facebook,
        "role": existing.creator_role,
        "infoHtml": existing.creator_info_html,
    }
    submitted_creator = (
        body.get("creator") if isinstance(body.get("creator"), dict) else {}
    )
    # Ausschließlich der ursprüngliche Ersteller darf sein gespeichertes
    # Profil vervollständigen. So wird z. B. ein alter Nachname-Fallback beim
    # nächsten Verlegen zu Vor- und Nachname korrigiert, während ein Admin
    # niemals versehentlich die ursprüngliche Person überschreibt.
    if (
        requester_id
        and requester_id == existing.creator_id
        and requester_id == str(submitted_creator.get("id") or "").strip()
    ):
        submitted_name = str(submitted_creator.get("name") or "").strip()
        existing_name = str(existing_creator.get("name") or "").strip()
        if submitted_name and (
            not existing_name
            or len(submitted_name.split()) > len(existing_name.split())
        ):
            existing_creator["name"] = submitted_name
        for key in (
            "phone", "mobile", "email", "chatId", "image", "instagram",
            "website", "facebook", "role", "infoHtml",
        ):
            if not existing_creator.get(key) and submitted_creator.get(key):
                existing_creator[key] = submitted_creator[key]

    event = {
        "id": existing.event_id,
        "title": existing.title,
        "start": existing.start.isoformat(),
        "end": existing.end.isoformat(),
        "resourceId": existing.resource_id,
        "area": existing.area,
        "description": existing.description,
        "team": existing.team,
        "creator": existing_creator,
        "movedBy": (
            body.get("creator")
            if isinstance(body.get("creator"), dict) and body.get("creator")
            else {
                "id": existing.moved_by_id,
                "name": existing.moved_by_name,
                "phone": existing.moved_by_phone,
                "mobile": existing.moved_by_mobile,
                "email": existing.moved_by_email,
                "chatId": existing.moved_by_chat_id,
                "image": existing.moved_by_image,
                "instagram": existing.moved_by_instagram,
                "website": existing.moved_by_website,
                "facebook": existing.moved_by_facebook,
                "role": existing.moved_by_role,
                "infoHtml": existing.moved_by_info_html,
            }
        ),
        "replacesTrainingEventId": existing.replaced_training_event_id,
    }
    return event, source_id, existing.resource_id


def _trainer_training_occurrence(event_id: str) -> dict:
    """Löst ausschließlich eine konfigurierte, konkrete Trainingsinstanz auf."""

    source_id = str(event_id or "").strip().lower()
    parts = source_id.split(":")
    if len(parts) < 4 or not source_id.startswith("training:"):
        raise SpecialOccupancyError(
            "EVENT_NOT_FOUND",
            "Der Trainingstermin wurde nicht gefunden.",
            status_code=404,
        )
    try:
        day = datetime.fromisoformat(parts[-1]).date()
    except ValueError as exc:
        raise SpecialOccupancyError(
            "EVENT_NOT_FOUND",
            "Der Trainingstermin wurde nicht gefunden.",
            status_code=404,
        ) from exc
    occurrences = build_training_occurrences(
        config_path=os.environ.get("OCCUPANCY_CONFIG_PATH", "occupancy/config.json"),
        start=day.isoformat(),
        end=(day + timedelta(days=1)).isoformat(),
        season=parts[1].capitalize(),
    )
    occurrence = next(
        (
            item
            for item in occurrences
            if str(item.get("id") or "").strip().lower() == source_id
        ),
        None,
    )
    if occurrence is None:
        raise SpecialOccupancyError(
            "EVENT_NOT_FOUND",
            "Der Trainingstermin wurde nicht gefunden.",
            status_code=404,
        )
    return occurrence


@app.route(
    route="trainer-occupancies",
    methods=["POST", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ssv53_trainer_occupancies(req: func.HttpRequest) -> func.HttpResponse:
    """Einzelbelegung für die in Appack auf die Rolle TR begrenzte Seite."""
    if req.method.upper() == "OPTIONS":
        return func.HttpResponse(
            status_code=204,
            headers=_occupancy_headers(cache=False),
        )

    try:
        if not special_occupancy_enabled(os.environ):
            raise SpecialOccupancyError(
                "SPECIAL_OCCUPANCY_DISABLED",
                "Manuelle Belegungen sind derzeit nicht aktiviert.",
                status_code=503,
            )
        try:
            body = req.get_json()
        except ValueError as exc:
            raise SpecialOccupancyError(
                "REQUEST_INVALID",
                "Der Request muss gültiges JSON enthalten.",
            ) from exc
        if not isinstance(body, dict):
            raise SpecialOccupancyError(
                "REQUEST_INVALID",
                "Der Request muss ein JSON-Objekt enthalten.",
            )
        action = str(body.get("action") or "create").strip().lower()
        expected_confirmation = {
            "create": "TRAINER_BELEGUNG_SPEICHERN",
            "delete": "TRAINER_BELEGUNG_LOESCHEN",
            "move": "TRAINER_BELEGUNG_VERSCHIEBEN",
        }.get(action)
        if expected_confirmation is None:
            raise SpecialOccupancyError(
                "ACTION_INVALID",
                "action muss create, move oder delete sein.",
            )
        if body.get("confirmation") != expected_confirmation:
            raise SpecialOccupancyError(
                "CONFIRMATION_INVALID",
                "Die Sicherheitsbestätigung fehlt oder ist ungültig.",
            )

        now_utc = datetime.now(timezone.utc)
        store = AzureTableSpecialOccupancyStore.from_environment(os.environ)
        if action == "delete":
            event_id = str(body.get("eventId") or "").removeprefix("one-off:").strip().lower()
            existing = store.get_active(event_id)
            if existing is None:
                raise SpecialOccupancyError("EVENT_NOT_FOUND", "Die Belegung wurde nicht gefunden.", status_code=404)
            requester_id = str(body.get("requesterId") or "").strip()
            is_admin = bool(body.get("isAppAdministrator"))
            is_relocated_training = bool(existing.replaced_training_event_id)
            if (
                not is_relocated_training
                and not is_admin
                and (not requester_id or requester_id != existing.creator_id)
            ):
                raise SpecialOccupancyError("DELETE_FORBIDDEN", "Diese Belegung darf nur vom Ersteller oder App-Administrator gelöscht werden.", status_code=403)
            cancelled_training_event_id = ""
            if is_relocated_training:
                # Zuerst die ursprüngliche Instanz absagen, erst danach den
                # Ersatztermin entfernen. Ein Teilfehler bleibt so fail-closed:
                # Der Platz wird niemals versehentlich vorzeitig freigegeben.
                occurrence = _trainer_training_occurrence(
                    existing.replaced_training_event_id
                )
                delay = int(
                    os.environ.get(
                        "TRAINING_CANCELLATION_RELEASE_DELAY_MINUTES",
                        "30",
                    )
                )
                cancellation_store = AzureTableCancellationStore.from_environment(
                    os.environ
                )
                cancellation_store.cancel(
                    occurrence,
                    now_utc=now_utc,
                    release_delay_minutes=delay,
                )
                cancelled_training_event_id = existing.replaced_training_event_id
            result = store.apply(
                {"commandId": str(body.get("commandId") or ""), "action": "delete", "eventId": event_id},
                now_utc=now_utc,
            )
            if cancelled_training_event_id:
                result["cancelledTrainingEventId"] = cancelled_training_event_id
            LOGGER.warning("SSV53_TRAINER_OCCUPANCY_DELETED event_id=%s", event_id)
            return _trainer_occupancy_response(result, status_code=200)
        if action == "move":
            event, source_id, source_resource = _trainer_move_source(
                body,
                store,
                now_utc=now_utc,
            )
            target_resource = str(body.get("targetResourceId") or "").strip().lower()
            if target_resource not in {"rasen", "kunstrasen"}:
                raise SpecialOccupancyError(
                    "RESOURCE_INVALID",
                    "Der Zielplatz muss Rasen oder Kunstrasen sein.",
                )
            original_start = datetime.fromisoformat(
                str(event["start"]).replace("Z", "+00:00")
            )
            original_end = datetime.fromisoformat(
                str(event["end"]).replace("Z", "+00:00")
            )
            target_start_value = body.get("targetStart")
            target_end_value = body.get("targetEnd")
            if (target_start_value in (None, "")) != (target_end_value in (None, "")):
                raise SpecialOccupancyError(
                    "MOVE_RANGE_INCOMPLETE",
                    "Für eine zeitliche Verlegung müssen Beginn und Ende angegeben werden.",
                )
            try:
                start = datetime.fromisoformat(
                    str(target_start_value or event["start"]).replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    str(target_end_value or event["end"]).replace("Z", "+00:00")
                )
            except (TypeError, ValueError) as exc:
                raise SpecialOccupancyError(
                    "DATETIME_INVALID",
                    "Der neue Terminzeitraum ist ungültig.",
                ) from exc
            if (
                start.tzinfo is None or start.utcoffset() is None
                or end.tzinfo is None or end.utcoffset() is None
                or end <= start
            ):
                raise SpecialOccupancyError("DATETIME_INVALID", "Der Terminzeitraum ist ungültig.")
            now_local = now_utc.astimezone(ZoneInfo("Europe/Berlin"))
            start = start.astimezone(ZoneInfo("Europe/Berlin"))
            end = end.astimezone(ZoneInfo("Europe/Berlin"))
            original_start = original_start.astimezone(ZoneInfo("Europe/Berlin"))
            original_end = original_end.astimezone(ZoneInfo("Europe/Berlin"))
            if original_end <= now_local:
                raise SpecialOccupancyError("EVENT_IN_PAST", "Ein beendeter Termin kann nicht verschoben werden.")
            if target_start_value not in (None, ""):
                if start < now_local - timedelta(minutes=5):
                    raise SpecialOccupancyError(
                        "START_IN_PAST",
                        "Der neue Beginn darf nicht in der Vergangenheit liegen.",
                    )
                if start > now_local + timedelta(days=63):
                    raise SpecialOccupancyError(
                        "START_TOO_FAR_AHEAD",
                        "Ein Training darf höchstens 63 Tage im Voraus verlegt werden.",
                    )
            if end - start > timedelta(days=14):
                raise SpecialOccupancyError(
                    "RANGE_TOO_LONG",
                    "Ein Training darf höchstens 14 Tage dauern.",
                )
            if (
                target_resource == source_resource
                and start == original_start
                and end == original_end
            ):
                raise SpecialOccupancyError(
                    "MOVE_TARGET_UNCHANGED",
                    "Platz und Zeitraum wurden nicht geändert.",
                )
            event["start"] = start.isoformat()
            event["end"] = end.isoformat()
            event["resourceId"] = target_resource
            event["suppressTraining"] = bool(event.get("replacesTrainingEventId"))
            event["mowerBufferBeforeMinutes"] = 30
            event["mowerBufferAfterMinutes"] = 30
            conflicts = _trainer_occupancy_conflicts(
                start=start,
                end=end,
                resource_id=target_resource,
                store=store,
                ignored_event_ids={
                    source_id,
                    str(event["id"]),
                    "one-off:" + str(event["id"]),
                    str(event.get("replacesTrainingEventId") or ""),
                },
            )
            if conflicts and body.get("overlapConfirmation") != "UEBERSCHNEIDUNG_TROTZDEM_SPEICHERN":
                return _trainer_occupancy_response(
                    {
                        "ok": False,
                        "code": "OCCUPANCY_CONFLICT",
                        "error": "Der Zielplatz ist in diesem Zeitraum bereits belegt.",
                        "conflicts": conflicts,
                    },
                    status_code=409,
                )
            command = {
                "commandId": str(body.get("commandId") or ""),
                "action": "upsert",
                "event": event,
            }
            result = store.apply(command, now_utc=now_utc)
            LOGGER.warning(
                "SSV53_TRAINER_OCCUPANCY_MOVED event_id=%s source=%s target=%s",
                event["id"],
                source_resource,
                target_resource,
            )
            return _trainer_occupancy_response(result, status_code=200)
        if action != "create":
            raise SpecialOccupancyError("ACTION_INVALID", "action muss create, move oder delete sein.")
        now_local = now_utc.astimezone(ZoneInfo("Europe/Berlin"))
        try:
            start = datetime.fromisoformat(
                str(body.get("start") or "").replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise SpecialOccupancyError(
                "DATETIME_INVALID",
                "Der Beginn ist ungültig.",
            ) from exc
        if start.tzinfo is None or start.utcoffset() is None:
            raise SpecialOccupancyError(
                "DATETIME_TIMEZONE_REQUIRED",
                "Der Beginn muss eine Zeitzone enthalten.",
            )
        start = start.astimezone(ZoneInfo("Europe/Berlin"))
        if start < now_local - timedelta(minutes=5):
            raise SpecialOccupancyError(
                "START_IN_PAST",
                "Eine neue Belegung darf nicht in der Vergangenheit beginnen.",
            )
        if start > now_local + timedelta(days=63):
            raise SpecialOccupancyError(
                "START_TOO_FAR_AHEAD",
                "Eine neue Belegung darf höchstens 63 Tage im Voraus liegen.",
            )

        try:
            end = datetime.fromisoformat(str(body.get("end") or "").replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise SpecialOccupancyError("DATETIME_INVALID", "Das Ende ist ungültig.") from exc
        if end.tzinfo is None or end.utcoffset() is None:
            raise SpecialOccupancyError("DATETIME_TIMEZONE_REQUIRED", "Das Ende muss eine Zeitzone enthalten.")
        end = end.astimezone(ZoneInfo("Europe/Berlin"))
        resource_id = str(body.get("resourceId") or "").strip().lower()
        conflicts = _trainer_occupancy_conflicts(
            start=start, end=end, resource_id=resource_id, store=store
        )
        if conflicts and body.get("overlapConfirmation") != "UEBERSCHNEIDUNG_TROTZDEM_SPEICHERN":
            return _trainer_occupancy_response(
                {"ok": False, "code": "OCCUPANCY_CONFLICT", "error": "Der Termin überschneidet sich mit einer vorhandenen Belegung.", "conflicts": conflicts},
                status_code=409,
            )

        command = {
            "commandId": str(body.get("commandId") or ""),
            "action": "upsert",
            "event": {
                "id": str(body.get("eventId") or ""),
                "title": body.get("title"),
                "start": body.get("start"),
                "end": body.get("end"),
                "resourceId": body.get("resourceId"),
                "area": body.get("area") or "vorne & hinten",
                "description": body.get("description") or "",
                "creator": body.get("creator") if isinstance(body.get("creator"), dict) else {},
                "suppressTraining": False,
                "mowerBufferBeforeMinutes": 30,
                "mowerBufferAfterMinutes": 30,
            },
        }
        result = store.apply(command, now_utc=now_utc)
        LOGGER.warning(
            "SSV53_TRAINER_OCCUPANCY_CREATED event_id=%s resource=%s",
            command["event"]["id"],
            command["event"]["resourceId"],
        )
        return _trainer_occupancy_response(result, status_code=200)
    except SpecialOccupancyError as exc:
        LOGGER.warning("SSV53_TRAINER_OCCUPANCY_REJECTED code=%s", exc.code)
        return _trainer_occupancy_response(
            {"ok": False, "code": exc.code, "error": str(exc)},
            exc.status_code,
        )
    except Exception:
        LOGGER.exception("SSV53_TRAINER_OCCUPANCY_ERROR")
        return _trainer_occupancy_response(
            {
                "ok": False,
                "code": "TRAINER_OCCUPANCY_INTERNAL_ERROR",
                "error": "Die Belegung konnte nicht sicher gespeichert werden.",
            },
            503,
        )


def _training_cancellation_response(
    payload: dict,
    status_code: int = 200,
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        status_code=status_code,
        mimetype="application/json",
        charset="utf-8",
        headers=_occupancy_headers(cache=False),
    )


@app.route(
    route="training-cancellations",
    methods=["GET", "POST", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ssv53_training_cancellations(req: func.HttpRequest) -> func.HttpResponse:
    """Trainingsabsagen für die in Appack auf die Rolle TR begrenzte Seite."""
    if req.method.upper() == "OPTIONS":
        return func.HttpResponse(
            status_code=204,
            headers=_occupancy_headers(cache=False),
        )

    now_utc = datetime.now(timezone.utc)
    tz = ZoneInfo("Europe/Berlin")
    today = now_utc.astimezone(tz).date()
    start_day = today
    end_day = today + timedelta(days=28)
    season = req.params.get("season") or "Sommer"
    config_path = os.environ.get(
        "OCCUPANCY_CONFIG_PATH",
        "occupancy/config.json",
    )

    try:
        if req.method.upper() == "GET":
            if req.params.get("start"):
                start_day = datetime.fromisoformat(req.params["start"]).date()
            if req.params.get("end"):
                end_day = datetime.fromisoformat(req.params["end"]).date()
            if (
                start_day < today
                or end_day < start_day
                or end_day > today + timedelta(days=63)
            ):
                raise ValueError(
                    "Erlaubt sind heutige und zukünftige Termine innerhalb von 63 Tagen."
                )

        occurrences = build_training_occurrences(
            config_path=config_path,
            start=start_day.isoformat(),
            end=(end_day + timedelta(days=1)).isoformat(),
            season=season,
        )
        now_local = now_utc.astimezone(tz)
        occurrences = [
            item
            for item in occurrences
            if datetime.fromisoformat(item["end"]) > now_local
        ]
        by_id = {item["id"]: item for item in occurrences}
        store = AzureTableCancellationStore.from_environment(os.environ)

        if req.method.upper() == "GET":
            active = {
                item.event_id: item
                for item in store.list_active(start_day, end_day)
            }
            items = []
            for occurrence in occurrences:
                cancellation = active.get(occurrence["id"])
                items.append(
                    {
                        **occurrence,
                        "cancelled": cancellation is not None,
                        "cancelledAtUtc": (
                            cancellation.cancelled_at_utc.isoformat()
                            if cancellation
                            else None
                        ),
                        "mowerReleaseNotBeforeUtc": (
                            cancellation.release_not_before_utc.isoformat()
                            if cancellation
                            else None
                        ),
                    }
                )
            return _training_cancellation_response(
                {"items": items, "season": season}
            )

        try:
            body = req.get_json()
        except ValueError as exc:
            raise ValueError("Der Request muss gültiges JSON enthalten.") from exc
        if not isinstance(body, dict):
            raise ValueError("Der Request muss ein JSON-Objekt enthalten.")
        event_id = str(body.get("eventId") or "")
        action = str(body.get("action") or "")
        occurrence = by_id.get(event_id)
        if occurrence is None:
            raise ValueError(
                "Der Trainingstermin ist nicht vorhanden oder nicht mehr änderbar."
            )

        if (
            action == "cancel"
            and body.get("confirmation") == "TRAINING_FAELLT_AUS"
        ):
            delay = int(
                os.environ.get(
                    "TRAINING_CANCELLATION_RELEASE_DELAY_MINUTES",
                    "30",
                )
            )
            cancellation = store.cancel(
                occurrence,
                now_utc=now_utc,
                release_delay_minutes=delay,
            )
            LOGGER.warning("SSV53_TRAINING_CANCELLED event_id=%s", event_id)
            return _training_cancellation_response(
                {
                    "ok": True,
                    "eventId": event_id,
                    "cancelled": True,
                    "mowerReleaseNotBeforeUtc": (
                        cancellation.release_not_before_utc.isoformat()
                    ),
                }
            )
        if (
            action == "restore"
            and body.get("confirmation") == "TRAINING_WIEDER_AKTIV"
        ):
            restored = store.restore(event_id, now_utc=now_utc)
            LOGGER.warning(
                "SSV53_TRAINING_RESTORED event_id=%s restored=%s",
                event_id,
                restored,
            )
            return _training_cancellation_response(
                {
                    "ok": True,
                    "eventId": event_id,
                    "cancelled": False,
                    "restored": restored,
                }
            )
        raise ValueError("Aktion oder Bestätigung ist ungültig.")
    except ValueError as exc:
        return _training_cancellation_response({"error": str(exc)}, 400)
    except Exception:
        LOGGER.exception("SSV53_TRAINING_CANCELLATION_ERROR")
        return _training_cancellation_response(
            {
                "error": (
                    "Trainingsänderung ist derzeit nicht möglich; "
                    "die Platzsperre bleibt bestehen."
                )
            },
            503,
        )


def _order_mail_response(
    payload: dict,
    *,
    status_code: int = 200,
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        status_code=status_code,
        mimetype="application/json",
        charset="utf-8",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.route(
    route="order-mail",
    methods=["POST", "OPTIONS"],
    auth_level=func.AuthLevel.FUNCTION,
)
def ssv53_order_mail(req: func.HttpRequest) -> func.HttpResponse:
    """Sicherer SMTP-Maildienst für T-Shirt-Bestellungen."""

    if req.method.upper() == "OPTIONS":
        return _order_mail_response({}, status_code=204)

    try:
        body = req.get_json()
    except ValueError:
        return _order_mail_response(
            {
                "ok": False,
                "code": "REQUEST_INVALID",
                "error": "Der Request muss gültiges JSON enthalten.",
            },
            status_code=400,
        )
    if not isinstance(body, dict):
        return _order_mail_response(
            {
                "ok": False,
                "code": "REQUEST_INVALID",
                "error": "Der Request muss ein JSON-Objekt enthalten.",
            },
            status_code=400,
        )

    action = str(body.get("action") or "").strip().lower()
    try:
        if action == "send-collision-test":
            if body.get("confirmation") != "SSV53-COLLISION-TEST":
                return _order_mail_response(
                    {
                        "ok": False,
                        "code": "CONFIRMATION_INVALID",
                        "error": "Die Testmail-Bestätigung ist ungültig.",
                    },
                    status_code=400,
                )
            result = send_collision_test_mail(os.environ)
            LOGGER.info("SSV53_OCCUPANCY_COLLISION_TEST_MAIL_OK sent=%s", result["sent"])
            return _order_mail_response(result)

        if action == "send-real-collision-test":
            if body.get("confirmation") != "SSV53-REAL-COLLISION-TEST":
                return _order_mail_response(
                    {
                        "ok": False,
                        "code": "CONFIRMATION_INVALID",
                        "error": "Die Echtkollisions-Testbestätigung ist ungültig.",
                    },
                    status_code=400,
                )
            result = send_real_collision_test_mail(datetime.now(timezone.utc), os.environ)
            LOGGER.info(
                "SSV53_OCCUPANCY_REAL_COLLISION_TEST_MAIL_OK sent=%s match_id=%s booking_id=%s",
                result["sent"],
                result["matchId"],
                result["bookingId"],
            )
            return _order_mail_response(result)

        if action == "check":
            result = check_smtp_connection(os.environ)
            LOGGER.info("SSV53_ORDER_MAIL_SMTP_CHECK_OK")
            return _order_mail_response(result)

        if action == "send-ready":
            result = send_order_ready_mail(body, os.environ)
            LOGGER.info(
                "SSV53_ORDER_MAIL_REQUEST_OK order_id=%s sent=%s duplicate=%s",
                result.get("orderId"),
                result.get("sent"),
                result.get("alreadySent"),
            )
            return _order_mail_response(result)

        return _order_mail_response(
            {
                "ok": False,
                "code": "ACTION_INVALID",
                "error": "Unbekannte Mail-Aktion.",
            },
            status_code=400,
        )
    except OrderMailError as exc:
        LOGGER.warning(
            "SSV53_ORDER_MAIL_REJECTED code=%s",
            exc.code,
        )
        return _order_mail_response(
            {
                "ok": False,
                "code": exc.code,
                "error": str(exc),
            },
            status_code=exc.status_code,
        )
    except Exception:
        LOGGER.exception("SSV53_ORDER_MAIL_ERROR")
        return _order_mail_response(
            {
                "ok": False,
                "code": "ORDER_MAIL_INTERNAL_ERROR",
                "error": "Der Bestell-Maildienst ist unerwartet fehlgeschlagen.",
            },
            status_code=500,
        )

