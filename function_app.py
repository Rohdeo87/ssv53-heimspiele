from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import azure.functions as func

from mower.controller import run_control_cycle
from mower.irrigation_recovery import (
    IrrigationRecoveryError,
    reset_failed_irrigation,
)
from occupancy.service import build_occupancy_payload, build_training_occurrences
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

def _occupancy_matches_path() -> tuple[str, str]:
    """Öffentliche Belegung aus dem gemeinsamen strukturierten Matchmodell.

    Die dynamische Mäherkonfiguration bleibt davon getrennt und verwendet
    weiterhin ausschließlich den fail-closed geprüften Rasen-ICS-Feed.
    """
    configured = os.environ.get(
        "OCCUPANCY_MATCHES_PATH",
        "public/matches.json",
    ).strip()
    return configured or "public/matches.json", "structured_matches"


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
        matches_path, match_source = _occupancy_matches_path()
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
        if body.get("confirmation") != "TRAINER_BELEGUNG_SPEICHERN":
            raise SpecialOccupancyError(
                "CONFIRMATION_INVALID",
                "Die Sicherheitsbestätigung fehlt oder ist ungültig.",
            )

        now_utc = datetime.now(timezone.utc)
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
                "suppressTraining": False,
                "mowerBufferBeforeMinutes": 30,
                "mowerBufferAfterMinutes": 30,
            },
        }
        store = AzureTableSpecialOccupancyStore.from_environment(os.environ)
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

