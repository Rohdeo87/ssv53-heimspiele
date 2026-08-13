from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mower.husqvarna import (
    MOWERS_URL,
    USER_AGENT,
    HusqvarnaError,
    get_access_token,
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

    token = get_access_token(client_id, client_secret, timeout=timeout)

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
