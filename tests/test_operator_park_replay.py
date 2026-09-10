"""Recorded status replay: simulated journal/sender, no live device actions."""
from datetime import datetime
import json
from pathlib import Path

from mower.operator_controls import (
    operator_commands_payload,
    queue_operator_action,
    run_operator_cycle,
)
from mower.runtime import CycleResult, RuntimeSettings
from mower.state_store import InMemoryStateStore


def test_recorded_september_10_station_reports_confirm_home_without_a_repeat():
    evidence = Path(__file__).resolve().parents[1] / "docs/audit-2026-09-10/manual-park-observation.json"
    rows = json.loads(evidence.read_text(encoding="utf-8"))["observations"]
    sent_row = next(row for row in rows if row["command_sent"])
    requested_at = datetime.fromisoformat(sent_row["park_request"]["requestedAt"])
    # Identity is anonymized in the public replay. It remains exact and fixed
    # for both the simulated request and every recorded status snapshot.
    target = "recorded-mower"
    environment = {
        "CONTROL_MODE": "OPERATOR_ONLY", "ENABLE_LIVE_READS": "true",
        "ENABLE_PARK_COMMANDS": "true", "ENABLE_START_COMMANDS": "false",
        "ENABLE_IRRIGATION_COMMANDS": "false", "HUSQVARNA_MOWER_ID": target,
        "OPERATOR_CONTROL_CONFIRMATION": "SSV53-OPERATOR-PARK-HEIGHT-V1",
    }
    settings = RuntimeSettings.from_mapping(environment)
    store = InMemoryStateStore()
    queue_operator_action(
        store, settings, "PARK_MOWER", "recorded-park", requested_at,
        mower_id=target,
    )
    simulated_sends = []

    def sender(*_args, before_send):
        before_send()
        simulated_sends.append("accepted")
        return {"accepted": True}

    outcomes = []
    for row in rows:
        now = datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00"))
        if now < requested_at:
            continue
        observed = CycleResult(
            2, now.isoformat(), "recorded-status-replay", "DRY_RUN", False,
            "READ", False, "READ",
            {"mower": {**row["mower"], "mower_id": target}},
        )
        run_operator_cycle(
            now_utc=now, settings=settings, environment=environment,
            past_due=False, source="recorded-status-replay",
            read_only_runner=lambda **_kwargs: observed,
            state_store_factory=lambda _environment: store,
            command_clock=lambda: now, park_sender=sender,
        )
        outcomes.append((row, operator_commands_payload(store.load())["PARK_MOWER"]["status"]))

    assert simulated_sends == ["accepted"]
    assert outcomes[0][1] == "SENT_UNCONFIRMED"
    station_outcomes = [
        (row, status) for row, status in outcomes
        if row["mower"]["activity"] == "PARKED_IN_CS"
    ]
    assert station_outcomes
    assert all(row["mower"]["mode"] == "HOME" for row, _ in station_outcomes)
    assert all(row["mower"]["override_action"] == "FORCE_MOW" for row, _ in station_outcomes)
    assert all(status == "CONFIRMED" for _, status in station_outcomes)
    # Recorded production still reported UNKNOWN at 08:29. This difference
    # proves the corrected interpretation, not a new physical device result.
    assert any(row["park_request"]["status"] == "UNKNOWN" for row, _ in station_outcomes)
