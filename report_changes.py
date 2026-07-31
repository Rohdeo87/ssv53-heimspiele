#!/usr/bin/env python3
"""Vergleicht den letzten veröffentlichten SSV53-Spielstand mit einem neuen Feed.

Das Skript erzeugt einen maschinenlesbaren JSON-Bericht und eine kompakte
Markdown-Zusammenfassung für GitHub Actions. Massive, unplausible Datenverluste
werden standardmäßig blockiert, damit ein technisch erfolgreicher, aber
inhaltlich unvollständiger Abruf den letzten guten Stand nicht überschreibt.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACKED_FIELDS: tuple[tuple[str, str], ...] = (
    ("kickoff", "Anstoß"),
    ("start", "Belegungsbeginn"),
    ("end", "Belegungsende"),
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
            lines.append(
                "- Der bisher veröffentlichte Stand bleibt unverändert. "
                "Eine Übernahme ist nur über einen manuell gestarteten Lauf mit ausdrücklicher Freigabe möglich."
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
                    if change["field"] in {"kickoff", "start", "end"}:
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
    args = parser.parse_args()

    if not 0.0 <= args.max_removal_ratio <= 1.0:
        parser.error("--max-removal-ratio muss zwischen 0 und 1 liegen")

    try:
        before_feed = load_feed(args.before, missing_ok=True)
        after_feed = load_feed(args.after)
        before = index_matches(before_feed, label="Vorheriger Feed")
        after = index_matches(after_feed, label="Neuer Feed")
        added, changed, removed = compare_matches(before, after)
    except ValueError as exc:
        parser.error(str(exc))

    guard_reasons = destructive_guard(
        before_count=len(before),
        after_count=len(after),
        removed_count=len(removed),
        max_removal_ratio=args.max_removal_ratio,
        min_previous_for_ratio=args.min_previous_for_ratio,
        min_removed_for_ratio=args.min_removed_for_ratio,
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
        "generatedAt": utc_now_iso(),
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
