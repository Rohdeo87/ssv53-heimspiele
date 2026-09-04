#!/usr/bin/env python3
"""Vergleicht den letzten veröffentlichten SSV53-Spielstand mit einem neuen Feed.

Das Skript erzeugt einen maschinenlesbaren JSON-Bericht und eine kompakte
Markdown-Zusammenfassung für GitHub Actions. Massive, unplausible Datenverluste
werden standardmäßig blockiert, damit ein technisch erfolgreicher, aber
inhaltlich unvollständiger Abruf den letzten guten Stand nicht überschreibt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TRACKED_FIELDS: tuple[tuple[str, str], ...] = (
    ("kickoff", "Anstoß"),
    ("start", "Belegungsbeginn"),
    ("end", "Belegungsende"),
    ("occupancyStart", "Sperrbeginn"),
    ("occupancyEnd", "Sperrende"),
    ("calendar", "Platz"),
    ("place", "Platzschlüssel"),
    ("team", "Mannschaft"),
    ("teamCategory", "Mannschaftsart"),
    ("teamRole", "Heim-/Gastrolle"),
    ("homeTeam", "Heimteam"),
    ("awayTeam", "Gastteam"),
    ("competition", "Wettbewerb"),
    ("status", "Status"),
    ("detailLink", "Detail-Link"),
)
SAFETY_FIELDS = (
    "kickoff",
    "start",
    "end",
    "occupancyStart",
    "occupancyEnd",
    "calendar",
    "place",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_feed(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if not path.exists():
        if missing_ok:
            return {"matches": []}
        raise ValueError(f"Feed fehlt: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Feed ist nicht lesbar: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Feed muss ein JSON-Objekt sein: {path}")
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise ValueError(f"Feed enthält keine gültige matches-Liste: {path}")
    return payload


def index_matches(feed: dict[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for position, raw in enumerate(feed.get("matches", []), start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"{label}: Spiel {position} ist kein JSON-Objekt")
        match_id = str(raw.get("id") or "").strip()
        if not match_id:
            raise ValueError(f"{label}: Spiel {position} hat keine ID")
        if match_id in indexed:
            raise ValueError(f"{label}: doppelte Spiel-ID {match_id}")
        indexed[match_id] = raw
    return indexed


def display_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "–"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return text


def match_label(match: dict[str, Any]) -> str:
    team = str(match.get("team") or "Unbekannte Mannschaft").strip()
    home = str(match.get("homeTeam") or "?").strip()
    away = str(match.get("awayTeam") or "?").strip()
    kickoff = display_datetime(match.get("kickoff"))
    calendar = str(match.get("calendar") or "ohne Platz").strip()
    return f"{team} · {home} – {away} · {kickoff} · {calendar}"


def compare_matches(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    before_ids = set(before)
    after_ids = set(after)

    added = [after[match_id] for match_id in sorted(after_ids - before_ids)]
    removed = [before[match_id] for match_id in sorted(before_ids - after_ids)]
    changed: list[dict[str, Any]] = []

    for match_id in sorted(before_ids & after_ids):
        old = before[match_id]
        new = after[match_id]
        field_changes = []
        for field, field_label in TRACKED_FIELDS:
            old_value = old.get(field, "")
            new_value = new.get(field, "")
            if old_value != new_value:
                field_changes.append({
                    "field": field,
                    "label": field_label,
                    "before": old_value,
                    "after": new_value,
                })
        if field_changes:
            changed.append({
                "id": match_id,
                "label": match_label(new),
                "before": old,
                "after": new,
                "changes": field_changes,
            })
    return added, changed, removed


def destructive_guard(
    *,
    before_count: int,
    after_count: int,
    removed_count: int,
    max_removal_ratio: float,
    min_previous_for_ratio: int,
    min_removed_for_ratio: int,
) -> list[str]:
    reasons: list[str] = []
    if before_count > 0 and after_count == 0:
        reasons.append(
            f"Der neue Feed ist leer, obwohl zuvor {before_count} Spiele vorhanden waren."
        )

    if before_count >= min_previous_for_ratio:
        removal_ratio = removed_count / before_count if before_count else 0.0
        if (
            removed_count >= min_removed_for_ratio
            and removal_ratio > max_removal_ratio
        ):
            percentage = math.floor(removal_ratio * 100)
            reasons.append(
                f"{removed_count} von {before_count} Spielen würden verschwinden "
                f"({percentage} %; zulässig sind höchstens "
                f"{math.floor(max_removal_ratio * 100)} % ohne manuelle Freigabe)."
            )
    return reasons


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def occupancy_bounds(match: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    start = parse_datetime(match.get("occupancyStart") or match.get("start"))
    end = parse_datetime(match.get("occupancyEnd") or match.get("end"))
    return start, end


def is_active_or_future(match: dict[str, Any], now: datetime) -> bool:
    _, end = occupancy_bounds(match)
    # An unparseable external timestamp is never treated as permission to
    # remove an existing safety block.
    return end is None or end >= now.astimezone(timezone.utc)


def safety_decreasing_changes(
    changed: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Return changes that could free previously protected occupancy time."""
    result: list[dict[str, Any]] = []
    for old in removed:
        if is_active_or_future(old, now):
            result.append({
                "kind": "removed",
                "id": str(old.get("id") or ""),
                "before": {field: old.get(field, "") for field in SAFETY_FIELDS},
                "after": None,
            })

    for item in changed:
        old = item["before"]
        new = item["after"]
        if not is_active_or_future(old, now):
            continue
        old_start, old_end = occupancy_bounds(old)
        new_start, new_end = occupancy_bounds(new)
        calendar_changed = (
            str(old.get("calendar") or "") != str(new.get("calendar") or "")
            or str(old.get("place") or "") != str(new.get("place") or "")
        )
        interval_no_longer_covers_old = (
            old_start is None
            or old_end is None
            or new_start is None
            or new_end is None
            or new_start > old_start
            or new_end < old_end
        )
        if calendar_changed or interval_no_longer_covers_old:
            result.append({
                "kind": "changed",
                "id": str(item.get("id") or ""),
                "before": {field: old.get(field, "") for field in SAFETY_FIELDS},
                "after": {field: new.get(field, "") for field in SAFETY_FIELDS},
            })
    return sorted(result, key=lambda item: (item["id"], item["kind"]))


def _empty_confirmation_state() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "pendingFingerprint": "",
        "firstSeenAt": "",
        "lastCountedAt": "",
        "confirmations": 0,
        "items": [],
    }


def load_confirmation_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return _empty_confirmation_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_confirmation_state()
    if not isinstance(value, dict):
        return _empty_confirmation_state()
    return {**_empty_confirmation_state(), **value}


def write_confirmation_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def confirm_safety_decrease(
    items: list[dict[str, Any]],
    *,
    state_path: Path | None,
    now: datetime,
    required_confirmations: int,
    minimum_interval: timedelta,
) -> dict[str, Any]:
    if not items:
        write_confirmation_state(state_path, _empty_confirmation_state())
        return {
            "required": False,
            "confirmed": True,
            "confirmations": 0,
            "requiredConfirmations": required_confirmations,
            "fingerprint": "",
            "items": [],
        }

    fingerprint = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    previous = load_confirmation_state(state_path)
    now_utc = now.astimezone(timezone.utc)
    confirmations = 1
    first_seen = now_utc
    last_counted = now_utc

    if previous.get("pendingFingerprint") == fingerprint:
        confirmations = max(int(previous.get("confirmations") or 0), 1)
        first_seen = parse_datetime(previous.get("firstSeenAt")) or now_utc
        last_counted = parse_datetime(previous.get("lastCountedAt")) or first_seen
        if now_utc - last_counted >= minimum_interval:
            confirmations += 1
            last_counted = now_utc

    confirmed = confirmations >= required_confirmations
    if confirmed:
        state = {
            **_empty_confirmation_state(),
            "lastConfirmedFingerprint": fingerprint,
            "lastConfirmedAt": now_utc.isoformat(timespec="seconds"),
        }
    else:
        state = {
            "schemaVersion": 1,
            "pendingFingerprint": fingerprint,
            "firstSeenAt": first_seen.isoformat(timespec="seconds"),
            "lastCountedAt": last_counted.isoformat(timespec="seconds"),
            "confirmations": confirmations,
            "items": items,
        }
    write_confirmation_state(state_path, state)
    return {
        "required": True,
        "confirmed": confirmed,
        "confirmations": confirmations,
        "requiredConfirmations": required_confirmations,
        "minimumIntervalMinutes": int(minimum_interval.total_seconds() // 60),
        "fingerprint": fingerprint,
        "items": items,
    }


def markdown_report(report: dict[str, Any], *, max_items: int = 50) -> str:
    counts = report["counts"]
    status = report["status"]
    if status == "blocked":
        headline = "⛔ Veröffentlichung blockiert"
    elif status == "approved_override":
        headline = "⚠️ Massive Änderung manuell freigegeben"
    elif status == "baseline":
        headline = "ℹ️ Vergleichsbasis neu aufgebaut"
    else:
        headline = "✅ Spielabruf erfolgreich geprüft"

    lines = [
        "## SSV53-Spielabruf",
        "",
        headline,
        "",
        "| Kennzahl | Anzahl |",
        "|---|---:|",
        f"| Spiele vorher | {counts['before']} |",
        f"| Spiele nachher | {counts['after']} |",
        f"| Neu | {counts['added']} |",
        f"| Geändert | {counts['changed']} |",
        f"| Entfernt | {counts['removed']} |",
        "",
    ]

    guard = report.get("guard", {})
    if guard.get("reasons"):
        lines.extend(["### Sicherheitsprüfung", ""])
        for reason in guard["reasons"]:
            lines.append(f"- {reason}")
        if status == "blocked":
            safety = guard.get("safetyConfirmation", {})
            if safety.get("required") and not safety.get("confirmed"):
                lines.append(
                    "- Der bisher veröffentlichte Stand bleibt unverändert. "
                    "Eine identische Beobachtung nach dem Sicherheitsabstand kann "
                    "die Einzeländerung automatisch bestätigen."
                )
            else:
                lines.append(
                    "- Der bisher veröffentlichte Stand bleibt unverändert. "
                    "Die ungewöhnlich große Änderung benötigt eine ausdrückliche "
                    "manuelle Freigabe."
                )
        lines.append("")

    def add_match_section(title: str, items: list[dict[str, Any]], kind: str) -> None:
        if not items:
            return
        lines.extend([f"### {title}", ""])
        for item in items[:max_items]:
            if kind == "changed":
                lines.append(f"- **{item['label']}**")
                for change in item["changes"]:
                    before_value = change["before"]
                    after_value = change["after"]
                    if change["field"] in {
                        "kickoff", "start", "end", "occupancyStart", "occupancyEnd"
                    }:
                        before_value = display_datetime(before_value)
                        after_value = display_datetime(after_value)
                    lines.append(
                        f"  - {change['label']}: `{before_value or '–'}` → `{after_value or '–'}`"
                    )
            else:
                lines.append(f"- {match_label(item)}")
        if len(items) > max_items:
            lines.append(f"- … und {len(items) - max_items} weitere")
        lines.append("")

    add_match_section("Neue Spiele", report["added"], "added")
    add_match_section("Geänderte Spiele", report["changed"], "changed")
    add_match_section("Entfernte Spiele", report["removed"], "removed")

    if not report["added"] and not report["changed"] and not report["removed"]:
        lines.extend(["Keine inhaltlichen Änderungen seit dem vorherigen erfolgreichen Abruf.", ""])

    lines.append(f"Erstellt: `{report['generatedAt']}`")
    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def append_github_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(markdown)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--public-json", type=Path)
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--max-removal-ratio", type=float, default=0.60)
    parser.add_argument("--min-previous-for-ratio", type=int, default=10)
    parser.add_argument("--min-removed-for-ratio", type=int, default=5)
    parser.add_argument("--confirmation-state", type=Path)
    parser.add_argument("--required-confirmations", type=int, default=2)
    parser.add_argument("--minimum-confirmation-minutes", type=int, default=60)
    args = parser.parse_args()

    if not 0.0 <= args.max_removal_ratio <= 1.0:
        parser.error("--max-removal-ratio muss zwischen 0 und 1 liegen")
    if args.required_confirmations < 2:
        parser.error("--required-confirmations muss mindestens 2 sein")
    if args.minimum_confirmation_minutes < 1:
        parser.error("--minimum-confirmation-minutes muss mindestens 1 sein")

    try:
        before_feed = load_feed(args.before, missing_ok=True)
        after_feed = load_feed(args.after)
        before = index_matches(before_feed, label="Vorheriger Feed")
        after = index_matches(after_feed, label="Neuer Feed")
        added, changed, removed = compare_matches(before, after)
    except ValueError as exc:
        parser.error(str(exc))

    generated_at = datetime.now(timezone.utc)
    guard_reasons = destructive_guard(
        before_count=len(before),
        after_count=len(after),
        removed_count=len(removed),
        max_removal_ratio=args.max_removal_ratio,
        min_previous_for_ratio=args.min_previous_for_ratio,
        min_removed_for_ratio=args.min_removed_for_ratio,
    )
    risky_changes = safety_decreasing_changes(changed, removed, now=generated_at)
    safety_confirmation = confirm_safety_decrease(
        risky_changes,
        state_path=args.confirmation_state,
        now=generated_at,
        required_confirmations=args.required_confirmations,
        minimum_interval=timedelta(minutes=args.minimum_confirmation_minutes),
    )
    if safety_confirmation["required"] and not safety_confirmation["confirmed"]:
        guard_reasons.append(
            f"{len(risky_changes)} sicherheitsrelevante Änderung(en) würden bisherige "
            "Sperrzeit freigeben. Erforderlich sind zwei identische, zeitlich "
            "getrennte Abrufe."
        )

    if not args.before.exists():
        status = "baseline"
    elif guard_reasons and not args.allow_destructive:
        status = "blocked"
    elif guard_reasons and args.allow_destructive:
        status = "approved_override"
    else:
        status = "ok"

    report: dict[str, Any] = {
        "schemaVersion": 1,
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "status": status,
        "counts": {
            "before": len(before),
            "after": len(after),
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
        },
        "guard": {
            "triggered": bool(guard_reasons),
            "overrideUsed": bool(guard_reasons and args.allow_destructive),
            "maxRemovalRatio": args.max_removal_ratio,
            "reasons": guard_reasons,
            "safetyConfirmation": safety_confirmation,
        },
        "added": added,
        "changed": changed,
        "removed": removed,
    }

    markdown = markdown_report(report)
    write_json(args.json, report)
    write_text(args.markdown, markdown)
    append_github_summary(markdown)

    if status == "blocked":
        return 2

    if args.public_json:
        args.public_json.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.json, args.public_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
