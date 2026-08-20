from __future__ import annotations

from typing import Any

from mower.hydrawise import HydrawiseError, _get_json


def _required_text(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HydrawiseError(f"{name} wird für den Hydrawise-Befehl benötigt.")
    return normalized


def _controller_parameters(controller_id: str | int | None) -> dict[str, str | int]:
    if controller_id in (None, ""):
        return {}
    return {"controller_id": str(controller_id).strip()}


def suspend_zone_until(
    api_key: str,
    relay_id: int,
    suspend_until_epoch: int,
    controller_id: str | int | None = None,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    """Suspendiert genau eine geplante Zone bis zu einem UTC-Epochenwert.

    Das wiederholte Senden desselben Befehls ist absichtlich idempotent. Erst
    wenn alle sieben Planläufe suspendiert wurden, darf ein vorgezogener
    manueller Lauf beginnen.
    """

    key = _required_text(api_key, "HYDRAWISE_API_KEY")
    if int(relay_id) <= 0:
        raise HydrawiseError("relay_id muss positiv sein.")
    if int(suspend_until_epoch) <= 0:
        raise HydrawiseError("suspend_until_epoch muss positiv sein.")
    parameters: dict[str, str | int] = {
        "api_key": key,
        "action": "suspend",
        "period_id": 999,
        "custom": int(suspend_until_epoch),
        "relay_id": int(relay_id),
        **_controller_parameters(controller_id),
    }
    return _get_json("setzone.php", parameters, timeout=timeout)


def start_zone_for(
    api_key: str,
    relay_id: int,
    run_seconds: int,
    controller_id: str | int | None = None,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    """Startet genau eine Zone für die aus dem Plan übernommene Laufzeit."""

    key = _required_text(api_key, "HYDRAWISE_API_KEY")
    if int(relay_id) <= 0:
        raise HydrawiseError("relay_id muss positiv sein.")
    if not 60 <= int(run_seconds) <= 7200:
        raise HydrawiseError("run_seconds muss zwischen 60 und 7200 liegen.")
    parameters: dict[str, str | int] = {
        "api_key": key,
        "action": "run",
        "period_id": 999,
        "custom": int(run_seconds),
        "relay_id": int(relay_id),
        **_controller_parameters(controller_id),
    }
    return _get_json("setzone.php", parameters, timeout=timeout)


def stop_zone_now(
    api_key: str,
    relay_id: int,
    controller_id: str | int | None = None,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    """Stoppt genau die aktuell laufende Hydrawise-Zone.

    Die Steuerung darf daraus noch keine Mäherfreigabe ableiten. Erst der
    anschließend fortlaufend bestätigte Live-Status startet den separaten
    Beregnungsnachlauf.
    """

    key = _required_text(api_key, "HYDRAWISE_API_KEY")
    if int(relay_id) <= 0:
        raise HydrawiseError("relay_id muss positiv sein.")
    parameters: dict[str, str | int] = {
        "api_key": key,
        "action": "stop",
        "relay_id": int(relay_id),
        **_controller_parameters(controller_id),
    }
    return _get_json("setzone.php", parameters, timeout=timeout)
