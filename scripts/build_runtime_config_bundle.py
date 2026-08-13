#!/usr/bin/env python3
"""Build a fail-closed Azure runtime bundle from the latest published match data."""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from zoneinfo import ZoneInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mower.planner import build_training_blocks, load_json, read_match_blocks
from occupancy.match_model import detect_age_class
from poc_scraper import Match, recalculate_event_times, write_ics


UTC = timezone.utc
LOCAL_TZ = ZoneInfo("Europe/Berlin")
MATCH_FIELDS = {item.name for item in fields(Match)}
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RuntimeBundleError(ValueError):
    """The candidate is unsafe or incomplete and must not be published."""


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeBundleError(f"{label} konnte nicht gelesen werden: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeBundleError(f"{label} muss ein JSON-Objekt sein.")
    return value


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


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeBundleError(f"{label} ist keine gültige Zeitangabe.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeBundleError(f"{label} muss eine Zeitzone enthalten.")
    return parsed.astimezone(UTC)


def _validate_source(
    *,
    included: list[dict[str, Any]],
    summary: dict[str, Any],
    quality: dict[str, Any],
    published_at: datetime,
    max_source_age_minutes: int,
) -> datetime:
    if summary.get("publishable") is not True:
        raise RuntimeBundleError("Der veröffentlichte Spielbestand ist nicht freigegeben.")
    if int(summary.get("review", -1)) != 0:
        raise RuntimeBundleError("Der veröffentlichte Spielbestand enthält Prüffälle.")
    if quality.get("publishable") is not True or quality.get("errors"):
        raise RuntimeBundleError("Die Qualitätsprüfung des Spielbestands ist nicht grün.")
    if int(summary.get("included", -1)) != len(included):
        raise RuntimeBundleError("summary.json und included_matches.json widersprechen sich.")

    source_generated_at = _parse_utc(
        str(summary.get("generated_at") or ""),
        "summary.generated_at",
    )
    age_minutes = (published_at - source_generated_at).total_seconds() / 60
    if age_minutes < -5:
        raise RuntimeBundleError("Der Spielbestand liegt unzulässig in der Zukunft.")
    if age_minutes > max_source_age_minutes:
        raise RuntimeBundleError(
            "Der veröffentlichte Spielbestand ist älter als das zulässige "
            f"Maximalalter von {max_source_age_minutes} Minuten."
        )

    ids = [str(item.get("external_id") or "").strip() for item in included]
    if any(not value for value in ids):
        raise RuntimeBundleError("Mindestens ein veröffentlichtes Spiel besitzt keine ID.")
    if len(ids) != len(set(ids)):
        raise RuntimeBundleError("Der veröffentlichte Spielbestand enthält doppelte IDs.")

    actual_by_calendar: dict[str, int] = {}
    for item in included:
        calendar = str(item.get("calendar") or "").strip()
        actual_by_calendar[calendar] = actual_by_calendar.get(calendar, 0) + 1
    expected_by_calendar = summary.get("by_calendar") or {}
    for calendar in ("Rasen", "Kunstrasen"):
        if int(expected_by_calendar.get(calendar, -1)) != actual_by_calendar.get(calendar, 0):
            raise RuntimeBundleError(
                f"Die veröffentlichte Anzahl für {calendar} ist widersprüchlich."
            )
    return source_generated_at


def _parse_active_range(value: dict[str, Any]) -> tuple[date, date]:
    try:
        start = date.fromisoformat(str(value.get("from") or ""))
        end = date.fromisoformat(str(value.get("to") or ""))
    except ValueError as exc:
        raise RuntimeBundleError("training.active_ranges enthält ein ungültiges Datum.") from exc
    if end < start:
        raise RuntimeBundleError("Ein aktiver Trainingszeitraum endet vor seinem Beginn.")
    return start, end


def _validate_mower_config(
    config: dict[str, Any],
    *,
    planning_start: date,
    horizon_days: int = 14,
) -> tuple[list[Any], dict[str, Any]]:
    if config.get("timezone") != "Europe/Berlin":
        raise RuntimeBundleError("mower/config.json muss Europe/Berlin verwenden.")

    planning = config.get("planning")
    if not isinstance(planning, dict):
        raise RuntimeBundleError("planning fehlt oder ist ungültig.")
    minimum_window = int(planning.get("minimum_mowing_window_minutes", 0))
    if minimum_window < 30:
        raise RuntimeBundleError("Das minimale Mähfenster darf nicht unter 30 Minuten liegen.")

    training = config.get("training")
    if not isinstance(training, dict):
        raise RuntimeBundleError("training fehlt oder ist ungültig.")
    before = int(training.get("before_minutes", -1))
    after = int(training.get("after_minutes", -1))
    if before < 30 or after < 30:
        raise RuntimeBundleError("Trainingspuffer dürfen nicht unter 30 Minuten liegen.")
    weekly = training.get("weekly")
    if not isinstance(weekly, list) or not weekly:
        raise RuntimeBundleError("training.weekly ist leer oder ungültig.")

    ranges = training.get("active_ranges")
    if not isinstance(ranges, list) or not ranges:
        raise RuntimeBundleError("training.active_ranges fehlt.")
    parsed_ranges = [_parse_active_range(item) for item in ranges if isinstance(item, dict)]
    if len(parsed_ranges) != len(ranges):
        raise RuntimeBundleError("training.active_ranges enthält einen ungültigen Eintrag.")
    horizon_end = planning_start + timedelta(days=horizon_days - 1)
    if not any(start <= horizon_end and end >= planning_start for start, end in parsed_ranges):
        raise RuntimeBundleError(
            "Kein aktiver Trainingszeitraum deckt den kommenden Planungshorizont ab."
        )

    try:
        training_blocks = build_training_blocks(
            training,
            planning_start,
            horizon_days,
            LOCAL_TZ,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeBundleError(f"Trainingsplan ist ungültig: {exc}") from exc
    if not training_blocks:
        raise RuntimeBundleError("Der Trainingsplan erzeugt keine Sperrblöcke.")

    matches = config.get("matches")
    if not isinstance(matches, dict):
        raise RuntimeBundleError("matches fehlt oder ist ungültig.")
    if str(matches.get("source") or "") != "public/rasen.ics":
        raise RuntimeBundleError("matches.source muss public/rasen.ics sein.")
    if matches.get("already_buffered") is not True:
        raise RuntimeBundleError("Die Heimspiel-ICS muss bereits gepuffert sein.")

    hydrawise = config.get("hydrawise")
    if not isinstance(hydrawise, dict) or hydrawise.get("enabled") is not True:
        raise RuntimeBundleError("Hydrawise muss für die Sicherheitsplanung aktiviert sein.")
    if hydrawise.get("include_all_zones") is not True:
        raise RuntimeBundleError("Für die Sicherheitsplanung müssen alle Hydrawise-Zonen gelten.")
    if int(hydrawise.get("before_minutes", -1)) < 30:
        raise RuntimeBundleError("Der Beregnungs-Vorlauf darf nicht unter 30 Minuten liegen.")
    hydrawise_after = int(hydrawise.get("after_minutes", -1))
    if hydrawise_after < 0:
        raise RuntimeBundleError("Der Beregnungs-Nachlauf darf nicht negativ sein.")
    if int(hydrawise.get("expected_zone_count", 0)) != 7:
        raise RuntimeBundleError("Hydrawise muss exakt sieben Zonen erwarten.")
    try:
        relay_ids = [int(value) for value in hydrawise.get("relay_ids", [])]
    except (TypeError, ValueError) as exc:
        raise RuntimeBundleError("Hydrawise-Relay-IDs sind ungültig.") from exc
    if len(relay_ids) != 7 or len(set(relay_ids)) != 7 or any(value <= 0 for value in relay_ids):
        raise RuntimeBundleError(
            "Hydrawise muss exakt sieben eindeutige positive Relay-IDs freigeben."
        )
    if hydrawise.get("start_after_confirmed_park") is not True:
        raise RuntimeBundleError(
            "Der vorgezogene Hydrawise-Lauf muss an die bestätigte Parkposition gebunden sein."
        )

    return training_blocks, {
        "minimum_mowing_window_minutes": minimum_window,
        "training_before_minutes": before,
        "training_after_minutes": after,
        "hydrawise_before_minutes": int(hydrawise.get("before_minutes", -1)),
        "hydrawise_after_minutes": hydrawise_after,
        "training_ranges": [
            {"from": start.isoformat(), "to": end.isoformat()}
            for start, end in parsed_ranges
        ],
    }


def _as_match(item: dict[str, Any]) -> Match:
    required = [
        "external_id",
        "match_number",
        "team_id",
        "team_name",
        "team_category",
        "team_role",
        "kickoff",
        "home_team",
        "away_team",
        "competition",
        "match_type",
        "status",
        "venue_raw",
        "detail_url",
        "source_url",
    ]
    missing = [name for name in required if name not in item]
    if missing:
        raise RuntimeBundleError(
            f"Spiel {item.get('external_id', 'unbekannt')} enthält nicht alle Rohfelder: "
            + ", ".join(missing)
        )
    values = {name: item[name] for name in required}
    for name in MATCH_FIELDS - set(required):
        if name in item:
            values[name] = item[name]
    return Match(**values)


def _retime_matches(
    included: list[dict[str, Any]],
    timing_config: dict[str, Any],
) -> tuple[list[Match], dict[str, int], dict[str, int]]:
    matches: list[Match] = []
    by_age_class: dict[str, int] = {}
    by_duration_rule: dict[str, int] = {}
    for item in included:
        match = _as_match(item)
        try:
            recalculate_event_times(match, timing_config)
        except ValueError as exc:
            raise RuntimeBundleError(
                f"Spiel {match.external_id} besitzt keine belastbare Zeitregel: {exc}"
            ) from exc
        if not all(
            (
                match.event_start,
                match.event_end,
                match.match_end,
                match.duration_rule,
                match.competition_format,
            )
        ) or match.match_duration_minutes <= 0:
            raise RuntimeBundleError(
                f"Spiel {match.external_id} konnte nicht vollständig neu terminiert werden."
            )
        age_class = detect_age_class(match.team_category, match.team_name) or "FALLBACK"
        by_age_class[age_class] = by_age_class.get(age_class, 0) + 1
        by_duration_rule[match.duration_rule] = by_duration_rule.get(match.duration_rule, 0) + 1
        if match.calendar == "Rasen":
            matches.append(match)
    if not matches:
        raise RuntimeBundleError("Der freigegebene Spielbestand enthält keine Rasenspiele.")
    return matches, by_age_class, by_duration_rule


def build_runtime_bundle(
    *,
    mower_config_path: Path,
    timing_config_path: Path,
    included_matches_path: Path,
    source_summary_path: Path,
    source_quality_path: Path,
    output_dir: Path,
    version: str,
    published_at: datetime,
    source_commit: str,
    max_source_age_minutes: int = 720,
) -> dict[str, Any]:
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise RuntimeBundleError("published_at muss eine Zeitzone enthalten.")
    published_at = published_at.astimezone(UTC)
    if not VERSION_PATTERN.fullmatch(version):
        raise RuntimeBundleError("version enthält unzulässige Zeichen.")
    if not 60 <= max_source_age_minutes <= 1440:
        raise RuntimeBundleError("max_source_age_minutes muss zwischen 60 und 1440 liegen.")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeBundleError("Das Bundle-Zielverzeichnis muss leer sein.")

    included = _load_list(included_matches_path, "included_matches.json")
    source_summary = _load_object(source_summary_path, "summary.json")
    source_quality = _load_object(source_quality_path, "quality_report.json")
    source_generated_at = _validate_source(
        included=included,
        summary=source_summary,
        quality=source_quality,
        published_at=published_at,
        max_source_age_minutes=max_source_age_minutes,
    )

    mower_config = load_json(mower_config_path)
    training_blocks, safety_summary = _validate_mower_config(
        mower_config,
        planning_start=published_at.astimezone(LOCAL_TZ).date(),
    )
    timing_config = load_json(timing_config_path)
    rasen_matches, by_age_class, by_duration_rule = _retime_matches(
        included,
        timing_config,
    )
    expected_rasen = int((source_summary.get("by_calendar") or {}).get("Rasen", -1))
    if len(rasen_matches) != expected_rasen:
        raise RuntimeBundleError("Bei der Neuberechnung gingen Rasenspiele verloren.")

    version_dir = output_dir / "versions" / version
    version_config = version_dir / "mower" / "config.json"
    version_matches = version_dir / "public" / "rasen.ics"
    version_config.parent.mkdir(parents=True, exist_ok=True)
    version_matches.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(mower_config_path, version_config)
    write_ics(version_matches, rasen_matches, "SSV53 Rasen – Spiele")

    parsed_blocks = read_match_blocks(version_matches, LOCAL_TZ)
    if len(parsed_blocks) != len(rasen_matches):
        raise RuntimeBundleError("Die erzeugte Rasen-ICS enthält nicht alle Spiele.")
    if any(block.end <= block.start for block in parsed_blocks):
        raise RuntimeBundleError("Die erzeugte Rasen-ICS enthält ein ungültiges Zeitfenster.")

    config_bytes = version_config.read_bytes()
    matches_bytes = version_matches.read_bytes()
    manifest = {
        "schema_version": 1,
        "version": version,
        "published_at_utc": published_at.isoformat(),
        "config_blob": f"versions/{version}/mower/config.json",
        "matches_blob": f"versions/{version}/public/rasen.ics",
        "config_sha256": sha256(config_bytes).hexdigest(),
        "matches_sha256": sha256(matches_bytes).hexdigest(),
        "source_commit": source_commit,
        "source_generated_at_utc": source_generated_at.isoformat(),
    }
    manifest_path = output_dir / "current" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = {
        "version": version,
        "published_at_utc": published_at.isoformat(),
        "source_commit": source_commit,
        "source_generated_at_utc": source_generated_at.isoformat(),
        "source_age_minutes": round(
            (published_at - source_generated_at).total_seconds() / 60,
            1,
        ),
        "matches_loaded": len(rasen_matches),
        "training_blocks_14_days": len(training_blocks),
        "by_age_class": dict(sorted(by_age_class.items())),
        "by_duration_rule": dict(sorted(by_duration_rule.items())),
        "config_sha256": manifest["config_sha256"],
        "matches_sha256": manifest["matches_sha256"],
        "safety": safety_summary,
    }
    (output_dir / "validation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mower-config", type=Path, required=True)
    parser.add_argument("--timing-config", type=Path, required=True)
    parser.add_argument("--included-matches", type=Path, required=True)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--source-quality", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--max-source-age-minutes", type=int, default=720)
    return parser


def main() -> int:
    args = _parser().parse_args()
    summary = build_runtime_bundle(
        mower_config_path=args.mower_config,
        timing_config_path=args.timing_config,
        included_matches_path=args.included_matches,
        source_summary_path=args.source_summary,
        source_quality_path=args.source_quality,
        output_dir=args.output,
        version=args.version,
        published_at=_parse_utc(args.published_at, "published_at"),
        source_commit=args.source_commit,
        max_source_age_minutes=args.max_source_age_minutes,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
