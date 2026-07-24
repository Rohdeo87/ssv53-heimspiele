#!/usr/bin/env python3
"""Erzeugt den schlanken, vom Appack-Belegungsplan lesbaren JSON-Feed."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


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

    if failed:
        raise SystemExit(
            "Mindestens eine Mannschaft konnte nicht geladen werden. "
            "Der bisherige veröffentlichte Stand bleibt unverändert."
        )

    matches = []
    for item in included:
        calendar = str(item.get("calendar") or "")
        if calendar not in {"Rasen", "Kunstrasen"}:
            continue
        external_id = str(item.get("external_id") or "").strip()
        start = str(item.get("event_start") or "").strip()
        end = str(item.get("event_end") or "").strip()
        if not external_id or not start or not end:
            continue
        team = str(item.get("team_name") or "").strip()
        home = str(item.get("home_team") or "").strip()
        away = str(item.get("away_team") or "").strip()
        title = f"Heimspiel {team}: {home} – {away}" if team else f"Heimspiel: {home} – {away}"
        matches.append({
            "id": "dfb:" + external_id,
            "title": title,
            "start": start,
            "end": end,
            "kickoff": item.get("kickoff", ""),
            "place": "rasen" if calendar == "Rasen" else "kunstrasen",
            "calendar": calendar,
            "team": team,
            "homeTeam": home,
            "awayTeam": away,
            "competition": item.get("competition", ""),
            "status": item.get("status", ""),
            "detailLink": item.get("detail_url", ""),
            "source": "fussball.de",
            "checksum": item.get("checksum", ""),
        })

    matches.sort(key=lambda value: (value.get("start", ""), value.get("id", "")))
    generated_at = summary.get("generated_at") or datetime.now(timezone.utc).isoformat()
    feed = {
        "schemaVersion": 1,
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
    ):
        src = source / name
        if src.exists():
            shutil.copy2(src, target / name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
