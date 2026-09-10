from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from mower.manual_session import (
    ManualSessionError,
    block_key,
    dump_manual_session,
    load_manual_session,
    new_session,
    observe_session,
    resolve_manual_permissions,
    start_dispatch_permission,
)
from mower.state import AutomationState


NOW = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
BLOCK = {
    "start": "2026-09-10T08:00:00+00:00",
    "end": "2026-09-10T10:00:00+00:00",
    "source": "training",
}


def session(**changes):
    create_fields = {
        "session_id", "epoch", "mower_id", "kind", "source", "now_utc",
        "confirmed_block_keys", "confirmed_dry_until_utc", "water_conflict_id", "water_choice",
    }
    create = {
        "session_id": "session-1", "epoch": 1, "mower_id": "mower-1", "kind": "START", "source": "APP",
        "now_utc": NOW, "confirmed_block_keys": [block_key(BLOCK)],
    }
    create.update({key: value for key, value in changes.items() if key in create_fields})
    value = new_session(**create)
    return {**value, **{key: value for key, value in changes.items() if key not in create_fields}}


def safety(**changes):
    value = {
        "available": True, "fresh": True, "relay_set_valid": True,
        "clear_now": True, "active_zone_count": 0, "active_relay_ids": [],
        "imminent_zone_count": 0, "imminent_relay_ids": [],
    }
    value.update(changes)
    return value


def mower(*, now=NOW, activity="MOWING", mower_id="mower-1"):
    return {
        "mower_id": mower_id, "connected": True, "activity": activity,
        "status_timestamp_ms": int(now.timestamp() * 1000),
    }


def test_session_round_trip_and_state_round_trip_preserve_all_bindings():
    value = session(water_conflict_id="water-1", water_choice="MOWER")
    encoded = dump_manual_session(value)
    assert load_manual_session(encoded) == value
    state = AutomationState(
        manual_session_json=encoded,
        operator_request_session_id="session-1", operator_request_session_epoch=1,
        mower_start_pending_session_id="session-1", mower_start_pending_session_epoch=1,
        manual_water_conflict_json='{"version":1}', device_send_journal_json="[]",
        manual_control_receipts_json="[]",
    )
    assert AutomationState.from_mapping(state.to_dict()) == state


@pytest.mark.parametrize(
    "changes",
    [
        {"mower_id": ""},
        {"status": "ACTIVE", "ended_at_utc": NOW.isoformat()},
        {"confirmed_block_keys": [block_key(BLOCK), block_key(BLOCK)]},
        {"water_conflict_id": "water", "water_choice": None},
    ],
)
def test_malformed_session_is_rejected_at_the_state_boundary(changes):
    with pytest.raises((ManualSessionError, ValueError)):
        bad = session(**changes)
        encoded = dump_manual_session(bad)
        AutomationState(manual_session_json=encoded)


def test_session_permission_binds_exact_blocks_and_never_bypasses_active_or_unknown_water():
    value = session()
    allowed = resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=BLOCK, parking_block=None,
        hydrawise_safety=safety(),
    )
    assert allowed["allowed"] is True
    changed = {**BLOCK, "end": "2026-09-10T10:01:00+00:00"}
    assert resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=changed, parking_block=None,
        hydrawise_safety=safety(),
    )["code"] == "MANUAL_SESSION_BLOCK_CHANGED"
    same_bounds_different_event = {**BLOCK, "event_id": "new-event"}
    assert block_key(same_bounds_different_event) != block_key(BLOCK)
    assert resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=same_bounds_different_event, parking_block=None,
        hydrawise_safety=safety(),
    )["code"] == "MANUAL_SESSION_BLOCK_CHANGED"
    nested_hard_block = {**BLOCK, "bindingClosure": {"source": "special"}}
    assert resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=nested_hard_block, parking_block=None,
        hydrawise_safety=safety(),
    )["code"] == "MANUAL_SESSION_BLOCK_FORBIDDEN"
    assert resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=BLOCK, parking_block=None,
        hydrawise_safety=safety(active_zone_count=1, active_relay_ids=[6]),
    )["code"] == "MANUAL_SESSION_WATER_ACTIVE"


def test_expired_dry_confirmation_keeps_occupancy_approval_but_not_a_new_hold():
    value = session(confirmed_dry_until_utc=NOW + timedelta(minutes=5), status="PENDING")
    ended_hold = resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW + timedelta(minutes=6),
        blocked_now=BLOCK, parking_block=None, hydrawise_safety=safety(),
        hydrawise_release={
            "dry_until_utc": (NOW + timedelta(minutes=5)).isoformat(), "allowed": True,
            "telemetry_confirmed": True, "persistent_state_available": True,
            "current_water_clear": True,
        },
    )
    assert ended_hold["allowed"] is True
    assert ended_hold["dry_override"] is False
    renewed_hold = resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW + timedelta(minutes=6),
        blocked_now=BLOCK, parking_block=None, hydrawise_safety=safety(),
        hydrawise_release={
            "dry_until_utc": (NOW + timedelta(minutes=20)).isoformat(), "allowed": True,
            "telemetry_confirmed": True, "persistent_state_available": True,
            "current_water_clear": True,
        },
    )
    assert renewed_hold["code"] == "MANUAL_SESSION_DRY_CONFIRMATION_CHANGED"


def test_pending_session_expires_with_the_same_preparation_deadline():
    value = session(status="PENDING")
    permission = resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW + timedelta(minutes=10),
        blocked_now=BLOCK, parking_block=None, hydrawise_safety=safety(),
    )
    assert permission["code"] == "MANUAL_SESSION_EXPIRED"
    assert resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=BLOCK, parking_block=None,
        hydrawise_safety=safety(fresh=False),
    )["code"] == "MANUAL_SESSION_WATER_UNKNOWN"


def test_start_session_ends_only_after_observed_departure_then_later_return():
    value = session()
    dock_first, changed = observe_session(value, mower(activity="PARKED_IN_CS"), now_utc=NOW)
    assert not changed and dock_first["status"] == "PREPARED"
    departed_at = NOW + timedelta(minutes=1)
    active, changed = observe_session(dock_first, mower(now=departed_at), now_utc=departed_at)
    assert changed and active["status"] == "ACTIVE"
    returned_at = departed_at + timedelta(minutes=20)
    ended, changed = observe_session(active, mower(now=returned_at, activity="GOING_HOME"), now_utc=returned_at)
    assert changed and ended["status"] == "ENDED"
    assert ended["ended_at_utc"] == returned_at.isoformat()


def test_expired_prepared_session_cannot_be_reanimated_by_a_late_departure():
    value = session()
    late = NOW + timedelta(minutes=10)
    expired, changed = observe_session(value, mower(now=late, activity="MOWING"), now_utc=late)
    assert changed and expired["status"] == "ENDED"
    assert expired["departure_observed_utc"] is None


def test_park_session_never_ends_from_a_station_snapshot():
    parked = new_session(
        session_id="park-1", epoch=2, mower_id="mower-1", kind="PARK", source="APP", now_utc=NOW,
    )
    observed, changed = observe_session(parked, mower(activity="PARKED_IN_CS"), now_utc=NOW)
    assert not changed and observed["status"] == "PREPARED"


def test_start_fence_requires_exact_epoch_target_and_unexpired_preparation():
    value = session()
    assert start_dispatch_permission(
        value, session_id="session-1", epoch=1, mower_id="mower-1", now_utc=NOW,
    )["allowed"] is True
    assert start_dispatch_permission(
        value, session_id="session-1", epoch=2, mower_id="mower-1", now_utc=NOW,
    )["code"] == "MANUAL_SESSION_FENCE_CHANGED"
    assert start_dispatch_permission(
        value, session_id="session-1", epoch=1, mower_id="other", now_utc=NOW,
    )["code"] == "MANUAL_SESSION_TARGET_CHANGED"
    assert start_dispatch_permission(
        value, session_id="session-1", epoch=1, mower_id="mower-1",
        now_utc=NOW + timedelta(minutes=10),
    )["code"] == "MANUAL_SESSION_EXPIRED"


def test_confirmed_dry_override_requires_a_future_exact_confirmation():
    value = session(confirmed_dry_until_utc=NOW + timedelta(minutes=5))
    permission = resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=BLOCK, parking_block=None,
        hydrawise_safety=safety(),
        hydrawise_release={
            "dry_until_utc": (NOW + timedelta(minutes=5)).isoformat(),
            "telemetry_confirmed": True, "persistent_state_available": True,
            "current_water_clear": True,
        },
    )
    assert permission == {
        "allowed": True, "code": "MANUAL_SESSION_ALLOWED", "dry_override": True,
        "session_id": "session-1", "epoch": 1,
    }
    assert resolve_manual_permissions(
        value, mower_id="mower-1", now_utc=NOW, blocked_now=BLOCK, parking_block=None,
        hydrawise_safety=safety(clear_now=False),
        hydrawise_release={
            "dry_until_utc": (NOW + timedelta(minutes=5)).isoformat(),
            "telemetry_confirmed": True, "persistent_state_available": True,
            "current_water_clear": True,
        },
    )["code"] == "MANUAL_SESSION_WATER_ACTIVE"
