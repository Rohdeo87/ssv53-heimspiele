from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from mower.manual_water_conflict import water_conflict_context
from mower.state import AutomationState


NOW = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)
RELAYS = [11, 12]


def details(*, active=(), next_start: datetime | None = None):
    start = next_start or NOW + timedelta(hours=1)
    rows = []
    for zone, relay in enumerate(RELAYS, 1):
        end = start + timedelta(minutes=10)
        rows.append({
            "valid": True, "scheduled": True, "running": relay in active,
            "relay_id": relay, "zone": zone, "run_seconds": 600,
            "scheduled_start_utc": start.isoformat(),
            "scheduled_end_utc": end.isoformat(),
        })
        start = end
    return {
        "hydrawise": {
            "zone_observations": rows,
            "safety": {
                "available": True, "fresh": True, "relay_set_valid": True,
                "observed_relay_ids": RELAYS, "expected_relay_ids": RELAYS,
                "active_relay_ids": list(active), "active_zone_count": len(active),
                "imminent_relay_ids": [], "imminent_zone_count": 0,
            },
        },
        "current_plan": {},
    }


def test_active_native_conflict_id_is_stable_across_state_projection():
    payload = details(active=(11,))
    first = water_conflict_context(AutomationState(), payload, NOW)
    projected = AutomationState(next_irrigation_start_utc=(NOW + timedelta(hours=1)).isoformat())
    second = water_conflict_context(projected, payload, NOW)
    assert first["known"] is True
    assert first["id"] == second["id"]


def test_future_run_is_not_a_conflict_before_ten_minute_horizon():
    context = water_conflict_context(
        AutomationState(next_irrigation_start_utc=(NOW + timedelta(minutes=11)).isoformat()),
        details(next_start=NOW + timedelta(minutes=11)), NOW,
    )
    assert context["required"] is False
    assert context["id"] is None


def test_imminent_run_becomes_exact_known_conflict():
    context = water_conflict_context(
        AutomationState(next_irrigation_start_utc=(NOW + timedelta(minutes=10)).isoformat()),
        details(next_start=NOW + timedelta(minutes=10)), NOW,
    )
    assert context["required"] is True
    assert context["known"] is True
    assert isinstance(context["id"], str)


def test_malformed_relay_ids_fail_closed_without_exception():
    payload = deepcopy(details(active=(11,)))
    payload["hydrawise"]["safety"]["expected_relay_ids"] = ["invalid", 12]
    context = water_conflict_context(AutomationState(), payload, NOW)
    assert context["required"] is True
    assert context["known"] is False
    assert context["id"] is None
