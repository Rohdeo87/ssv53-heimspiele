from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from azure.core.exceptions import ServiceRequestError

from occupancy.runtime_source import resolve_occupancy_match_source


NOW = datetime(2026, 8, 21, 11, 5, tzinfo=timezone.utc)
GENERATED = NOW - timedelta(minutes=5)


class _Download:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def readall(self) -> bytes:
        return self.payload


class _Props:
    def __init__(self, etag: str) -> None:
        self.etag = etag


class _Blob:
    def __init__(self, payload: bytes, etag: str) -> None:
        self.payload = payload
        self.etag = etag

    def get_blob_properties(self) -> _Props:
        return _Props(self.etag)

    def download_blob(self) -> _Download:
        return _Download(self.payload)


class _Container:
    def __init__(self, blobs: dict[str, bytes], etag: str = '"current-etag"') -> None:
        self.blobs = blobs
        self.etag = etag

    def get_blob_client(self, name: str) -> _Blob:
        if name not in self.blobs:
            raise ValueError(f"missing blob {name}")
        return _Blob(self.blobs[name], self.etag)


class _Service:
    def __init__(self, blobs: dict[str, bytes], etag: str = '"current-etag"') -> None:
        self.container = _Container(blobs, etag)

    def get_container_client(self, _name: str) -> _Container:
        return self.container


def _feed(kickoff: str = "2026-08-29T12:00+02:00") -> bytes:
    return (
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": GENERATED.isoformat(),
                "status": "ok",
                "matches": [
                    {
                        "id": "dfb:031HB5BAAS000000VS5489BTVVG7L386",
                        "kickoff": kickoff,
                    }
                ],
            }
        )
        + "\n"
    ).encode("utf-8")


def _blobs(feed: bytes | None = None) -> dict[str, bytes]:
    matches = feed or _feed()
    path = "versions/v1/public/matches.json"
    manifest = {
        "schema_version": 1,
        "version": "v1",
        "published_at_utc": NOW.isoformat(),
        "source_generated_at_utc": GENERATED.isoformat(),
        "source_commit": "a" * 40,
        "occupancy_matches_blob": path,
        "occupancy_matches_sha256": sha256(matches).hexdigest(),
    }
    return {
        "current/manifest.json": json.dumps(manifest).encode("utf-8"),
        path: matches,
    }


def _environment(cache: str) -> dict[str, str]:
    return {
        "SSV53_DYNAMIC_CONFIG_ENABLED": "true",
        "SSV53_CONFIG_STORAGE_ACCOUNT_URL": "https://example.blob.core.windows.net",
        "SSV53_CONFIG_CONTAINER": "runtime-config",
        "SSV53_CONFIG_MANAGED_IDENTITY_CLIENT_ID": "client",
        "SSV53_OCCUPANCY_CACHE_DIR": cache,
        "SSV53_OCCUPANCY_MAX_AGE_MINUTES": "720",
    }


class OccupancyRuntimeSourceTests(unittest.TestCase):
    def test_package_source_remains_default_outside_dynamic_production(self) -> None:
        result = resolve_occupancy_match_source({}, now_utc=NOW)
        self.assertEqual(result.source_kind, "package")
        self.assertEqual(result.matches_path, "public/matches.json")

    def test_current_manifest_downloads_hash_checks_and_caches_rescheduled_match(self) -> None:
        blobs = _blobs()
        with tempfile.TemporaryDirectory() as cache:
            result = resolve_occupancy_match_source(
                _environment(cache),
                now_utc=NOW,
                service_client_factory=lambda **_: _Service(blobs),
                credential_factory=lambda **_: object(),
            )
            self.assertEqual(result.source_kind, "azure_blob")
            self.assertFalse(result.fallback_used)
            payload = json.loads(Path(result.matches_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["matches"][0]["kickoff"], "2026-08-29T12:00+02:00")

            cached = resolve_occupancy_match_source(
                _environment(cache),
                now_utc=NOW + timedelta(minutes=1),
                service_client_factory=lambda **_: (_ for _ in ()).throw(
                    ServiceRequestError("offline")
                ),
                credential_factory=lambda **_: object(),
            )
            self.assertEqual(cached.source_kind, "azure_blob_cache")
            self.assertTrue(cached.fallback_used)

    def test_reachable_new_manifest_with_bad_hash_never_returns_old_cache(self) -> None:
        with tempfile.TemporaryDirectory() as cache:
            original = _blobs()
            resolve_occupancy_match_source(
                _environment(cache),
                now_utc=NOW,
                service_client_factory=lambda **_: _Service(original, '"etag-old"'),
                credential_factory=lambda **_: object(),
            )
            broken = _blobs(_feed("2026-08-29T13:00+02:00"))
            manifest = json.loads(broken["current/manifest.json"])
            manifest["occupancy_matches_sha256"] = "0" * 64
            broken["current/manifest.json"] = json.dumps(manifest).encode("utf-8")
            with self.assertRaisesRegex(RuntimeError, "kein veralteter Spielplan"):
                resolve_occupancy_match_source(
                    _environment(cache),
                    now_utc=NOW + timedelta(minutes=1),
                    service_client_factory=lambda **_: _Service(broken, '"etag-new"'),
                    credential_factory=lambda **_: object(),
                )

    def test_previous_manifest_is_not_used_to_resurrect_old_kickoff(self) -> None:
        blobs = _blobs()
        current = json.loads(blobs["current/manifest.json"])
        current.pop("occupancy_matches_blob")
        blobs["current/manifest.json"] = json.dumps(current).encode("utf-8")
        blobs["previous/manifest.json"] = _blobs(_feed("2026-08-29T11:00+02:00"))[
            "current/manifest.json"
        ]
        with tempfile.TemporaryDirectory() as cache:
            with self.assertRaisesRegex(RuntimeError, "kein veralteter Spielplan"):
                resolve_occupancy_match_source(
                    _environment(cache),
                    now_utc=NOW,
                    service_client_factory=lambda **_: _Service(blobs),
                    credential_factory=lambda **_: object(),
                )

    def test_stale_cache_is_rejected_during_blob_outage(self) -> None:
        blobs = _blobs()
        with tempfile.TemporaryDirectory() as cache:
            resolve_occupancy_match_source(
                _environment(cache),
                now_utc=NOW,
                service_client_factory=lambda **_: _Service(blobs),
                credential_factory=lambda **_: object(),
            )
            with self.assertRaisesRegex(RuntimeError, "Keine frischen"):
                resolve_occupancy_match_source(
                    _environment(cache),
                    now_utc=GENERATED + timedelta(minutes=721),
                    service_client_factory=lambda **_: (_ for _ in ()).throw(
                        ServiceRequestError("offline")
                    ),
                    credential_factory=lambda **_: object(),
                )


if __name__ == "__main__":
    unittest.main()
