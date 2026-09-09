"""Replay identical synthetic observations against real historical/current code.

Run: python -m scripts.replay_hydrawise_gap_audit --output docs/audit-2026-09-09/gap-replay.json
Only git object reads and local calculations are used. No device/API calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mower.hydrawise import evaluate_continuous_clear_confirmation
from mower.state import AutomationState


ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "9c2d0fc"
END = datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc)
PHYSICAL_ORIGINS = {"IRRIGATION_ACTIVE", "IRRIGATION_END", "POSSIBLE_IRRIGATION_DURING_GAP"}


def baseline_module(path: str):
    source = subprocess.run(
        ["git", "show", f"{BASE_REVISION}:{path}"], cwd=ROOT,
        check=True, capture_output=True,
    ).stdout
    name = "_audit_baseline_" + Path(path).stem
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, f"{BASE_REVISION}:{path}", "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


def replay(state_class, evaluate, *, internal: bool, failed_minutes: list[int], current: bool):
    state = state_class(
        irrigation_phase="COMPLETE_HOLD" if internal else None,
        hydrawise_clear_origin="IRRIGATION_ACTIVE", last_hydrawise_active_count=1,
        last_hydrawise_success_utc=(END - timedelta(minutes=1)).isoformat(),
    )
    first_release = None
    recovery = max(failed_minutes) + 1
    samples = []
    for minute in range(361):
        now = END + timedelta(minutes=minute)
        available = minute not in failed_minutes
        state = state.record_cycle(
            started_utc=now, success=available, decision_code="SYNTHETIC_OBSERVATION",
            hydrawise_success_utc=now if available else None,
            hydrawise_observed_utc=now if available else None,
            hydrawise_clear=available, hydrawise_active_count=0 if available else None,
        )
        # Persist/restore every observation to include restart serialization.
        state = state_class.from_mapping(state.to_dict())
        origin = state.hydrawise_clear_origin or ("IRRIGATION_END" if state.irrigation_phase == "COMPLETE_HOLD" else "DATA_GAP")
        options = {"drying_since_utc": state.hydrawise_drying_since_utc, "telemetry_confirmation_minutes": 2} if current else {}
        release = evaluate(
            available=available, fresh=available, clear_now=available,
            physical_reason="synthetic clear" if available else "synthetic missing poll",
            clear_since_utc=state.hydrawise_clear_since_utc, now_utc=now,
            required_clear_minutes=150 if origin in PHYSICAL_ORIGINS else 2,
            persistent_state_available=True, **options,
        )
        if release.allowed and first_release is None:
            first_release = minute
        if minute in {min(failed_minutes) - 1, *failed_minutes, recovery, recovery + 2, 150, 152, 300}:
            samples.append({
                "at_utc": now.isoformat(), "available": available, "origin": origin,
                "clear_since_utc": state.hydrawise_clear_since_utc,
                "drying_since_utc": getattr(state, "hydrawise_drying_since_utc", None),
                "release": release.to_dict(),
            })
    return {
        "first_release_minutes_after_end": first_release,
        "first_release_utc": (END + timedelta(minutes=first_release)).isoformat() if first_release is not None else None,
        "samples": samples,
    }


def comparison():
    old_state, state_hash = baseline_module("mower/state.py")
    old_hydrawise, hydrawise_hash = baseline_module("mower/hydrawise.py")
    cases = []
    for name, internal, failed in (
        ("internal_one_failed_poll_near_end", True, [149]),
        ("external_one_failed_poll_early", False, [20]),
        ("internal_240_second_gap_near_end", True, [147, 148, 149]),
        ("external_240_second_gap_early", False, [20, 21, 22]),
    ):
        old = replay(old_state.AutomationState, old_hydrawise.evaluate_continuous_clear_confirmation,
                     internal=internal, failed_minutes=failed, current=False)
        new = replay(AutomationState, evaluate_continuous_clear_confirmation,
                     internal=internal, failed_minutes=failed, current=True)
        cases.append({
            "name": name, "failed_minutes": failed, "internal_complete_hold": internal,
            "baseline": old, "updated": new,
            "release_shift_minutes_later": new["first_release_minutes_after_end"] - old["first_release_minutes_after_end"],
        })
    return {
        "evidence_kind": "SYNTHETIC_CODE_REPLAY_NOT_OPERATIONAL_MEASUREMENT",
        "base_commit": subprocess.run(["git", "rev-parse", BASE_REVISION], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip(),
        "baseline_source_sha256": {"mower/state.py": state_hash, "mower/hydrawise.py": hydrawise_hash},
        "updated_source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in ("mower/state.py", "mower/hydrawise.py")},
        "assumptions": [
            "Observed irrigation end at 07:00 UTC; no further actual irrigation in this synthetic trace.",
            "One-minute polls, persisted state, 150-minute physical rule, two-minute data confirmation, 180-second gap bound.",
            "No mower, charging, occupancy, hydraulic sensors or manufacturer queue is simulated; a release is not productive mowing.",
            "During a 240-second data gap unobserved irrigation remains possible even though this synthetic trace inserts no new water event.",
        ],
        "cases": cases,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = comparison()
    serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
        print(json.dumps([{"case": case["name"], "before": case["baseline"]["first_release_utc"],
                           "after": case["updated"]["first_release_utc"], "shift_minutes": case["release_shift_minutes_later"]}
                          for case in report["cases"]], indent=2))
    else:
        print(serialized)
