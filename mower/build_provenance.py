"""Read local package evidence. This neither deploys nor attests device behavior."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


def _checked_path(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("INVALID_MANIFEST_PATH")
    return path


def inspect_installed_package(root: Path) -> dict[str, Any]:
    """Verify each declared source byte without trusting paths in a manifest.

    Remote-build dependencies and previously imported modules are outside this
    check. In particular, a matching source directory is not a device rollout.
    No file contents, configured credentials or absolute paths are returned.
    """
    root = root.resolve()
    result: dict[str, Any] = {
        "entrypoint_sha256": None,
        "package_manifest_sha256": None,
        "manifest_files_verified": False,
        "manifest_file_count": 0,
        "verified_file_count": 0,
        "all_installed_files_verified": False,
        "remote_build_dependencies_verified": False,
        "verification_error": None,
    }
    try:
        # Validate both fixed entry points before any content read. A manifest
        # cannot legitimize a symlink that was already followed while hashing.
        entrypoint = _checked_path(root, "function_app.py")
        manifest_path = _checked_path(root, "package-manifest.json")
        result["entrypoint_sha256"] = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
        raw = manifest_path.read_bytes()
        result["package_manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        manifest = json.loads(raw)
        entries = manifest.get("files")
        if manifest.get("schema_version") != 1 or not isinstance(entries, list) or not 1 <= len(entries) <= 250:
            raise ValueError("INVALID_MANIFEST")
        result["manifest_file_count"] = len(entries)
        names: set[str] = set()
        for entry in entries:
            name = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name or "\\" in name or ":" in name:
                raise ValueError("INVALID_MANIFEST_PATH")
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or name in names:
                raise ValueError("INVALID_MANIFEST_PATH")
            names.add(name)
            path = _checked_path(root, name)
            expected_hash, expected_size = entry.get("sha256"), entry.get("size")
            if (not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                    or type(expected_size) is not int or expected_size < 0):
                raise ValueError("INVALID_FILE_EVIDENCE")
            content = path.read_bytes()
            if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
                raise ValueError("INSTALLED_FILE_MISMATCH")
            result["verified_file_count"] += 1
        if not {"function_app.py", "host.json", "requirements.txt", "mower/controller.py"}.issubset(names):
            raise ValueError("INCOMPLETE_MANIFEST")
        # Check only application source locations, never site-packages or their
        # data. Unexpected application modules can otherwise hide an old sender.
        installed = {path.relative_to(root).as_posix() for path in root.glob("*.py")}
        for folder in ("mower", "occupancy"):
            installed.update(path.relative_to(root).as_posix() for path in (root / folder).rglob("*.py")
                             if "__pycache__" not in path.parts)
        if installed - names:
            raise ValueError("UNDECLARED_APPLICATION_SOURCE")
        result["manifest_files_verified"] = True
    except OSError:
        result["verification_error"] = "PACKAGE_FILE_UNAVAILABLE"
    except (ValueError, TypeError, AttributeError) as exc:
        # Parse/library error text can contain input fragments. Emit a fixed
        # code except for our finite, known validation failures.
        message = str(exc)
        result["verification_error"] = message if message in {
            "INVALID_MANIFEST", "INVALID_MANIFEST_PATH", "INVALID_FILE_EVIDENCE",
            "INSTALLED_FILE_MISMATCH", "INCOMPLETE_MANIFEST", "UNDECLARED_APPLICATION_SOURCE",
        } else "INVALID_MANIFEST"
    return result
