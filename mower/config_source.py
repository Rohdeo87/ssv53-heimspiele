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
class RuntimeInputPaths:
    config_path: str
    matches_path: str
    source_kind: str
    manifest_etag: str | None = None
    manifest_path: str | None = None
    published_at_utc: str | None = None
    fallback_used: bool = False


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("published_at_utc muss eine Zeitzone enthalten.")
    return parsed.astimezone(timezone.utc)


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _validate_age(published_at: datetime, now_utc: datetime, max_age_minutes: int) -> None:
    age_seconds = (now_utc - published_at).total_seconds()
    if age_seconds < -300:
        raise ValueError("Konfigurationsstand liegt unzulässig in der Zukunft.")
    if age_seconds > max_age_minutes * 60:
        raise ValueError("Konfigurationsstand ist älter als das zulässige Maximalalter.")


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if int(manifest.get("schema_version", 0)) != 1:
        raise ValueError("Unbekannte Manifest-Version.")
    required = (
        "version",
        "published_at_utc",
        "config_blob",
        "matches_blob",
        "config_sha256",
        "matches_sha256",
    )
    missing = [name for name in required if not str(manifest.get(name, "")).strip()]
    if missing:
        raise ValueError("Manifest-Felder fehlen: " + ", ".join(missing))
    for name in ("config_blob", "matches_blob"):
        value = str(manifest[name])
        if value.startswith("/") or ".." in Path(value).parts:
            raise ValueError(f"Unsicherer Blob-Pfad in {name}.")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _cache_paths(cache_dir: Path) -> tuple[Path, Path, Path]:
    return cache_dir / "mower-config.json", cache_dir / "rasen.ics", cache_dir / "metadata.json"


def _load_valid_cache(
    *,
    cache_dir: Path,
    now_utc: datetime,
    max_age_minutes: int,
    expected_etag: str | None = None,
    expected_manifest_path: str | None = None,
) -> RuntimeInputPaths | None:
    config_path, matches_path, metadata_path = _cache_paths(cache_dir)
    if not (config_path.is_file() and matches_path.is_file() and metadata_path.is_file()):
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        published = _parse_utc(str(metadata["published_at_utc"]))
        _validate_age(published, now_utc, max_age_minutes)
        if expected_etag is not None and metadata.get("manifest_etag") != expected_etag:
            return None
        if expected_manifest_path is not None and metadata.get("manifest_path") != expected_manifest_path:
            return None
        config_bytes = config_path.read_bytes()
        matches_bytes = matches_path.read_bytes()
        if _hash(config_bytes) != metadata.get("config_sha256"):
            return None
        if _hash(matches_bytes) != metadata.get("matches_sha256"):
            return None
        if not isinstance(json.loads(config_bytes.decode("utf-8")), dict):
            return None
        if b"BEGIN:VCALENDAR" not in matches_bytes:
            return None
        return RuntimeInputPaths(
            config_path=str(config_path),
            matches_path=str(matches_path),
            source_kind="azure_blob_cache",
            manifest_etag=metadata.get("manifest_etag"),
            manifest_path=metadata.get("manifest_path"),
            published_at_utc=metadata.get("published_at_utc"),
            fallback_used=True,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _download_candidate(
    *,
    container_client: Any,
    manifest_path: str,
    cache_dir: Path,
    now_utc: datetime,
    max_age_minutes: int,
) -> RuntimeInputPaths:
    manifest_blob = container_client.get_blob_client(manifest_path)
    properties = manifest_blob.get_blob_properties()
    etag = str(getattr(properties, "etag", "") or "")

    cached = _load_valid_cache(
        cache_dir=cache_dir,
        now_utc=now_utc,
        max_age_minutes=max_age_minutes,
        expected_etag=etag or None,
        expected_manifest_path=manifest_path,
    )
    if cached is not None:
        return RuntimeInputPaths(
            config_path=cached.config_path,
            matches_path=cached.matches_path,
            source_kind=cached.source_kind,
            manifest_etag=cached.manifest_etag,
            manifest_path=cached.manifest_path,
            published_at_utc=cached.published_at_utc,
            fallback_used=manifest_path != "current/manifest.json",
        )

    manifest = json.loads(manifest_blob.download_blob().readall().decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Manifest muss ein JSON-Objekt sein.")
    _validate_manifest(manifest)
    published = _parse_utc(str(manifest["published_at_utc"]))
    _validate_age(published, now_utc, max_age_minutes)

    config_bytes = container_client.get_blob_client(str(manifest["config_blob"])).download_blob().readall()
    matches_bytes = container_client.get_blob_client(str(manifest["matches_blob"])).download_blob().readall()
    if _hash(config_bytes) != str(manifest["config_sha256"]):
        raise ValueError("SHA256 der Konfiguration stimmt nicht.")
    if _hash(matches_bytes) != str(manifest["matches_sha256"]):
        raise ValueError("SHA256 der Spielplandatei stimmt nicht.")
    if not isinstance(json.loads(config_bytes.decode("utf-8")), dict):
        raise ValueError("mower/config.json muss ein JSON-Objekt sein.")
    if b"BEGIN:VCALENDAR" not in matches_bytes:
        raise ValueError("rasen.ics ist kein gültiger VCALENDAR-Grundkörper.")

    config_path, matches_path, metadata_path = _cache_paths(cache_dir)
    _atomic_write(config_path, config_bytes)
    _atomic_write(matches_path, matches_bytes)
    metadata = {
        "manifest_etag": etag or None,
        "manifest_path": manifest_path,
        "published_at_utc": published.isoformat(),
        "config_sha256": _hash(config_bytes),
        "matches_sha256": _hash(matches_bytes),
        "version": str(manifest["version"]),
    }
    _atomic_write(metadata_path, (json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return RuntimeInputPaths(
        config_path=str(config_path),
        matches_path=str(matches_path),
        source_kind="azure_blob",
        manifest_etag=etag or None,
        manifest_path=manifest_path,
        published_at_utc=published.isoformat(),
        fallback_used=manifest_path != "current/manifest.json",
    )


def resolve_runtime_inputs(
    environment: Mapping[str, str],
    *,
    now_utc: datetime,
    service_client_factory: Callable[..., Any] = BlobServiceClient,
    credential_factory: Callable[..., Any] = ManagedIdentityCredential,
) -> RuntimeInputPaths:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    now_utc = now_utc.astimezone(timezone.utc)

    packaged_config = environment.get("MOWER_CONFIG_PATH", "mower/config.json").strip()
    packaged_matches = environment.get("MOWER_MATCHES_PATH", "public/rasen.ics").strip()
    if not _truthy(environment.get("SSV53_DYNAMIC_CONFIG_ENABLED", "false")):
        return RuntimeInputPaths(packaged_config, packaged_matches, "package")

    account_url = environment.get("SSV53_CONFIG_STORAGE_ACCOUNT_URL", "").strip()
    container_name = environment.get("SSV53_CONFIG_CONTAINER", "").strip()
    client_id = environment.get("SSV53_CONFIG_MANAGED_IDENTITY_CLIENT_ID", "").strip()
    if not account_url or not container_name or not client_id:
        raise RuntimeError("Dynamische Konfiguration ist aktiv, aber die Blob-Konfiguration ist unvollständig.")

    try:
        max_age_minutes = int(environment.get("SSV53_CONFIG_MAX_AGE_MINUTES", "1440"))
    except ValueError as exc:
        raise RuntimeError("SSV53_CONFIG_MAX_AGE_MINUTES ist ungültig.") from exc
    if max_age_minutes < 60 or max_age_minutes > 10080:
        raise RuntimeError("SSV53_CONFIG_MAX_AGE_MINUTES muss zwischen 60 und 10080 liegen.")

    cache_dir = Path(environment.get("SSV53_CONFIG_CACHE_DIR", "/tmp/ssv53-config").strip())
    errors: list[str] = []
    try:
        credential = credential_factory(client_id=client_id)
        service_client = service_client_factory(account_url=account_url, credential=credential)
        container_client = service_client.get_container_client(container_name)
        for manifest_path in ("current/manifest.json", "previous/manifest.json"):
            try:
                return _download_candidate(
                    container_client=container_client,
                    manifest_path=manifest_path,
                    cache_dir=cache_dir,
                    now_utc=now_utc,
                    max_age_minutes=max_age_minutes,
                )
            except (AzureError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append(f"{manifest_path}: {exc}")
    except (AzureError, OSError, ValueError) as exc:
        errors.append(f"Blob-Verbindung: {exc}")

    cached = _load_valid_cache(cache_dir=cache_dir, now_utc=now_utc, max_age_minutes=max_age_minutes)
    if cached is not None:
        return cached
    raise RuntimeError("Keine frische, validierte Laufzeitkonfiguration verfügbar; fail-closed. " + " | ".join(errors))
