from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mower.husqvarna import MOWERS_URL, USER_AGENT, HusqvarnaError, get_access_token


def set_work_area_cutting_height(
    client_id: str,
    client_secret: str,
    mower_id: str,
    work_area_id: int,
    cutting_height_percent: int,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    """Changes only the cutting height of one explicitly identified work area."""

    client_id = client_id.strip()
    client_secret = client_secret.strip()
    mower_id = mower_id.strip()
    area_id = int(work_area_id)
    height = int(cutting_height_percent)
    if not client_id or not client_secret or not mower_id:
        raise HusqvarnaError("Client-ID, Client-Secret und mower_id werden benötigt.")
    if area_id <= 0:
        raise HusqvarnaError("work_area_id muss positiv sein.")
    if not 0 <= height <= 100:
        raise HusqvarnaError("cutting_height_percent muss zwischen 0 und 100 liegen.")

    token = get_access_token(client_id, client_secret, timeout=timeout)
    payload = json.dumps(
        {
            "data": {
                "type": "workArea",
                "id": area_id,
                "attributes": {"cuttingHeight": height},
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        f"{MOWERS_URL}{mower_id}/workAreas/{area_id}",
        data=payload,
        method="PATCH",
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
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8").strip()
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HusqvarnaError(
            "Husqvarna-Schnitthöhe konnte nicht geändert werden: "
            f"HTTP {exc.code}. Antwort: {body[:500]}"
        ) from exc
    except URLError as exc:
        raise HusqvarnaError(
            f"Husqvarna-Schnitthöhe konnte nicht geändert werden: {exc.reason}"
        ) from exc

    if not body:
        return {"status_code": status, "accepted": True}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"status_code": status, "accepted": True, "body": body[:500]}
    return parsed if isinstance(parsed, dict) else {"status_code": status, "accepted": True}
