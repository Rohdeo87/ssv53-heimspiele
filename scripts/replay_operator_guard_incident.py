"""Replay selected actual observations without credentials, network or devices.

Each case starts with an independent empty in-memory command journal. This
compares decisions on recorded inputs; it does not simulate physical command
delivery, a changed subsequent trajectory, or minutes of prevented overlap.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mower.operator_controls import run_operator_cycle
from mower.runtime import CycleResult, RuntimeSettings
from mower.state_store import InMemoryStateStore

CASES = {
    "before_water": ("2026-09-10T01:50:", True),
    "active_water": ("2026-09-10T02:30:", True),
    "charging_during_water": ("2026-09-10T02:54:", True),
    "leaving_during_water": ("2026-09-10T03:45:", True),
    "manual_pause": ("2026-09-10T04:28:", False),
}


def replay(input_path: Path) -> dict:
    raw = input_path.read_bytes()
    rows = json.loads(raw)["tables"][0]["rows"]
    cases = []
    for name, (prefix, must_park) in CASES.items():
        row = next(row for row in rows if row[0].startswith(prefix))
        payload = json.loads(row[1].split(" ", 1)[1])
        observation = CycleResult(**{key: payload[key] for key in CycleResult.__dataclass_fields__})
        now = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        mower = observation.details["mower"]
        outcomes = {}
        for enabled in (False, True):
            store = InMemoryStateStore()
            calls = []
            environment = {
                "CONTROL_MODE": "OPERATOR_ONLY", "ENABLE_LIVE_READS": "true",
                "ENABLE_PARK_COMMANDS": "true", "ENABLE_START_COMMANDS": "false",
                "ENABLE_IRRIGATION_COMMANDS": "false",
                "ENABLE_OPERATOR_SAFETY_GUARD": str(enabled).lower(),
                "OPERATOR_CONTROL_CONFIRMATION": "SSV53-OPERATOR-PARK-HEIGHT-V1",
                "HUSQVARNA_MOWER_ID": mower["mower_id"],
            }

            def park(_client_id, _secret, target, *, before_send):
                assert target == mower["mower_id"]
                before_send()
                calls.append("PARK_MOWER")
                return {"fake": True}

            def forbidden(*_args, **_kwargs):
                raise AssertionError("No other device sender is permitted")

            with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
                result = run_operator_cycle(
                    now_utc=now, settings=RuntimeSettings.from_mapping(environment),
                    environment=environment, past_due=False, source="offline-incident-replay",
                    read_only_runner=lambda **_kwargs: copy.deepcopy(observation),
                    state_store_factory=lambda _environment: store,
                    mower_reader=forbidden, park_sender=park,
                    cutting_height_sender=forbidden, command_clock=lambda: now,
                )
            expected = ["PARK_MOWER"] if enabled and must_park else []
            assert calls == expected, (name, enabled, calls, result.details)
            assert result.command_sent is bool(expected)
            assert store.load().automation_restart_allowed is False
            assert result.details["operatorSafetyGuard"]["protected"] is False
            outcomes["guard_on" if enabled else "guard_off"] = {
                "fake_park_calls": len(calls), "automatic_restart_allowed": False,
                "physical_hold_confirmed": False,
                "guard": result.details["operatorSafetyGuard"],
            }
        cases.append({
            "case": name, "observed_at_utc": row[0],
            "mower_activity": mower["activity"], "mower_state": mower["state"],
            "active_zone_count": observation.details["hydrawise"]["safety"]["active_zone_count"],
            "outcomes": outcomes,
        })
    return {
        "kind": "independent-observation-replay-with-fake-senders",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ("mower/operator_controls.py", "mower/runtime.py")},
        "network_allowed": False, "real_device_commands": 0,
        "physical_prevention_or_mowing_minutes_proven": False, "cases": cases,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases_passed": len(report["cases"]), "real_device_commands": 0}))
