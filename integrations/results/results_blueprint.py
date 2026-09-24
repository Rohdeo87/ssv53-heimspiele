"""Additive Azure Functions v2 blueprint. Register in the EXISTING app.

This is NOT a replacement for the existing function_app.py.
HTTP reads only cached JSON; only the timer contacts the sports websites.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from ssv_results.collector import Fetcher, SPORTS, collect

bp = func.Blueprint()
logger = logging.getLogger("ssv53.results")


@lru_cache(maxsize=1)
def _container():
    account = os.environ.get("SSV_RESULTS_STORAGE_ACCOUNT_URL", "").rstrip("/")
    name = os.environ.get("SSV_RESULTS_CONTAINER", "ssv53-results")
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net", account):
        raise RuntimeError("SSV_RESULTS_STORAGE_ACCOUNT_URL fehlt oder ist ungültig.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", name):
        raise RuntimeError("Ungültiger Ergebnis-Containername.")
    credential = DefaultAzureCredential(
        exclude_interactive_browser_credential=True,
        managed_identity_client_id=os.environ.get("SSV_RESULTS_MANAGED_IDENTITY_CLIENT_ID") or None,
    )
    return BlobServiceClient(account, credential=credential).get_container_client(name)


def _load(sport: str) -> dict | None:
    try:
        downloader = _container().download_blob(f"{sport}.json", max_concurrency=1)
        if downloader.size > 2_500_000:
            raise RuntimeError("Cache-Datei zu groß.")
        data = json.loads(downloader.readall())
        if data.get("schemaVersion") != 1 or data.get("sport") != sport:
            raise RuntimeError("Cache-Schema stimmt nicht überein.")
        return data
    except ResourceNotFoundError:
        return None


def _save(sport: str, value: dict) -> None:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > 2_500_000:
        raise RuntimeError("Ergebnisdaten überschreiten das Größenlimit.")
    _container().upload_blob(
        f"{sport}.json", body, overwrite=True,
        content_settings=ContentSettings(content_type="application/json; charset=utf-8", cache_control="no-cache"),
    )


def _headers() -> dict[str, str]:
    # Only public team data; no cookies or authentication secrets are accepted.
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "If-None-Match",
        "Access-Control-Expose-Headers": "ETag",
        "Access-Control-Max-Age": "3600",
        "Cache-Control": "public, max-age=60",
        "X-Content-Type-Options": "nosniff",
    }


@bp.route(route="ssv-results", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def ssv53_results_read(req: func.HttpRequest) -> func.HttpResponse:
    headers = _headers()
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=headers)
    sport = req.params.get("sport", "")
    if sport not in SPORTS:
        return func.HttpResponse('{"error":"sport muss handball oder volleyball sein."}',
                                 status_code=400, mimetype="application/json", headers=headers)
    try:
        payload = _load(sport)
        if payload is None:
            headers["Cache-Control"] = "no-store"
            return func.HttpResponse('{"error":"Die erste Datenübernahme steht noch aus."}',
                                     status_code=503, mimetype="application/json", headers=headers)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'
        headers["ETag"] = etag
        if req.headers.get("If-None-Match") == etag:
            return func.HttpResponse(status_code=304, headers=headers)
        return func.HttpResponse(body, mimetype="application/json", charset="utf-8", headers=headers)
    except Exception as exc:
        # Do not disclose storage configuration, credentials or raw upstream data.
        logger.warning("Results cache unavailable: %s", type(exc).__name__)
        headers["Cache-Control"] = "no-store"
        return func.HttpResponse('{"error":"Ergebnisdienst vorübergehend nicht verfügbar."}',
                                 status_code=503, mimetype="application/json", headers=headers)


@bp.timer_trigger(schedule="0 17 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def ssv53_results_update(timer: func.TimerRequest) -> None:
    # Isolated feature switch. No changes to mower, irrigation or occupancy flags.
    if os.environ.get("SSV_RESULTS_ENABLED", "false").lower() != "true":
        return
    for sport in SPORTS:
        try:
            old = _load(sport)  # Do not fetch upstream if the cache cannot be read.
            next_attempt = (old or {}).get("nextAttemptAt")
            if next_attempt and datetime.fromisoformat(next_attempt.replace("Z", "+00:00")) > datetime.now(timezone.utc):
                continue
            with_agent = os.environ.get("SSV_RESULTS_USER_AGENT", "SSV53Results/1.0")
            fetcher = Fetcher(with_agent)
            try:
                override = os.environ.get("SSV_RESULTS_SEASON_START", "")
                season_start = int(override) if override else None
                payload = collect(sport, fetcher, old, season_start=season_start)
                _save(sport, payload)
                logger.info("Results update sport=%s stale=%s requests=%s", sport, payload["stale"], fetcher.request_count)
            finally:
                fetcher.close()
        except Exception as exc:
            # One sport cannot overwrite the other sport or empty a healthy cache.
            logger.warning("Results update failed sport=%s error=%s", sport, type(exc).__name__)
