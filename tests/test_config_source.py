from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from mower.config_source import resolve_runtime_inputs

NOW = datetime(2026, 8, 7, 6, 0, tzinfo=timezone.utc)

class _Download:
    def __init__(self, payload: bytes) -> None: self.payload = payload
    def readall(self) -> bytes: return self.payload

class _Props:
    def __init__(self, etag: str) -> None: self.etag = etag

class _Blob:
    def __init__(self, payload: bytes, etag: str = '"etag-1"') -> None:
        self.payload = payload; self.etag = etag
    def get_blob_properties(self) -> _Props: return _Props(self.etag)
    def download_blob(self) -> _Download: return _Download(self.payload)

class _Container:
    def __init__(self, blobs: dict[str, bytes]) -> None: self.blobs = blobs
    def get_blob_client(self, name: str) -> _Blob:
        if name not in self.blobs: raise ValueError(f"missing blob {name}")
        return _Blob(self.blobs[name])

class _Service:
    def __init__(self, blobs: dict[str, bytes]) -> None: self.container = _Container(blobs)
    def get_container_client(self, _name: str) -> _Container: return self.container

class RuntimeConfigSourceTests(unittest.TestCase):
    def test_package_source_is_default(self) -> None:
        result = resolve_runtime_inputs({}, now_utc=NOW)
        self.assertEqual("package", result.source_kind)
        self.assertEqual("mower/config.json", result.config_path)
        self.assertEqual("public/rasen.ics", result.matches_path)

    def test_current_manifest_downloads_and_validates(self) -> None:
        config = b'{"planning":{"minimum_mowing_window_minutes":30}}\n'
        matches = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
        manifest = {
            "schema_version": 1, "version": "v1", "published_at_utc": NOW.isoformat(),
            "config_blob": "versions/v1/mower/config.json", "matches_blob": "versions/v1/public/rasen.ics",
            "config_sha256": sha256(config).hexdigest(), "matches_sha256": sha256(matches).hexdigest(),
        }
        blobs = {"current/manifest.json": json.dumps(manifest).encode(), manifest["config_blob"]: config, manifest["matches_blob"]: matches}
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SSV53_DYNAMIC_CONFIG_ENABLED": "true", "SSV53_CONFIG_STORAGE_ACCOUNT_URL": "https://example.blob.core.windows.net",
                "SSV53_CONFIG_CONTAINER": "runtime-config", "SSV53_CONFIG_MANAGED_IDENTITY_CLIENT_ID": "client",
                "SSV53_CONFIG_CACHE_DIR": tmp, "SSV53_CONFIG_MAX_AGE_MINUTES": "1440",
            }
            result = resolve_runtime_inputs(env, now_utc=NOW, service_client_factory=lambda **_: _Service(blobs), credential_factory=lambda **_: object())
            self.assertEqual("azure_blob", result.source_kind)
            self.assertFalse(result.fallback_used)
            self.assertTrue(Path(result.config_path).is_file())
            self.assertTrue(Path(result.matches_path).is_file())

    def test_stale_remote_fails_closed(self) -> None:
        config = b"{}\n"; matches = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"; stale = NOW - timedelta(days=2)
        manifest = {
            "schema_version": 1, "version": "stale", "published_at_utc": stale.isoformat(),
            "config_blob": "versions/stale/mower/config.json", "matches_blob": "versions/stale/public/rasen.ics",
            "config_sha256": sha256(config).hexdigest(), "matches_sha256": sha256(matches).hexdigest(),
        }
        blobs = {"current/manifest.json": json.dumps(manifest).encode(), manifest["config_blob"]: config, manifest["matches_blob"]: matches}
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SSV53_DYNAMIC_CONFIG_ENABLED": "true", "SSV53_CONFIG_STORAGE_ACCOUNT_URL": "https://example.blob.core.windows.net",
                "SSV53_CONFIG_CONTAINER": "runtime-config", "SSV53_CONFIG_MANAGED_IDENTITY_CLIENT_ID": "client",
                "SSV53_CONFIG_CACHE_DIR": tmp, "SSV53_CONFIG_MAX_AGE_MINUTES": "1440",
            }
            with self.assertRaisesRegex(RuntimeError, "fail-closed"):
                resolve_runtime_inputs(env, now_utc=NOW, service_client_factory=lambda **_: _Service(blobs), credential_factory=lambda **_: object())

if __name__ == "__main__": unittest.main()
