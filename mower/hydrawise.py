from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.hydrawise.com/api/v1"
USER_AGENT = "SSV53-Maehplan-Dry-Run/1.0 (+https://www.ssv53.de)"


class HydrawiseError(RuntimeError):
    pass


@dataclass(frozen=True)
class HydrawiseSafetySnapshot:
    """Fail-closed Sicht auf den aktuellen Beregnungszustand."""

    available: bool
    fresh: bool
    clear_now: bool
    observed_at_utc: str | None
    age_seconds: int | None
    selected_zone_count: int
    observed_relay_ids: tuple[int, ...]
    expected_relay_ids: tuple[int, ...]
    relay_set_valid: bool
    active_zone_count: int
    imminent_zone_count: int
    active_relay_ids: tuple[int, ...]
    imminent_relay_ids: tuple[int, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["observed_relay_ids"] = list(self.observed_relay_ids)
        value["expected_relay_ids"] = list(self.expected_relay_ids)
        value["active_relay_ids"] = list(self.active_relay_ids)
        value["imminent_relay_ids"] = list(self.imminent_relay_ids)
        return value


def parse_relay_id_allowlist(
    value: Any,
    *,
    expected_count: int,
    required: bool,
) -> tuple[int, ...]:
    """Parst die unveränderliche Relay-ID-Freigabeliste fail-closed."""

    text = str(value or "").strip()
    if not text:
        if required:
            raise HydrawiseError("HYDRAWISE_EXPECTED_RELAY_IDS fehlt.")
        return ()
    raw_values = [item.strip() for item in text.split(",")]
    if any(not item for item in raw_values):
        raise HydrawiseError("HYDRAWISE_EXPECTED_RELAY_IDS enthält einen leeren Wert.")
    try:
        relay_ids = tuple(int(item) for item in raw_values)
    except ValueError as exc:
        raise HydrawiseError(
            "HYDRAWISE_EXPECTED_RELAY_IDS darf nur positive Ganzzahlen enthalten."
        ) from exc
    if any(relay_id <= 0 for relay_id in relay_ids):
        raise HydrawiseError(
            "HYDRAWISE_EXPECTED_RELAY_IDS darf nur positive Ganzzahlen enthalten."
        )
    if len(relay_ids) != len(set(relay_ids)):
        raise HydrawiseError("HYDRAWISE_EXPECTED_RELAY_IDS enthält Duplikate.")
    if len(relay_ids) != expected_count:
        raise HydrawiseError(
            "HYDRAWISE_EXPECTED_RELAY_IDS muss exakt "
            f"{expected_count} eindeutige Relay-IDs enthalten."
        )
    return tuple(sorted(relay_ids))


@dataclass(frozen=True)
class HydrawiseContinuousClearSnapshot:
    """Persistently confirmed release after the physical irrigation end."""

    allowed: bool
    physical_clear_now: bool
    persistent_state_available: bool
    required_clear_minutes: int
    clear_since_utc: str | None
    confirmed_for_seconds: int
    release_at_utc: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_continuous_clear_confirmation(
    *,
    available: bool,
    fresh: bool,
    clear_now: bool,
    physical_reason: str,
    clear_since_utc: str | None,
    now_utc: datetime,
    required_clear_minutes: int,
    persistent_state_available: bool,
) -> HydrawiseContinuousClearSnapshot:
    """Release only after an uninterrupted, persistently stored clear period.

    A missing or invalid state is deliberately interpreted fail-closed. The
    clear period starts with the first successful control cycle after the last
    active/imminent irrigation status; it is never backdated from API data.
    """

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss eine zeitzonenbewusste UTC-Zeit sein.")
    if not 1 <= required_clear_minutes <= 1440:
        raise ValueError(
            "required_clear_minutes muss zwischen 1 und 1440 liegen."
        )

    required_seconds = required_clear_minutes * 60
    common = {
        "physical_clear_now": bool(available and fresh and clear_now),
        "persistent_state_available": persistent_state_available,
        "required_clear_minutes": required_clear_minutes,
        "clear_since_utc": clear_since_utc,
        "confirmed_for_seconds": 0,
        "release_at_utc": None,
    }
    if not available or not fresh or not clear_now:
        return HydrawiseContinuousClearSnapshot(
            allowed=False,
            reason=physical_reason,
            **common,
        )
    if not persistent_state_available:
        return HydrawiseContinuousClearSnapshot(
            allowed=False,
            reason=(
                "Die fortlaufende Hydrawise-Freigabe konnte nicht sicher "
                "gespeichert werden."
            ),
            **common,
        )
    if not clear_since_utc:
        return HydrawiseContinuousClearSnapshot(
            allowed=False,
            reason="Die fortlaufende Hydrawise-Freigabe hat noch nicht begonnen.",
            **common,
        )

    try:
        clear_since = datetime.fromisoformat(
            clear_since_utc.replace("Z", "+00:00")
        )
        if clear_since.tzinfo is None or clear_since.utcoffset() is None:
            raise ValueError
        clear_since = clear_since.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return HydrawiseContinuousClearSnapshot(
            allowed=False,
            reason="Die gespeicherte Hydrawise-Freigabezeit ist ungültig.",
            **common,
        )

    now = now_utc.astimezone(timezone.utc)
    if clear_since > now:
        return HydrawiseContinuousClearSnapshot(
            allowed=False,
            reason="Die gespeicherte Hydrawise-Freigabezeit liegt in der Zukunft.",
            **common,
        )

    confirmed_seconds = int((now - clear_since).total_seconds())
    release_at = clear_since + timedelta(minutes=required_clear_minutes)
    confirmed = confirmed_seconds >= required_seconds
    if confirmed:
        reason = (
            "Hydrawise hat das Beregnungsende fortlaufend für "
            f"mindestens {required_clear_minutes} Minuten bestätigt."
        )
    else:
        reason = (
            "Hydrawise meldet erst seit "
            f"{confirmed_seconds / 60:.1f} Minuten fortlaufend frei; benötigt "
            f"werden {required_clear_minutes} Minuten."
        )
    return HydrawiseContinuousClearSnapshot(
        allowed=confirmed,
        physical_clear_now=True,
        persistent_state_available=True,
        required_clear_minutes=required_clear_minutes,
        clear_since_utc=clear_since.isoformat(),
        confirmed_for_seconds=confirmed_seconds,
        release_at_utc=release_at.isoformat(),
        reason=reason,
    )


def _relay_selected(
    relay: dict[str, Any],
    hydrawise_config: dict[str, Any],
) -> bool:
    include_all = bool(hydrawise_config.get("include_all_zones", False))
    relay_ids = {
        int(value)
        for value in hydrawise_config.get("relay_ids", [])
    }
    patterns = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in hydrawise_config.get("zone_name_patterns", [])
    ]
    relay_id = int(relay.get("relay_id", -1))
    name = str(relay.get("name", f"Zone {relay.get('relay', '?')}"))
    return (
        include_all
        or relay_id in relay_ids
        or any(pattern.search(name) for pattern in patterns)
    )


def evaluate_safety_status(
    status: dict[str, Any] | None,
    hydrawise_config: dict[str, Any],
    *,
    now_utc: datetime,
    max_age_seconds: int = 180,
) -> HydrawiseSafetySnapshot:
    """Bewertet Hydrawise konservativ als Startfreigabe oder Sperre.

    ``clear_now`` ist nur eine Momentaufnahme. Die produktive Steuerung muss
    zusätzlich mehrere aufeinanderfolgende klare Abrufe persistent bestätigen.
    """

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss eine zeitzonenbewusste UTC-Zeit sein.")
    if not 30 <= max_age_seconds <= 900:
        raise ValueError("max_age_seconds muss zwischen 30 und 900 liegen.")
    expected_relay_ids = tuple(
        sorted(
            int(value)
            for value in hydrawise_config.get("expected_relay_ids", [])
        )
    )
    if not bool(hydrawise_config.get("enabled", True)):
        return HydrawiseSafetySnapshot(
            available=False,
            fresh=False,
            clear_now=False,
            observed_at_utc=None,
            age_seconds=None,
            selected_zone_count=0,
            observed_relay_ids=(),
            expected_relay_ids=expected_relay_ids,
            relay_set_valid=False,
            active_zone_count=0,
            imminent_zone_count=0,
            active_relay_ids=(),
            imminent_relay_ids=(),
            reason="Hydrawise ist in der Laufzeitkonfiguration deaktiviert.",
        )
    if not isinstance(status, dict):
        return HydrawiseSafetySnapshot(
            available=False,
            fresh=False,
            clear_now=False,
            observed_at_utc=None,
            age_seconds=None,
            selected_zone_count=0,
            observed_relay_ids=(),
            expected_relay_ids=expected_relay_ids,
            relay_set_valid=False,
            active_zone_count=0,
            imminent_zone_count=0,
            active_relay_ids=(),
            imminent_relay_ids=(),
            reason="Hydrawise lieferte keinen verwertbaren Live-Status.",
        )

    try:
        observed = datetime.fromtimestamp(
            int(status["time"]),
            tz=timezone.utc,
        )
    except (KeyError, TypeError, ValueError, OSError):
        observed = None

    age_seconds = None
    fresh = False
    if observed is not None:
        age_seconds = int(
            (now_utc.astimezone(timezone.utc) - observed).total_seconds()
        )
        fresh = -60 <= age_seconds <= max_age_seconds

    observed_relay_ids: list[int] = []
    selected: list[dict[str, Any]] = []
    for raw_relay in status.get("relays", []):
        if not isinstance(raw_relay, dict):
            continue
        try:
            observed_relay_ids.append(int(raw_relay.get("relay_id", -1)))
        except (TypeError, ValueError):
            observed_relay_ids.append(-1)
        if _relay_selected(raw_relay, hydrawise_config):
            selected.append(raw_relay)

    observed_relay_set = tuple(sorted(observed_relay_ids))
    relay_set_valid = (
        not expected_relay_ids
        or (
            len(observed_relay_set) == len(set(observed_relay_set))
            and observed_relay_set == expected_relay_ids
        )
    )

    before_seconds = max(
        60,
        int(hydrawise_config.get("before_minutes", 30)) * 60,
    )
    active_ids: list[int] = []
    imminent_ids: list[int] = []
    for relay in selected:
        relay_id = int(relay.get("relay_id", -1))
        try:
            seconds_until = int(float(relay.get("time", 0) or 0))
            run_seconds = int(float(relay.get("run", 0) or 0))
        except (TypeError, ValueError):
            # Ein unverständlicher ausgewählter Zonenstatus darf niemals als
            # bestätigte Freigabe interpretiert werden.
            imminent_ids.append(relay_id)
            continue
        if run_seconds <= 0 or seconds_until <= 0:
            continue
        if seconds_until == 1:
            active_ids.append(relay_id)
        elif seconds_until <= before_seconds:
            imminent_ids.append(relay_id)

    available = observed is not None and bool(selected)
    clear_now = (
        available
        and fresh
        and relay_set_valid
        and not active_ids
        and not imminent_ids
    )
    if observed is None:
        reason = "Hydrawise-Status enthält keinen gültigen Beobachtungszeitpunkt."
    elif not selected:
        reason = "Hydrawise lieferte keine ausgewählte Beregnungszone."
    elif not fresh:
        reason = (
            f"Hydrawise-Status ist nicht frisch genug ({age_seconds} Sekunden)."
        )
    elif not relay_set_valid:
        reason = (
            "Die von Hydrawise gemeldeten Relay-IDs entsprechen nicht exakt "
            "der freigegebenen Sieben-Zonen-Liste."
        )
    elif active_ids:
        reason = "Mindestens eine Hydrawise-Zone läuft aktuell."
    elif imminent_ids:
        reason = "Mindestens eine Hydrawise-Zone steht innerhalb des Schutzvorlaufs an."
    else:
        reason = "Hydrawise meldet alle ausgewählten Zonen aktuell frei."

    return HydrawiseSafetySnapshot(
        available=available,
        fresh=fresh,
        clear_now=clear_now,
        observed_at_utc=(observed.isoformat() if observed is not None else None),
        age_seconds=age_seconds,
        selected_zone_count=len(selected),
        observed_relay_ids=observed_relay_set,
        expected_relay_ids=expected_relay_ids,
        relay_set_valid=relay_set_valid,
        active_zone_count=len(active_ids),
        imminent_zone_count=len(imminent_ids),
        active_relay_ids=tuple(sorted(active_ids)),
        imminent_relay_ids=tuple(sorted(imminent_ids)),
        reason=reason,
    )


def selected_zone_schedule(
    status: dict[str, Any] | None,
    hydrawise_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Liefert ausschließlich den geprüften, nicht geheimen Zonenfahrplan.

    ``time == 1`` kennzeichnet eine bereits laufende Zone. Für einen
    zukünftigen Lauf werden Start und Ende aus demselben Hydrawise-Snapshot
    berechnet, damit die sieben Laufzeiten später unverändert vorgezogen
    werden können.
    """

    if not isinstance(status, dict):
        return []
    try:
        observed = datetime.fromtimestamp(int(status["time"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OSError):
        return []

    zones: list[dict[str, Any]] = []
    for raw_relay in status.get("relays", []):
        if not isinstance(raw_relay, dict) or not _relay_selected(
            raw_relay,
            hydrawise_config,
        ):
            continue
        try:
            relay_id = int(raw_relay["relay_id"])
            zone_number = int(raw_relay["relay"])
            seconds_until = int(float(raw_relay.get("time", 0) or 0))
            run_seconds = int(float(raw_relay.get("run", 0) or 0))
        except (KeyError, TypeError, ValueError):
            continue
        if relay_id <= 0 or zone_number <= 0 or seconds_until <= 0 or run_seconds <= 0:
            continue
        running = seconds_until == 1
        start = observed if running else observed + timedelta(seconds=seconds_until)
        zones.append(
            {
                "relay_id": relay_id,
                "zone": zone_number,
                "name": str(raw_relay.get("name") or f"Zone {zone_number}"),
                "running": running,
                "seconds_until": seconds_until,
                "run_seconds": run_seconds,
                "scheduled_start_utc": start.isoformat(),
                "scheduled_end_utc": (start + timedelta(seconds=run_seconds)).isoformat(),
            }
        )
    return sorted(
        zones,
        key=lambda item: (
            str(item["scheduled_start_utc"]),
            int(item["zone"]),
        ),
    )


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
