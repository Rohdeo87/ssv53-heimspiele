"""Prepare a detached, offline approval copy of the manual calendar candidate.

This command records approval supplied by an operator; it neither invents an
approval nor activates, publishes, or contacts Azure.  The output is created
exclusively and only after the stamped document passes the normal calendar
validation gates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from occupancy.training_calendar import calendar_digest, load_calendar, validate_calendar


UTC = timezone.utc


class ApprovalPreparationError(ValueError):
    """The candidate cannot be turned into an approved detached copy."""


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ApprovalPreparationError(f"JSON-Konfiguration ist kein Objekt: {path}")
    return value


def _approval_time(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ApprovalPreparationError("--approved-at benötigt einen UTC-Zeitpunkt.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovalPreparationError("--approved-at benötigt einen expliziten UTC-Offset.")
    return parsed.astimezone(UTC)


def prepare_approved_copy(
    calendar_path: str | Path,
    output_path: str | Path,
    *,
    expected_content_sha256: str,
    approval_reference: str,
    approved_at: datetime | str,
    occupancy_config_path: str | Path = ROOT / "occupancy/config.json",
    mower_config_path: str | Path = ROOT / "mower/config.json",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return and exclusively write a validated detached approval copy.

    ``now_utc`` is injectable for deterministic tests.  All input and output
    paths are local files; this function has no network or Azure calls.
    """
    source = Path(calendar_path)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ApprovalPreparationError("Zieldatei darf die Quelldatei nicht ersetzen.")
    if output.exists():
        raise ApprovalPreparationError(f"Zieldatei existiert bereits: {output}")
    if not isinstance(expected_content_sha256, str) or len(expected_content_sha256) != 64:
        raise ApprovalPreparationError("Erwarteter Inhaltshash muss ein SHA-256-Hash sein.")
    expected = expected_content_sha256.casefold()
    if any(char not in "0123456789abcdef" for char in expected):
        raise ApprovalPreparationError("Erwarteter Inhaltshash muss ein SHA-256-Hash sein.")
    if not isinstance(approval_reference, str) or not approval_reference.strip():
        raise ApprovalPreparationError("Ein Freigabebeleg darf nicht leer sein.")
    now = (now_utc or datetime.now(UTC))
    if now.tzinfo is None or now.utcoffset() is None:
        raise ApprovalPreparationError("Prüfzeit benötigt einen expliziten UTC-Offset.")
    now = now.astimezone(UTC)
    try:
        stamped_at = _approval_time(approved_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ApprovalPreparationError(str(exc)) from exc
    if stamped_at > now:
        raise ApprovalPreparationError("Freigabezeit darf nicht in der Zukunft liegen.")

    try:
        candidate = load_calendar(source)
        occupancy_config = _load_object(Path(occupancy_config_path))
        mower_config = _load_object(Path(mower_config_path))
        actual_digest = calendar_digest(candidate)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ApprovalPreparationError(f"Eingabe konnte nicht geprüft werden: {exc}") from exc
    if actual_digest != expected:
        raise ApprovalPreparationError("Erwarteter Hash stimmt nicht mit dem Kandidateninhalt überein.")

    detached = deepcopy(candidate)
    detached["approval"] = {
        "status": "approved",
        "reference": approval_reference.strip(),
        "approved_at_utc": stamped_at.isoformat().replace("+00:00", "Z"),
        "content_sha256": actual_digest,
    }
    validation = validate_calendar(
        detached,
        now_utc=now,
        occupancy_config=occupancy_config,
        mower_config=mower_config,
        require_season_periods=False,
    )
    if not validation.ready:
        details = [*validation.errors, *validation.activation_blockers]
        raise ApprovalPreparationError("Kalender bleibt blockiert: " + "; ".join(details))

    encoded = (json.dumps(detached, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")
    descriptor: int | None = None
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
    except (OSError, TypeError, ValueError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        # Do not unlink here: a concurrent creator may own the path after the
        # O_EXCL race, and a partial file requires explicit operator cleanup.
        raise ApprovalPreparationError(
            f"Zieldatei konnte nicht exklusiv erstellt werden; bitte prüfen: {exc}"
        ) from exc
    return detached


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar", type=Path, default=ROOT / "occupancy/training_calendar.manual-control.candidate.json")
    parser.add_argument("--mower-config", type=Path, default=ROOT / "mower/config.json")
    parser.add_argument("--occupancy-config", type=Path, default=ROOT / "occupancy/config.json")
    parser.add_argument("--expected-content-sha256", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--approved-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, now_utc: datetime | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = prepare_approved_copy(
            args.calendar, args.output,
            expected_content_sha256=args.expected_content_sha256,
            approval_reference=args.approval_reference,
            approved_at=args.approved_at,
            occupancy_config_path=args.occupancy_config,
            mower_config_path=args.mower_config,
            now_utc=now_utc,
        )
    except ApprovalPreparationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "output": str(args.output), "contentSha256": calendar_digest(document)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
