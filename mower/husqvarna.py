from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


AUTH_URL = "https://api.authentication.husqvarnagroup.dev/v1/oauth2/token"
MOWERS_URL = "https://api.amc.husqvarna.dev/v1/mowers/"
USER_AGENT = "SSV53-Azure-Dry-Run/1.0"
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}


class HusqvarnaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MowerSnapshot:
    mower_id: str
    name: str | None
    model: str | None
    battery_percent: int
    activity: str
    state: str
    mode: str
    error_code: int
    override_action: str
    restricted_reason: str
    external_reason_id: int | None
    next_start_timestamp_ms: int | None
    work_areas: tuple[dict[str, Any], ...]
    connected: bool | None = None
    status_timestamp_ms: int | None = None
    global_cutting_height_percent: int | None = None
    statistics: dict[str, int | None] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["work_areas"] = list(self.work_areas)
        value["statistics"] = dict(self.statistics or {})
        return value


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _request_json(
    request: Request,
    label: str,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HusqvarnaError(
            f"{label} fehlgeschlagen: HTTP {exc.code}. Antwort: {body[:500]}"
        ) from exc
    except URLError as exc:
        raise HusqvarnaError(f"{label} fehlgeschlagen: {exc.reason}") from exc

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HusqvarnaError(
            f"{label} lieferte keine gültige JSON-Antwort."
        ) from exc
    if not isinstance(parsed, dict):
        raise HusqvarnaError(
            f"{label} lieferte ein unerwartetes Datenformat."
        )
    return parsed


def get_access_token(
    client_id: str,
    client_secret: str,
    *,
    timeout: int = 30,
) -> str:
    """Teilt ein kurzlebiges Token zwischen Statusabruf und Folgeaktion."""

    normalized_id = client_id.strip()
    normalized_secret = client_secret.strip()
    if not normalized_id or not normalized_secret:
        raise HusqvarnaError(
            "HUSQVARNA_CLIENT_ID und HUSQVARNA_CLIENT_SECRET werden benötigt."
        )
    cache_key = (normalized_id, normalized_secret)
    now = time.monotonic()
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached is not None and now < cached[1]:
            return cached[0]

        token_body = urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": normalized_id,
                "client_secret": normalized_secret,
            }
        ).encode("utf-8")
        token_request = Request(
            AUTH_URL,
            data=token_body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
        )
        token_data = _request_json(
            token_request,
            "Husqvarna-Anmeldung",
            timeout=timeout,
        )
        token = str(token_data.get("access_token", "")).strip()
        if not token:
            raise HusqvarnaError("Husqvarna lieferte kein Zugriffstoken.")
        try:
            expires_in = int(token_data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        usable_seconds = max(1, min(expires_in - 60, 3300))
        _TOKEN_CACHE[cache_key] = (token, now + usable_seconds)
        return token


def clear_access_token_cache() -> None:
    """Leert den nur im Arbeitsspeicher gehaltenen Token-Cache."""

    with _TOKEN_LOCK:
        _TOKEN_CACHE.clear()


def fetch_mowers(
    client_id: str,
    client_secret: str,
    *,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Liest Mäherdaten. Diese Phase enthält bewusst keine Aktions-Endpunkte."""

    token = get_access_token(client_id, client_secret, timeout=timeout)

    mower_request = Request(
        MOWERS_URL,
        method="GET",
        headers={
            "Accept": "application/vnd.api+json",
            "Authorization": f"Bearer {token}",
            "Authorization-Provider": "husqvarna",
            "Content-Type": "application/vnd.api+json",
            "X-Api-Key": client_id,
            "User-Agent": USER_AGENT,
        },
    )
    mower_data = _request_json(
        mower_request,
        "Husqvarna-Mäherabruf",
        timeout=timeout,
    )
    items = mower_data.get("data", [])
    if not isinstance(items, list):
        raise HusqvarnaError("Husqvarna lieferte keine gültige Mäherliste.")
    return [item for item in items if isinstance(item, dict)]


def select_mower(
    items: list[dict[str, Any]],
    *,
    preferred_name: str = "Schaf",
    model_fragment: str = "580 EPOS",
) -> dict[str, Any]:
    if len(items) == 1:
        return items[0]

    name_target = preferred_name.casefold()
    model_target = model_fragment.casefold()
    matches: list[dict[str, Any]] = []
    for item in items:
        system = as_dict(as_dict(item.get("attributes")).get("system"))
        name = str(system.get("name", "")).casefold()
        model = str(system.get("model", "")).casefold()
        if name == name_target or model_target in model:
            matches.append(item)

    if len(matches) == 1:
        return matches[0]
    raise HusqvarnaError(
        "Der Mäher konnte nicht eindeutig ausgewählt werden. "
        f"Gefundene Mäher: {len(items)}, passende Mäher: {len(matches)}."
    )


def _parse_external_reason(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_work_areas(raw: Any) -> tuple[dict[str, Any], ...]:
    candidates: list[Any]
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict) and isinstance(raw.get("workAreas"), list):
        candidates = raw["workAreas"]
    else:
        candidates = []

    result: list[dict[str, Any]] = []
    for area in candidates:
        if not isinstance(area, dict):
            continue
        area_attributes = as_dict(area.get("attributes"))
        source = area_attributes or area
        result.append(
            {
                "id": area.get("id", source.get("workAreaId")),
                "name": source.get("name"),
                "enabled": source.get(
                    "enable",
                    source.get("enabled"),
                ),
                "type": str(source.get("type") or "").upper() or None,
                "progress": _parse_external_reason(source.get("progress")),
                "last_time_completed": _parse_external_reason(
                    source.get("lastTimeCompleted")
                ),
                "last_time_abandoned": _parse_external_reason(
                    source.get("lastTimeAbandoned")
                ),
                "cutting_height_percent": _parse_external_reason(
                    source.get("cuttingHeight")
                ),
                "use_global_cutting_height": (
                    bool(source.get("useGlobalCuttingHeight"))
                    if isinstance(source.get("useGlobalCuttingHeight"), bool)
                    else None
                ),
            }
        )
    return tuple(result)


def _parse_statistics(raw: Any) -> dict[str, int | None]:
    source = as_dict(raw)
    fields = {
        "cutting_blade_usage_seconds": "cuttingBladeUsageTime",
        "charging_cycles": "numberOfChargingCycles",
        "collisions": "numberOfCollisions",
        "total_charging_seconds": "totalChargingTime",
        "total_cutting_seconds": "totalCuttingTime",
        "total_drive_metres": "totalDriveDistance",
        "total_running_seconds": "totalRunningTime",
        "total_searching_seconds": "totalSearchingTime",
    }
    return {
        target: _parse_external_reason(source.get(api_name))
        for target, api_name in fields.items()
    }


def parse_snapshot(item: dict[str, Any]) -> MowerSnapshot:
    attributes = as_dict(item.get("attributes"))
    system = as_dict(attributes.get("system"))
    battery_data = as_dict(attributes.get("battery"))
    mower_data = as_dict(attributes.get("mower"))
    planner_data = as_dict(attributes.get("planner"))
    metadata = as_dict(attributes.get("metadata"))
    settings_data = as_dict(attributes.get("settings"))
    override_data = as_dict(planner_data.get("override"))

    return MowerSnapshot(
        mower_id=str(item.get("id") or "").strip(),
        name=system.get("name"),
        model=system.get("model"),
        battery_percent=int(battery_data.get("batteryPercent") or 0),
        activity=str(mower_data.get("activity") or "UNKNOWN").upper(),
        state=str(mower_data.get("state") or "UNKNOWN").upper(),
        mode=str(mower_data.get("mode") or "UNKNOWN").upper(),
        error_code=int(mower_data.get("errorCode") or 0),
        override_action=str(
            override_data.get("action") or "NOT_ACTIVE"
        ).upper(),
        restricted_reason=str(
            planner_data.get("restrictedReason") or "NONE"
        ).upper(),
        external_reason_id=_parse_external_reason(
            planner_data.get("externalReason")
        ),
        next_start_timestamp_ms=_parse_timestamp(
            planner_data.get("nextStartTimestamp")
        ),
        work_areas=_parse_work_areas(attributes.get("workAreas")),
        connected=(
            bool(metadata.get("connected"))
            if isinstance(metadata.get("connected"), bool)
            else None
        ),
        status_timestamp_ms=_parse_timestamp(metadata.get("statusTimestamp")),
        global_cutting_height_percent=_parse_external_reason(
            settings_data.get("cuttingHeight")
        ),
        statistics=_parse_statistics(attributes.get("statistics")),
    )
