from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.hydrawise.com/api/v1"
USER_AGENT = "SSV53-Maehplan-Dry-Run/1.0 (+https://www.ssv53.de)"


class HydrawiseError(RuntimeError):
    pass


def _get_json(endpoint: str, parameters: dict[str, str | int], timeout: int = 20) -> dict[str, Any]:
    url = f"{API_BASE}/{endpoint}?{urlencode(parameters)}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - feste HTTPS-Domain
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        if exc.code == 429:
            raise HydrawiseError("Hydrawise-Rate-Limit erreicht (HTTP 429).") from exc
        raise HydrawiseError(f"Hydrawise antwortete mit HTTP {exc.code}.") from exc
    except URLError as exc:
        raise HydrawiseError(f"Hydrawise ist nicht erreichbar: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HydrawiseError("Hydrawise lieferte keine gültige JSON-Antwort.") from exc
    if not isinstance(data, dict):
        raise HydrawiseError("Hydrawise lieferte ein unerwartetes Datenformat.")
    if data.get("message_type") == "error":
        raise HydrawiseError(str(data.get("message", "Unbekannter Hydrawise-Fehler")))
    return data


def fetch_status(api_key: str, controller_id: str | int | None = None, timeout: int = 20) -> dict[str, Any]:
    parameters: dict[str, str | int] = {"api_key": api_key}
    if controller_id not in (None, ""):
        parameters["controller_id"] = controller_id
    return _get_json("statusschedule.php", parameters, timeout=timeout)


def fetch_controllers(api_key: str, timeout: int = 20) -> dict[str, Any]:
    return _get_json("customerdetails.php", {"api_key": api_key}, timeout=timeout)
