from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from azure.core.exceptions import AzureError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient


@dataclass(frozen=True)
class OccupancyMatchSource:
    matches_path: str
    source_kind: str
    manifest_etag: str | None = None
    published_at_utc: str | None = None
    source_generated_at_utc: str | None = None
    source_commit: str | None = None
    fallback_used: bool = False


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _parse_utc(value: str, label: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} muss eine Zeitzone enthalten.")
    return parsed.astimezone(timezone.utc)


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _validate_age(value: datetime, now_utc: datetime, max_age_minutes: int) -> None:
    age_seconds = (now_utc - value).total_seconds()
    if age_seconds < -300:
        raise ValueError("Der Spielplan liegt unzulässig in der Zukunft.")
    if age_seconds > max_age_minutes * 60:
        raise ValueError("Der Spielplan ist älter als das zulässige Maximalalter.")


def _safe_blob_path(value: Any, label: str) -> str:
    path = str(value or "").strip()
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise ValueError(f"Unsicherer oder fehlender Blob-Pfad in {label}.")
    return path


def _validate_feed(data: bytes, *, expected_generated_at: datetime) -> None:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schemaVersion", 0)) != 2:
        raise ValueError("Der dynamische Belegungsplan besitzt nicht Schema 2.")
    if payload.get("status") != "ok" or not isinstance(payload.get("matches"), list):
        raise ValueError("Der dynamische Belegungsplan ist nicht freigegeben.")
    generated_at = _parse_utc(str(payload.get("generatedAt") or ""), "generatedAt")
    if generated_at != expected_generated_at:
        raise ValueError("Manifest und dynamischer Belegungsplan widersprechen sich.")
    ids = [
        str(item.get("id") or "").strip()
        for item in payload["matches"]
        if isinstance(item, dict)
    ]
    if len(ids) != len(payload["matches"]) or any(not value for value in ids):
        raise ValueError("Der dynamische Belegungsplan enthält ungültige Spiele.")
    if len(ids) != len(set(ids)):
        raise ValueError("Der dynamische Belegungsplan enthält doppelte Spiele.")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _cache_paths(cache_dir: Path) -> tuple[Path, Path]:
    return cache_dir / "matches.json", cache_dir / "metadata.json"


def _load_cache(
    *,
    cache_dir: Path,
    now_utc: datetime,
    max_age_minutes: int,
    expected_etag: str | None = None,
) -> OccupancyMatchSource | None:
    matches_path, metadata_path = _cache_paths(cache_dir)
    if not (matches_path.is_file() and metadata_path.is_file()):
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if expected_etag is not None and metadata.get("manifest_etag") != expected_etag:
            return None
        published_at = _parse_utc(
            str(metadata["published_at_utc"]),
            "published_at_utc",
        )
        source_generated_at = _parse_utc(
            str(metadata["source_generated_at_utc"]),
            "source_generated_at_utc",
        )
        _validate_age(source_generated_at, now_utc, max_age_minutes)
        data = matches_path.read_bytes()
        if _hash(data) != metadata.get("occupancy_matches_sha256"):
            return None
        _validate_feed(data, expected_generated_at=source_generated_at)
        return OccupancyMatchSource(
            matches_path=str(matches_path),
            source_kind="azure_blob_cache",
            manifest_etag=metadata.get("manifest_etag"),
            published_at_utc=published_at.isoformat(),
            source_generated_at_utc=source_generated_at.isoformat(),
            source_commit=metadata.get("source_commit"),
            fallback_used=True,
        )
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _download_current(
    *,
    container_client: Any,
    cache_dir: Path,
    now_utc: datetime,
    max_age_minutes: int,
) -> OccupancyMatchSource:
    manifest_blob = container_client.get_blob_client("current/manifest.json")
    properties = manifest_blob.get_blob_properties()
    etag = str(getattr(properties, "etag", "") or "")
    cached = _load_cache(
        cache_dir=cache_dir,
        now_utc=now_utc,
        max_age_minutes=max_age_minutes,
        expected_etag=etag or None,
    )
    if cached is not None:
        return OccupancyMatchSource(
            **{**cached.__dict__, "fallback_used": False}
        )

    manifest = json.loads(manifest_blob.download_blob().readall().decode("utf-8"))
    if not isinstance(manifest, dict) or int(manifest.get("schema_version", 0)) != 1:
        raise ValueError("Unbekannte Runtime-Manifest-Version.")
    matches_blob = _safe_blob_path(
        manifest.get("occupancy_matches_blob"),
        "occupancy_matches_blob",
    )
    expected_sha = str(manifest.get("occupancy_matches_sha256") or "").strip()
    if not expected_sha:
        raise ValueError("occupancy_matches_sha256 fehlt im Runtime-Manifest.")
    published_at = _parse_utc(
        str(manifest.get("published_at_utc") or ""),
        "published_at_utc",
    )
    source_generated_at = _parse_utc(
        str(manifest.get("source_generated_at_utc") or ""),
        "source_generated_at_utc",
    )
    _validate_age(source_generated_at, now_utc, max_age_minutes)

    data = container_client.get_blob_client(matches_blob).download_blob().readall()
    if _hash(data) != expected_sha:
        raise ValueError("SHA256 des dynamischen Belegungsplans stimmt nicht.")
    _validate_feed(data, expected_generated_at=source_generated_at)

    matches_path, metadata_path = _cache_paths(cache_dir)
    _atomic_write(matches_path, data)
    metadata = {
        "manifest_etag": etag or None,
        "published_at_utc": published_at.isoformat(),
        "source_generated_at_utc": source_generated_at.isoformat(),
        "source_commit": str(manifest.get("source_commit") or "") or None,
        "occupancy_matches_sha256": expected_sha,
    }
    _atomic_write(
        metadata_path,
        (json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return OccupancyMatchSource(
        matches_path=str(matches_path),
        source_kind="azure_blob",
        manifest_etag=etag or None,
        published_at_utc=published_at.isoformat(),
        source_generated_at_utc=source_generated_at.isoformat(),
        source_commit=metadata["source_commit"],
        fallback_used=False,
    )


def resolve_occupancy_match_source(
    environment: Mapping[str, str],
    *,
    now_utc: datetime,
    service_client_factory: Callable[..., Any] = BlobServiceClient,
    credential_factory: Callable[..., Any] = ManagedIdentityCredential,
) -> OccupancyMatchSource:
    """Resolve the App feed from the atomically published runtime manifest.

    Unlike the mower input resolver this deliberately does not roll back to the
    previous manifest: the public calendar must not silently resurrect an old
    kickoff after an official reschedule. A locally validated cache remains
    available for short Azure interruptions; otherwise the endpoint fails
    visibly instead of returning a known-stale schedule.
    """

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    now_utc = now_utc.astimezone(timezone.utc)
    packaged = str(
        environment.get("OCCUPANCY_MATCHES_PATH") or "public/matches.json"
    ).strip()
    if not _truthy(environment.get("SSV53_DYNAMIC_CONFIG_ENABLED", "false")):
        return OccupancyMatchSource(packaged, "package")

    account_url = str(environment.get("SSV53_CONFIG_STORAGE_ACCOUNT_URL") or "").strip()
    container_name = str(environment.get("SSV53_CONFIG_CONTAINER") or "").strip()
    client_id = str(
        environment.get("SSV53_CONFIG_MANAGED_IDENTITY_CLIENT_ID") or ""
    ).strip()
    if not account_url or not container_name or not client_id:
        raise RuntimeError(
            "Dynamische Belegungsdaten sind aktiv, aber die Blob-Konfiguration ist unvollständig."
        )
    try:
        max_age_minutes = int(
            environment.get(
                "SSV53_OCCUPANCY_MAX_AGE_MINUTES",
                "720",
            )
        )
    except ValueError as exc:
        raise RuntimeError("SSV53_OCCUPANCY_MAX_AGE_MINUTES ist ungültig.") from exc
    if max_age_minutes < 60 or max_age_minutes > 10080:
        raise RuntimeError(
            "SSV53_OCCUPANCY_MAX_AGE_MINUTES muss zwischen 60 und 10080 liegen."
        )
    cache_dir = Path(
        str(environment.get("SSV53_OCCUPANCY_CACHE_DIR") or "/tmp/ssv53-occupancy")
    )

    try:
        credential = credential_factory(client_id=client_id)
        service_client = service_client_factory(
            account_url=account_url,
            credential=credential,
        )
        return _download_current(
            container_client=service_client.get_container_client(container_name),
            cache_dir=cache_dir,
            now_utc=now_utc,
            max_age_minutes=max_age_minutes,
        )
    except (UnicodeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        # A reachable but malformed/new manifest must never be hidden by an
        # older cache because that could resurrect a superseded kickoff.
        raise RuntimeError(
            "Der aktuelle dynamische Belegungsplan ist ungültig; kein "
            "veralteter Spielplan wird ausgeliefert. " + str(exc)
        ) from exc
    except (AzureError, OSError) as exc:
        connection_error = str(exc)

    cached = _load_cache(
        cache_dir=cache_dir,
        now_utc=now_utc,
        max_age_minutes=max_age_minutes,
    )
    if cached is not None:
        return cached
    raise RuntimeError(
        "Keine frischen, validierten Belegungsdaten verfügbar; kein veralteter "
        "Spielplan wird ausgeliefert. "
        + connection_error
    )
