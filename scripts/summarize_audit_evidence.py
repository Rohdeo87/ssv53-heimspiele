"""Extract bounded, identity-free incident evidence from a local read-only export."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import subprocess

from scripts.analyze_control_observations import read_export, utc


def run(export, output):
    rows = sorted(read_export(json.loads(export.read_text(encoding="utf-8-sig"))), key=lambda x: utc(x["timestamp"]))
    episodes, resets = [], []
    ongoing = None
    previous = None
    fields = ("timestamp", "code", "activity", "error", "hydraAvailable", "hydraFresh", "activeZones", "clearSince", "clearOrigin", "phase", "completed")
    for row in rows:
        error = int(row.get("error") or 0)
        continuous = previous is not None and utc(row["timestamp"]) - utc(previous["timestamp"]) <= timedelta(seconds=90)
        if ongoing and (ongoing["error_code"] != error or not continuous):
            episodes.append(ongoing)
            ongoing = None
        if error:
            if ongoing is None:
                ongoing = {"error_code": error, "first_observation_utc": row["timestamp"], "last_observation_utc": row["timestamp"], "samples": 0}
            ongoing["last_observation_utc"] = row["timestamp"]
            ongoing["samples"] += 1
        if previous and previous.get("clearSince") and previous.get("clearSince") != row.get("clearSince"):
            if previous.get("phase") == "COMPLETE_HOLD" or row.get("phase") == "COMPLETE_HOLD":
                resets.append({"before": {key: previous.get(key) for key in fields}, "after": {key: row.get(key) for key in fields}})
        previous = row
    if ongoing:
        episodes.append(ongoing)
    for episode in episodes:
        episode["observed_span_minutes"] = round((utc(episode["last_observation_utc"]) - utc(episode["first_observation_utc"])).total_seconds()/60, 2)
    incident_window = [{key: row.get(key) for key in fields} for row in rows if "2026-09-08T08:04" <= row["timestamp"] < "2026-09-08T08:15"]
    summary = {"source": "Application Insights read-only export, UTC", "contains_device_or_person_ids": False, "rows": len(rows), "error_episodes": episodes,
               "september_8_recovery_excerpt_utc": incident_window,
               "complete_hold_clear_clock_changes": resets, "limitations": ["An observed error code is not proof of a hardware root cause.", "Clock changes are observations; causality and additional physical idle time require replay.", "Missing cycles split episodes; no interpolation through telemetry gaps."]}
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()
    (output.parent / "repository-inventory.txt").write_text("Tracked paths at audit baseline; depth and coverage are explained in architecture.md.\n" + "\n".join(tracked) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "error_episodes": len(episodes), "complete_hold_clock_changes": len(resets), "tracked_paths": len(tracked)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.export, args.output)
