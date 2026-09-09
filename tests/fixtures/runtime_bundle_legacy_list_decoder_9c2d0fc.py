"""Unmodified old decoder body, frozen for the producer/consumer compatibility test.

Source: scripts/build_runtime_config_bundle.py at repository commit
9c2d0fc9c010b366cd49b58b8086b27f9d59a0dd, read from Git on 2026-09-09.
Only its imports and error class alias are provided locally. No network calls.
"""
import json
from pathlib import Path
from typing import Any

RuntimeBundleError = ValueError


def _load_list(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeBundleError(f"{label} konnte nicht gelesen werden: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise RuntimeBundleError(f"{label} muss eine nicht leere JSON-Liste sein.")
    if any(not isinstance(item, dict) for item in value):
        raise RuntimeBundleError(f"{label} enthält einen ungültigen Eintrag.")
    return value
