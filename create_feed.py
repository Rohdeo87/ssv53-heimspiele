#!/usr/bin/env python3
"""Erzeugt den schlanken, vom Appack-Belegungsplan lesbaren JSON-Feed."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from occupancy.match_model import normalize_match_description, normalize_match_title


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("generated"))
    parser.add_argument("--output", type=Path, default=Path("public"))
    args = parser.parse_args()

    source = args.input
    target = args.output
    target.mkdir(parents=True, exist_ok=True)

    included = load_json(source / "included_matches.json")
    summary = load_json(source / "summary.json")
    failed = load_json(source / "failed_teams.json")
    quality = load_json(source / "quality_report.json")

    if failed:
        raise SystemExit(
            "Mindestens eine Mannschaft konnte nicht geladen werden. "
            "Der bisherige veröffentlichte Stand bleibt unverändert."
        )

    if not quality.get("publishable"):
        raise SystemExit(
            "Die Qualitätsprüfung ist fehlgeschlagen. "
            "Der bisherige veröffentlichte Stand bleibt unverändert: "
            + " | ".join(quality.get("errors", []))
        )

    matches = []
    invalid = []
    for item in included:
        calendar = str(item.get("calendar") or "")
        if calendar not in {"Rasen", "Kunstrasen"}:
            continue
        external_id = str(item.get("external_id") or "").strip()
        start = str(item.get("kickoff") or "").strip()
        end = str(item.get("match_end") or "").strip()
        occupancy_start = str(item.get("event_start") or "").strip()
        occupancy_end = str(item.get("event_end") or "").strip()
        duration_minutes = int(item.get("match_duration_minutes") or 0)
        duration_rule = str(item.get("duration_rule") or "").strip()
        competition_format = str(item.get("competition_format") or "").strip()
        if not all((external_id, start, end, occupancy_start, occupancy_end)):
            invalid.append(str(item.get("external_id") or "unbekannt"))
            continue
        if duration_minutes <= 0 or not duration_rule or not competition_format:
            invalid.append(external_id)
            continue
        team = str(item.get("team_name") or "").strip()
        home = str(item.get("home_team") or "").strip()
        away = str(item.get("away_team") or "").strip()
        team_category = str(item.get("team_category") or "").strip()
        competition = str(item.get("competition") or "").strip()
        matches.append({
            "id": "dfb:" + external_id,
            "title": normalize_match_title(home, away),
            "start": start,
            "end": end,
            "occupancyStart": occupancy_start,
            "occupancyEnd": occupancy_end,
            "kickoff": start,
            "matchDurationMinutes": duration_minutes,
            "durationRule": duration_rule,
            "competitionFormat": competition_format,
            "place": "rasen" if calendar == "Rasen" else "kunstrasen",
            "calendar": calendar,
            "team": team,
            "teamCategory": team_category,
            "teamRole": item.get("team_role", "unknown"),
            "homeTeam": home,
            "awayTeam": away,
            "competition": competition,
            "description": normalize_match_description(team_category, competition),
            "status": item.get("status", ""),
            "detailLink": item.get("detail_url", ""),
            "location": item.get("venue_raw", ""),
            "source": "fussball.de",
            "checksum": item.get("checksum", ""),
        })

    if invalid:
        raise SystemExit(
            "Unvollständige aufzunehmende Spiele: " + ", ".join(invalid)
        )

    if len(matches) != len(included):
        raise SystemExit(
            "Nicht alle geprüften Spiele wurden in den Feed übernommen."
        )

    matches.sort(key=lambda value: (value.get("start", ""), value.get("id", "")))
    generated_at = summary.get("generated_at") or datetime.now(timezone.utc).isoformat()
    feed = {
        "schemaVersion": 2,
        "generatedAt": generated_at,
        "status": "ok",
        "matches": matches,
    }
    (target / "matches.json").write_text(
        json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for name in (
        "summary.json",
        "all_matches.json",
        "included_matches.json",
        "review_matches.json",
        "excluded_matches.json",
        "failed_teams.json",
        "appack_preview.csv",
        "rasen.ics",
        "kunstrasen.ics",
        "quality_report.json",
        "team_registry.json",
    ):
        src = source / name
        if src.exists():
            shutil.copy2(src, target / name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
