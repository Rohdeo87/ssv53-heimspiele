"""Read-only training source comparison: python -m occupancy.training_calendar_audit.

No inferred season dates, no publication, no runtime configuration changes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from occupancy.training_calendar import (
    TrainingBatch, _weekday, content_digest, legacy_source_hashes, load_calendar, validate_calendar,
)


def _pattern(item: Mapping[str, Any], *, mower: bool = False) -> dict[str, Any]:
    return {"id": item["id"], "weekday": _weekday(item["weekday"]),
            "start": item["start"], "end": item["end"], "team": item["team"],
            "resource_id": "rasen" if mower else item["resource_id"]}


def _union(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if end <= start:
            raise ValueError("Ein Trainingsintervall muss positiv sein.")
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _subtract(left: list[tuple[datetime, datetime]], right: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in left:
        cursor = start
        for other_start, other_end in right:
            if other_end <= cursor or other_start >= end:
                continue
            if other_start > cursor:
                result.append((cursor, other_start))
            cursor = max(cursor, min(other_end, end))
        if cursor < end:
            result.append((cursor, end))
    return result


def compare_training_blocks(batch: TrainingBatch, existing_training_blocks: Iterable[Any]) -> dict[str, Any]:
    """Report exact UNION differences; never interpret a removal as authorized."""
    def clipped(blocks: Iterable[Any]) -> list[tuple[datetime, datetime]]:
        intervals = []
        for block in blocks:
            if block.source != "training":
                raise ValueError("Der Vergleich akzeptiert ausschließlich Trainingssperren.")
            for value in (block.start, block.end):
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError("Sperrzeit ohne UTC-Offset.")
            start = max(batch.range_start_utc, block.start.astimezone(timezone.utc))
            end = min(batch.range_end_utc, block.end.astimezone(timezone.utc))
            if start < end:
                intervals.append((start, end))
        return _union(intervals)

    before, after = clipped(existing_training_blocks), clipped(batch.mower_blocks())
    removed, added = _subtract(before, after), _subtract(after, before)
    return {
        "calendarRevision": batch.revision, "contentSha256": batch.content_sha256,
        "rangeStartUtc": batch.range_start_utc.isoformat(), "rangeEndUtc": batch.range_end_utc.isoformat(),
        "existingBlockedMinutes": sum((end - start).total_seconds() / 60 for start, end in before),
        "candidateBlockedMinutes": sum((end - start).total_seconds() / 60 for start, end in after),
        "removedBlockedMinutes": sum((end - start).total_seconds() / 60 for start, end in removed),
        "addedBlockedMinutes": sum((end - start).total_seconds() / 60 for start, end in added),
        "removedIntervals": [{"start": start.isoformat(), "end": end.isoformat()} for start, end in removed],
        "addedIntervals": [{"start": start.isoformat(), "end": end.isoformat()} for start, end in added],
        "automaticReleaseAuthorized": False,
        "note": "Vergleich eines vorbereiteten Kalenders; keine Änderung vorhandener Sperren und kein Mähzeitnachweis.",
    }


def source_comparison_report(
    document: Mapping[str, Any], occupancy_config: Mapping[str, Any], mower_config: Mapping[str, Any], *, now_utc: datetime,
) -> dict[str, Any]:
    validation = validate_calendar(document, now_utc=now_utc, occupancy_config=occupancy_config, mower_config=mower_config)
    seasons: dict[str, Any] = {}
    controller = {_pattern(item, mower=True)["id"]: _pattern(item, mower=True)
                  for item in mower_config["training"]["weekly"]}
    for season, content in occupancy_config["seasons"].items():
        app = {item["id"]: _pattern(item) for item in content["weekly"] if item["resource_id"] == "rasen"}
        seasons[season] = {
            "appTrainingCount": len(content["weekly"]), "appRasenTrainingCount": len(app),
            "controllerRasenTrainingCount": len(controller),
            "controllerOnlyScheduleIds": sorted(set(controller) - set(app)),
            "appOnlyScheduleIds": sorted(set(app) - set(controller)),
            "changedPatterns": [{"scheduleId": key, "controller": controller[key], "app": app[key]}
                                for key in sorted(set(app) & set(controller)) if app[key] != controller[key]],
        }
    copied_patterns = {name: value["weekly"] for name, value in occupancy_config["seasons"].items()}
    return {
        "schemaVersion": 1, "generatedAtUtc": now_utc.astimezone(timezone.utc).isoformat(),
        "calendarId": document.get("calendar_id"), "calendarRevision": document.get("revision"),
        "validation": validation.to_dict(),
        "retainExistingTraining": True,
        "writesPerformed": False,
        "sourceHashes": legacy_source_hashes(occupancy_config, mower_config),
        "templatePatternsMatchSource": content_digest(document.get("weekly_patterns")) == content_digest(copied_patterns),
        "templateExclusionsMatchSource": document.get("excluded_occurrences") == occupancy_config.get("cancelled_occurrences", []),
        "legacyMowerActiveRanges": mower_config["training"].get("active_ranges", []),
        "seasonComparisons": seasons,
        "note": "Saisonvarianten sind Quellvergleiche. Ohne bestätigte Umschalttage ist keine Variante ein verbindlicher Tagesplan.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar", type=Path, default=Path("occupancy/training_calendar.template.json"))
    parser.add_argument("--occupancy-config", type=Path, default=Path("occupancy/config.json"))
    parser.add_argument("--mower-config", type=Path, default=Path("mower/config.json"))
    args = parser.parse_args()
    try:
        report = source_comparison_report(
            load_calendar(args.calendar), json.loads(args.occupancy_config.read_text(encoding="utf-8")),
            json.loads(args.mower_config.read_text(encoding="utf-8")), now_utc=datetime.now(timezone.utc),
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"retainExistingTraining": True, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["validation"]["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
