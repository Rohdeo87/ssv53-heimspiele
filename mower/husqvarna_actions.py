from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mower.husqvarna import (
    AUTH_URL,
    MOWERS_URL,
    USER_AGENT,
    HusqvarnaError,
    _request_json,
)
def park_until_further_notice(
    client_id: str,
    client_secret: str,
    mower_id: str,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    """Sendet ausschließlich ParkUntilFurtherNotice; keine Startfunktion existiert."""

    client_id = client_id.strip()
    client_secret = client_secret.strip()
    mower_id = mower_id.strip()
    if not client_id or not client_secret or not mower_id:
        raise HusqvarnaError("Client-ID, Client-Secret und mower_id werden benötigt.")

    token_body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
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
        "Husqvarna-Anmeldung für Parkbefehl",
        timeout=timeout,
    )
    token = str(token_data.get("access_token", "")).strip()
    if not token:
        raise HusqvarnaError("Husqvarna lieferte kein Zugriffstoken.")

    payload = json.dumps(
        {"data": {"type": "ParkUntilFurtherNotice"}},
        separators=(",", ":"),
    ).encode("utf-8")
    action_request = Request(
        f"{MOWERS_URL}{mower_id}/actions",
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.api+json",
            "Authorization": f"Bearer {token}",
            "Authorization-Provider": "husqvarna",
            "Content-Type": "application/vnd.api+json",
            "X-Api-Key": client_id,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(action_request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8").strip()
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HusqvarnaError(
            f"Husqvarna-Parkbefehl fehlgeschlagen: HTTP {exc.code}. Antwort: {body[:500]}"
        ) from exc
    except URLError as exc:
        raise HusqvarnaError(
            f"Husqvarna-Parkbefehl fehlgeschlagen: {exc.reason}"
        ) from exc

    if not body:
        return {"status_code": status, "accepted": True}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"status_code": status, "accepted": True, "body": body[:500]}
    return parsed if isinstance(parsed, dict) else {"status_code": status, "accepted": True}
