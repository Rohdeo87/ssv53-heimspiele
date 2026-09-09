#!/usr/bin/env python3
"""Prepare one conservative JSON/ICS bundle from a complete source observation.

This command never calls a source, publishes remotely, or controls a device.
Pending removals belong to the published bundle, so a checkout/restart cannot
forget them. Each old occupancy interval is confirmed independently.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from dataclasses import fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from create_feed import validate_timing
from occupancy.match_model import normalize_match_description, normalize_match_title
from poc_scraper import Match, evaluate_quality, write_ics
from report_changes import (
    SAFETY_FIELDS, append_github_summary, compare_matches, destructive_guard,
    index_matches, markdown_report, occupancy_bounds, parse_datetime,
    write_json, write_text,
)

UTC = timezone.utc
LOCAL_TZ = ZoneInfo("Europe/Berlin")
HOLD_PREFIX = "held-"
REQUIRED_CONSUMER = "ssv53-additive-retention-v1"
MATCH_FIELDS = {item.name for item in fields(Match)}
RAW_FIELDS = {
    "start": "kickoff", "kickoff": "kickoff", "end": "match_end",
    "occupancyStart": "event_start", "occupancyEnd": "event_end",
    "matchDurationMinutes": "match_duration_minutes", "durationRule": "duration_rule",
    "competitionFormat": "competition_format", "matchType": "match_type",
    "calendar": "calendar", "team": "team_name", "teamCategory": "team_category",
    "teamRole": "team_role", "homeTeam": "home_team", "awayTeam": "away_team",
    "competition": "competition", "detailLink": "detail_url", "status": "status",
}


class PublicationError(ValueError):
    """No candidate may replace the last complete published bundle."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _load(path: Path, expected: type | tuple[type, ...]) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublicationError(f"{path.name} ist nicht vollständig lesbar.") from exc
    if not isinstance(value, expected):
        raise PublicationError(f"{path.name} hat ein falsches Datenformat.")
    return value


def decode_included_matches(payload: Any) -> list[dict[str, Any]]:
    """Old list-only consumers must reject held intervals instead of retiming them."""
    versioned = isinstance(payload, dict)
    if versioned:
        if (payload.get("schemaVersion") != 2 or payload.get("kind") != "retained-occupancy"
                or payload.get("requiredConsumer") != REQUIRED_CONSUMER):
            raise PublicationError("Das Rückhalteformat benötigt einen anderen geprüften Consumer.")
        rows = payload.get("matches")
    else:
        rows = payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PublicationError("Der Rohbestand benötigt eine Liste gültiger Spielobjekte.")
    has_holds = any(row.get("publication_retention") or
                    str(row.get("external_id") or "").startswith(HOLD_PREFIX) for row in rows)
    if has_holds and not versioned:
        raise PublicationError("Rückhaltesperren benötigen das versionierte Consumer-Format.")
    if versioned:
        validate_publication_evidence(payload.get("publication"), rows)
    return rows


def validate_publication_evidence(publication: Any, rows: list[dict[str, Any]]) -> None:
    if not isinstance(publication, dict) or publication.get("schemaVersion") != 1:
        raise PublicationError("Der Veröffentlichungsnachweis fehlt oder hat ein unbekanntes Format.")
    retained = sum(bool(row.get("publication_retention")) for row in rows)
    expected_counts = {"sourceCount": len(rows) - retained, "retainedCount": retained, "effectiveCount": len(rows)}
    if any(type(publication.get(name)) is not int or publication[name] != count
           for name, count in expected_counts.items()):
        raise PublicationError("Der Veröffentlichungsnachweis enthält widersprüchliche Zählungen.")
    if publication.get("effectiveDigest") != _digest(rows):
        raise PublicationError("Der Digest des wirksamen Rohbestands stimmt nicht mit dem Veröffentlichungsnachweis überein.")
    if publication.get("requiredConsumer") != (REQUIRED_CONSUMER if retained else None):
        raise PublicationError("Consumer-Freigabe und zurückgehaltene Sperren widersprechen sich.")
    if publication.get("status") != ("additive_pending" if retained else "ok"):
        raise PublicationError("Qualitätsstatus und zurückgehaltene Sperren widersprechen sich.")
    _clock(publication.get("sourceGeneratedAt"), "publication.sourceGeneratedAt")


def _clock(value: Any, label: str) -> datetime:
    result = parse_datetime(value)
    if result is None:
        raise PublicationError(f"{label} benötigt einen gültigen Zeitpunkt mit Zeitzone.")
    return result


def _snapshot(row: dict[str, Any], source_id: str) -> dict[str, Any]:
    result = {name: row.get(name) for name in SAFETY_FIELDS}
    for name in ("kickoff", "start", "end", "occupancyStart", "occupancyEnd"):
        result[name] = _clock(result[name], name).isoformat()
    return {"sourceId": source_id, **result}


def _hold_id(row: dict[str, Any], source_id: str) -> str:
    return "dfb:" + HOLD_PREFIX + _digest(_snapshot(row, source_id))


def validate_frozen_match(item: dict[str, Any]) -> None:
    """A retained interval keeps its original duration, never a new shorter rule."""
    clocks = {name: _clock(item.get(name), name)
              for name in ("kickoff", "match_end", "event_start", "event_end")}
    duration = item.get("match_duration_minutes")
    if (type(duration) is not int or duration <= 0
            or not item.get("duration_rule") or not item.get("competition_format")
            or clocks["match_end"] - clocks["kickoff"] != timedelta(minutes=duration)
            or clocks["kickoff"] - clocks["event_start"] != timedelta(minutes=60)
            or clocks["event_end"] - clocks["match_end"] != timedelta(minutes=60)):
        raise PublicationError("Zurückgehaltene Spiel-/Sperrzeiten sind widersprüchlich.")


def validate_retained_match(item: dict[str, Any]) -> None:
    validate_frozen_match(item)
    retention = item.get("publication_retention")
    if not isinstance(retention, dict) or retention.get("schemaVersion") != 1:
        raise PublicationError("Der Herkunftsnachweis einer zurückgehaltenen Sperre fehlt.")
    source_id = str(retention.get("sourceId") or "")
    if (not source_id.startswith("dfb:") or source_id == "dfb:"
            or source_id.startswith("dfb:" + HOLD_PREFIX)):
        raise PublicationError("Eine zurückgehaltene Sperre hat keine gültige Quell-ID.")
    row = {name: item.get(raw) for name, raw in RAW_FIELDS.items()}
    row["place"] = {"Rasen": "rasen", "Kunstrasen": "kunstrasen"}.get(item.get("calendar"))
    if "dfb:" + str(item.get("external_id")) != _hold_id(row, source_id):
        raise PublicationError("Kennung und unveränderte Rückhaltesperre widersprechen sich.")
    _clock(retention.get("sourceObservedAt"), "retention.sourceObservedAt")
    if type(retention.get("requiresManual")) is not bool:
        raise PublicationError("Die Freigabestufe einer Rückhaltesperre fehlt.")


def retained_presentation(row: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(row)
    if row.get("publicationRetention"):
        row["title"] = "Sperre bis Klärung: " + normalize_match_title(
            str(row.get("homeTeam") or ""), str(row.get("awayTeam") or ""))
        row["description"] = normalize_match_description(
            str(row.get("teamCategory") or ""), str(row.get("competition") or ""))
        row["description"] += " · Bisherige Belegung bleibt bis zur bestätigten Rücknahme gesperrt."
    return row


def _read_bundle(directory: Path, *, candidate: bool, config: dict[str, Any]) -> dict[str, Any]:
    feed = _load(directory / "matches.json", dict)
    if (feed.get("schemaVersion") != 2 or feed.get("status") != "ok"
            or not isinstance(feed.get("matches"), list)):
        raise PublicationError("Der Spielbestand benötigt das freigegebene Schema 2.")
    indexed = index_matches(feed, label="Spielbestand")
    observed_at = _clock(feed.get("generatedAt"), "matches.generatedAt")
    raw_payload = _load(directory / "included_matches.json", (dict, list))
    if candidate and not isinstance(raw_payload, list):
        raise PublicationError("Ein Quellkandidat darf kein internes Veröffentlichungsformat enthalten.")
    included = decode_included_matches(raw_payload)
    if isinstance(raw_payload, dict) and raw_payload.get("publication") != feed.get("publication"):
        raise PublicationError("Primärfeed und versionierter Rohbestand stammen nicht aus derselben Veröffentlichung.")
    if feed.get("publication"):
        validate_publication_evidence(feed["publication"], included)
    raw_by_id: dict[str, dict[str, Any]] = {}
    for raw in included:
        if not isinstance(raw, dict):
            raise PublicationError("Der Rohbestand enthält einen ungültigen Datensatz.")
        match_id = "dfb:" + str(raw.get("external_id") or "").strip()
        if match_id == "dfb:" or match_id in raw_by_id:
            raise PublicationError("Der Rohbestand benötigt eindeutige stabile IDs.")
        if match_id not in indexed:
            raise PublicationError("JSON-Feed und Rohbestand enthalten unterschiedliche IDs.")
        row = indexed[match_id]
        if any(row.get(name, "") != raw.get(raw_name, "") for name, raw_name in RAW_FIELDS.items()):
            raise PublicationError(f"JSON-Feed und Rohbestand widersprechen sich: {match_id}.")
        calendar = str(raw.get("calendar") or "")
        if calendar not in {"Rasen", "Kunstrasen"} or row.get("place") != calendar.lower():
            raise PublicationError(f"Die Platzzuordnung ist widersprüchlich: {match_id}.")
        # Constructing the shared model also checks all mandatory source fields.
        try:
            Match(**{name: value for name, value in raw.items() if name in MATCH_FIELDS})
        except TypeError as exc:
            raise PublicationError(f"Der Rohbestand ist unvollständig: {match_id}.") from exc
        retention = raw.get("publication_retention") or {}
        if bool(retention) != bool(row.get("publicationRetention")):
            raise PublicationError("JSON-Feed und Rohbestand haben verschiedene Rückhaltegründe.")
        if candidate and (retention or row.get("publicationRetention")
                          or match_id.startswith("dfb:" + HOLD_PREFIX)):
            raise PublicationError("Eine Quellantwort darf keine Rückhaltesperren vorgeben.")
        if retention:
            validate_retained_match(raw)
            if _clock(retention["sourceObservedAt"], "retention.sourceObservedAt") > observed_at:
                raise PublicationError("Der Herkunftsnachweis liegt nach dem aktuellen Abruf.")
            if row.get("publicationRetention") != retention:
                raise PublicationError("JSON-Feed und Rohbestand haben verschiedene Rückhaltegründe.")
        else:
            validate_frozen_match(raw)
            if candidate:
                try:
                    validate_timing(raw, config)
                except (ValueError, TypeError) as exc:
                    raise PublicationError(f"Ungültige aktuelle Zeitregel: {match_id}.") from exc
        raw_by_id[match_id] = raw
    if set(indexed) != set(raw_by_id):
        raise PublicationError("JSON-Feed und Rohbestand enthalten unterschiedliche Anzahlen.")
    result = {"feed": feed, "indexed": indexed, "raw": raw_by_id, "observed_at": observed_at}
    if not candidate:
        return result
    summary = _load(directory / "summary.json", dict)
    quality = _load(directory / "quality_report.json", dict)
    failed = _load(directory / "failed_teams.json", list)
    all_rows = _load(directory / "all_matches.json", list)
    review = _load(directory / "review_matches.json", list)
    excluded = _load(directory / "excluded_matches.json", list)
    audits = quality.get("window_audits")
    if (summary.get("publishable") is not True or quality.get("publishable") is not True
            or failed or review or summary.get("review") != 0 or quality.get("errors") != []
            or summary.get("quality_errors") != [] or quality.get("invalid_included") != []
            or not isinstance(audits, list) or not audits):
        raise PublicationError("Die Quelle ist fehlgeschlagen, unvollständig oder nicht geprüft.")
    if (_clock(summary.get("generated_at"), "summary.generated_at") != observed_at
            or summary.get("included") != len(included)
            or summary.get("total") != len(all_rows)
            or summary.get("excluded") != len(excluded)):
        raise PublicationError("Quellzählung oder Abrufzeit widersprechen dem Kandidaten.")
    # Recheck the actual partition, not just the claimed counts/quality flag.
    if (sorted(_digest(row) for row in all_rows)
            != sorted(_digest(row) for row in included + excluded + review)):
        raise PublicationError("Die Quellantwort ist nicht vollständig partitioniert.")
    if any(not isinstance(row, dict) or row.get("decision") != "exclude" for row in excluded):
        raise PublicationError("Die ausgeschlossenen Quelldaten sind widersprüchlich.")
    if any(raw.get("decision") != "include" for raw in included):
        raise PublicationError("Nicht freigegebene Quellenzeile im Spielbestand.")
    source_ids = [str(row.get("external_id") or "") for row in all_rows]
    if any(not key for key in source_ids) or len(set(source_ids)) != len(source_ids):
        raise PublicationError("Die gesamte Quellantwort enthält leere oder doppelte IDs.")
    if any(row.get("publication_retention") or str(row.get("external_id") or "").startswith(HOLD_PREFIX)
           for row in all_rows):
        raise PublicationError("Die Quelle darf keine internen Rückhaltesperren vorgeben.")
    for audit in audits:
        if (not isinstance(audit, dict) or type(audit.get("accepted")) is not bool
                or audit.get("missing_detail_ids")
                or audit.get("duplicate_detail_ids")
                or (audit.get("accepted") is True and (
                    audit.get("truncated") is not False or audit.get("has_more") is not False
                    or not isinstance(audit.get("missing_detail_ids"), list)
                    or not isinstance(audit.get("duplicate_detail_ids"), list)))):
            raise PublicationError("Ein Abruffenster ist unvollständig oder möglicherweise gekürzt.")
    try:
        actual_quality = evaluate_quality(
            [Match(**{name: value for name, value in row.items() if name in MATCH_FIELDS})
             for row in all_rows], audits, config, int(quality.get("request_count", 0)))
    except (KeyError, TypeError, ValueError) as exc:
        raise PublicationError("Die vollständige Quellenprüfung konnte nicht wiederholt werden.") from exc
    if not actual_quality["publishable"]:
        raise PublicationError("Die erneute Quellenprüfung ist fehlgeschlagen: " + " | ".join(actual_quality["errors"]))
    counts = {name: sum(raw["calendar"] == name for raw in included) for name in ("Rasen", "Kunstrasen")}
    if summary.get("by_calendar") != counts or quality.get("by_calendar") != counts:
        raise PublicationError("Die Quellzählung je Platz ist widersprüchlich.")
    result.update(summary=summary, quality=quality, all_rows=all_rows, excluded=excluded)
    return result


def _covered(row: dict[str, Any], audits: list[dict[str, Any]]) -> bool:
    day = _clock(row["kickoff"], "kickoff").astimezone(LOCAL_TZ).date().isoformat()
    return any(audit.get("accepted") is True
               and str(audit.get("date_from", "")) <= day <= str(audit.get("date_to", ""))
               for audit in audits)


def _covers(new: dict[str, Any] | None, old: dict[str, Any]) -> bool:
    if new is None or any(new.get(name) != old.get(name) for name in ("calendar", "place")):
        return False
    start, end = occupancy_bounds(old)
    new_start, new_end = occupancy_bounds(new)
    return bool(start and end and new_start and new_end and new_start <= start and new_end >= end)


def _observe(previous: Any, *, fingerprint: str, observed_at: datetime,
             interval: timedelta, required: int) -> dict[str, Any]:
    """Malformed/replayed/future state cannot manufacture another observation."""
    count = 1
    first = last = observed_at
    if isinstance(previous, dict):
        old_count = previous.get("confirmations")
        old_first = parse_datetime(previous.get("firstSeenAt"))
        old_last = parse_datetime(previous.get("lastCountedAt"))
        if (previous.get("fingerprint") == fingerprint and type(old_count) is int
                and 1 <= old_count < required and old_first and old_last
                and old_first <= old_last <= observed_at
                and old_last - old_first >= interval * (old_count - 1)):
            first, last, count = old_first, old_last, old_count
            if observed_at - last >= interval:
                count += 1
                last = observed_at
    return {"fingerprint": fingerprint, "confirmations": count,
            "firstSeenAt": first.isoformat(), "lastCountedAt": last.isoformat()}


def _read_state(before_dir: Path) -> dict[str, Any]:
    try:
        state = _load(before_dir / "publication_state.json", dict)
    except PublicationError:
        return {}
    if state.get("schemaVersion") != 2 or not isinstance(state.get("pending"), dict):
        return {}
    return state["pending"]


def prepare_bundle(*, before_dir: Path, candidate_dir: Path, output_dir: Path,
                   config: dict[str, Any], now: datetime,
                   required_confirmations: int = 2, minimum_interval_minutes: int = 60,
                   allow_destructive: bool = False) -> dict[str, Any]:
    if required_confirmations < 2 or minimum_interval_minutes < 1:
        raise PublicationError("Mindestens zwei getrennte Abrufe mit positivem Abstand sind erforderlich.")
    if now.tzinfo is None or now.utcoffset() is None:
        raise PublicationError("Die Ausführungszeit benötigt eine Zeitzone.")
    now = now.astimezone(UTC)
    paths = [path.resolve() for path in (before_dir, candidate_dir, output_dir)]
    if (paths[0] == paths[1] or output_dir.exists()
            or any(paths[2].is_relative_to(path) or path.is_relative_to(paths[2]) for path in paths[:2])):
        raise PublicationError("Quellstand und neue Ausgabe müssen getrennte Verzeichnisse sein.")
    candidate = _read_bundle(candidate_dir, candidate=True, config=config)
    observed_at = candidate["observed_at"]
    if observed_at > now + timedelta(minutes=5):
        raise PublicationError("Die Abrufzeit liegt unzulässig in der Zukunft.")
    # An empty observation must never refresh freshness and thereby mask a source failure.
    if not candidate["indexed"] and not allow_destructive:
        raise PublicationError("Ein leerer Abruf ersetzt ohne ausdrückliche Prüfung keinen veröffentlichten Stand.")
    if (before_dir / "matches.json").exists():
        before = _read_bundle(before_dir, candidate=False, config=config)
    else:
        if before_dir.exists() and any(before_dir.iterdir()):
            raise PublicationError("Der bisherige Veröffentlichungsstand ist unvollständig.")
        before = {"feed": {"matches": []}, "indexed": {}, "raw": {}, "observed_at": observed_at}
    if observed_at < before["observed_at"]:
        raise PublicationError("Ein älterer Abruf darf den aktuellen Stand nicht ersetzen.")
    source_path = before_dir / "source_matches.json"
    canonical_before = {key: row for key, row in before["indexed"].items() if not row.get("publicationRetention")}
    previous_publication = before["feed"].get("publication", {})
    if not isinstance(previous_publication, dict):
        raise PublicationError("Der bisherige Veröffentlichungsnachweis ist beschädigt.")
    if previous_publication and (
        previous_publication.get("schemaVersion") != 1
        or previous_publication.get("sourceCount") != len(canonical_before)
        or previous_publication.get("retainedCount") != len(before["indexed"]) - len(canonical_before)
        or previous_publication.get("effectiveCount") != len(before["indexed"])
        or _clock(previous_publication.get("sourceGeneratedAt"), "publication.sourceGeneratedAt") != before["observed_at"]
    ):
        raise PublicationError("Der bisherige Veröffentlichungsnachweis enthält widersprüchliche Zählungen oder Zeiten.")
    if source_path.exists():
        previous_source = _load(source_path, dict)
        if (_digest(previous_source) != previous_publication.get("sourceDigest")
                or previous_source.get("schemaVersion") != 2 or previous_source.get("status") != "ok"
                or not isinstance(previous_source.get("matches"), list)
                or _clock(previous_source.get("generatedAt"), "source_matches.generatedAt") != before["observed_at"]):
            raise PublicationError("Der gespeicherte Quellenstand stimmt nicht mit seinem Veröffentlichungsnachweis überein.")
        source_before = index_matches(previous_source, label="Bisherige Quelle")
        if source_before != canonical_before:
            raise PublicationError("Bisherige Quelle und wirksamer Quellbestand widersprechen sich.")
    else:
        # Legacy bundles have no source sidecar; held intervals are never counted
        # as newly observed source rows. Their manual gate remains in each row.
        source_before = canonical_before
    source_hash = _digest(candidate["feed"])
    old_hash = previous_publication.get("sourceDigest")
    if observed_at == before["observed_at"] and old_hash and old_hash != source_hash:
        raise PublicationError("Dieselbe Abrufzeit wurde mit widersprüchlichen Daten wiederholt.")
    added, changed, removed = compare_matches(source_before, candidate["indexed"])
    mass_reasons = destructive_guard(
        before_count=len(source_before), after_count=len(candidate["indexed"]), removed_count=len(removed),
        max_removal_ratio=0.60, min_previous_for_ratio=10, min_removed_for_ratio=5)
    state = _read_state(before_dir)
    next_state: dict[str, Any] = {}
    effective = deepcopy(candidate["indexed"])
    effective_raw = deepcopy(candidate["raw"])
    retained: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []
    interval = timedelta(minutes=minimum_interval_minutes)
    for old_id, old in sorted(before["indexed"].items()):
        old_retention = old.get("publicationRetention") or {}
        source_id = str(old_retention.get("sourceId") or old_id)
        new = candidate["indexed"].get(source_id)
        if _covers(new, old):
            continue
        _, old_end = occupancy_bounds(old)
        if old_end and old_end < now:
            if old_retention:
                released.append({"id": old_id, "sourceId": source_id, "reason": "expired"})
            continue
        hold_id = _hold_id(old, source_id)
        proposal = {"before": _snapshot(old, source_id),
                    "after": _snapshot(new, source_id) if new else None}
        observation = _observe(state.get(hold_id), fingerprint=_digest(proposal), observed_at=observed_at,
                               interval=interval, required=required_confirmations)
        manual_required = bool(mass_reasons or old_retention.get("requiresManual")
                               or not _covered(old, candidate["quality"]["window_audits"]))
        confirmed = observation["confirmations"] >= required_confirmations and not manual_required
        if confirmed or allow_destructive:
            released.append({"id": old_id, "sourceId": source_id,
                             "reason": "manual_override" if allow_destructive else "confirmed",
                             **observation})
            continue
        # Manual holds remain latched even after source counts have already shrunk.
        if manual_required and observation["confirmations"] >= required_confirmations:
            observation["confirmations"] = required_confirmations - 1
        retention = {
            "schemaVersion": 1, "sourceId": source_id, "status": "pending",
            "reason": "removed" if new is None else "moved_or_shortened",
            "sourceObservedAt": old_retention.get("sourceObservedAt") or before["observed_at"].isoformat(),
            "requiresManual": manual_required, "requiredConfirmations": required_confirmations,
            "minimumIntervalMinutes": minimum_interval_minutes, **observation,
        }
        row = deepcopy(old)
        row.update(id=hold_id, publicationRetention=retention)
        row = retained_presentation(row)
        raw = deepcopy(before["raw"][old_id])
        raw.update(external_id=hold_id.removeprefix("dfb:"), publication_retention=retention)
        validate_retained_match(raw)
        if hold_id in effective and effective[hold_id] != row:
            raise PublicationError("Zwei unterschiedliche Sperren besitzen dieselbe Kennung.")
        effective[hold_id], effective_raw[hold_id] = row, raw
        next_state[hold_id] = observation
        retained.append({"id": hold_id, **retention})
    rows = sorted(effective.values(), key=lambda row: (row["start"], row["id"]))
    raw_rows = [effective_raw[row["id"]] for row in rows]
    publication = {
        "schemaVersion": 1, "status": "additive_pending" if retained else "ok",
        "sourceGeneratedAt": observed_at.isoformat(), "sourceDigest": source_hash,
        "sourceCount": len(candidate["indexed"]), "retainedCount": len(retained),
        "effectiveCount": len(rows),
        "requiredConsumer": REQUIRED_CONSUMER if retained else None,
        "effectiveDigest": _digest(raw_rows),
    }
    feed = {**deepcopy(candidate["feed"]), "matches": rows, "publication": publication}
    reasons = list(mass_reasons)
    if retained:
        reasons.append(f"{len(retained)} bisherige Sperre(n) bleiben bis zur bestätigten Rücknahme bestehen; neue Sperren sind enthalten.")
    override_used = any(item["reason"] == "manual_override" for item in released)
    report = {
        "schemaVersion": 2, "generatedAt": now.isoformat(),
        "status": "additive_pending" if retained else ("approved_override" if override_used else "ok"),
        "counts": {"before": len(source_before), "after": len(candidate["indexed"]),
                   "added": len(added), "changed": len(changed), "removed": len(removed),
                   "effectiveAfter": len(rows), "retained": len(retained), "released": len(released)},
        "guard": {"triggered": bool(retained or mass_reasons), "overrideUsed": override_used,
                  "reasons": reasons, "safetyConfirmation": {"required": bool(retained), "confirmed": not retained}},
        "added": added, "changed": changed, "removed": removed,
        "retained": retained, "released": released, "publication": publication,
    }
    summary = deepcopy(candidate["summary"])
    all_rows = raw_rows + deepcopy(candidate["excluded"])
    calendar_counts = {name: sum(row["calendar"] == name for row in raw_rows) for name in ("Rasen", "Kunstrasen")}
    summary.update(total=len(all_rows), included=len(raw_rows), by_calendar=calendar_counts, publication=publication)
    summary["by_team"] = {
        team: {"total": sum(row["team_name"] == team for row in all_rows),
               "included": sum(row["team_name"] == team for row in raw_rows),
               "excluded": sum(row["team_name"] == team for row in candidate["excluded"]), "review": 0}
        for team in sorted({row["team_name"] for row in all_rows})}
    quality = {**deepcopy(candidate["quality"]), "by_calendar": calendar_counts, "publication": publication}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output_dir.name + ".", dir=output_dir.parent))
    try:
        payloads = {
            "matches.json": feed,
            "included_matches.json": ({"schemaVersion": 2, "kind": "retained-occupancy",
                                       "requiredConsumer": REQUIRED_CONSUMER,
                                       "publication": publication, "matches": raw_rows}
                                      if retained else raw_rows),
            "all_matches.json": all_rows,
            "summary.json": summary, "quality_report.json": quality,
            "failed_teams.json": [], "review_matches.json": [], "excluded_matches.json": candidate["excluded"],
            "source_matches.json": candidate["feed"], "source_summary.json": candidate["summary"],
            "source_quality_report.json": candidate["quality"], "source_all_matches.json": candidate["all_rows"],
            "publication_state.json": {"schemaVersion": 2, "sourceGeneratedAt": observed_at.isoformat(), "pending": next_state},
            "change_report.json": report,
        }
        for name, payload in payloads.items():
            write_json(staging / name, payload)
        registry = candidate_dir / "team_registry.json"
        if registry.exists():
            shutil.copyfile(registry, staging / registry.name)
        with (staging / "appack_preview.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            columns = sorted(set().union(*(row.keys() for row in all_rows))) if all_rows else sorted(MATCH_FIELDS)
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in all_rows:
                writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                                 for key, value in row.items()})
        models = [Match(**{name: value for name, value in row.items() if name in MATCH_FIELDS}) for row in raw_rows]
        for calendar in ("Rasen", "Kunstrasen"):
            write_ics(staging / f"{calendar.lower()}.ics", [m for m in models if m.calendar == calendar],
                      f"SSV53 {calendar} – Spiele")
        _read_bundle(staging, candidate=False, config=config)
        staging.replace(output_dir)
    finally:
        if staging.exists() and staging.resolve().is_relative_to(output_dir.parent.resolve()):
            shutil.rmtree(staging)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--required-confirmations", type=int, default=2)
    parser.add_argument("--minimum-confirmation-minutes", type=int, default=60)
    parser.add_argument("--allow-destructive", action="store_true")
    args = parser.parse_args()
    try:
        report = prepare_bundle(before_dir=args.before, candidate_dir=args.candidate, output_dir=args.output,
                                config=_load(args.config, dict), now=datetime.now(UTC),
                                required_confirmations=args.required_confirmations,
                                minimum_interval_minutes=args.minimum_confirmation_minutes,
                                allow_destructive=args.allow_destructive)
    except (PublicationError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    write_json(args.json, report)
    markdown = markdown_report(report)
    write_text(args.markdown, markdown)
    append_github_summary(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
