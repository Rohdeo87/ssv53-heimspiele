"""Offline proof tests for cache-aware device confirmation chains.

No test in this module performs a Hydrawise request or sends a device command.
"""
from datetime import datetime, timedelta, timezone

from mower.full_failsafe import (
    _candidate_confirmation,
    _hydrawise_source_observation,
    _record_suspension_revalidation_observation,
    _record_zone_clear_observation,
)
from mower.state import AutomationState


NOW = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)


def _cached_details(observed: datetime) -> dict:
    return {
        "hydrawise": {
            "cache": {
                "source_observed_at_utc": observed.isoformat(),
                "fetched_at_utc": NOW.isoformat(),
                # A different reader may have filled this row.  This flag is
                # deliberately false and must not invalidate the source proof.
                "new_observation": False,
            },
            "safety": {
                "available": True,
                "fresh": True,
                "relay_set_valid": True,
                "observed_at_utc": observed.isoformat(),
            },
        }
    }


def test_cached_source_can_be_shared_but_never_recounted() -> None:
    observed = _hydrawise_source_observation(_cached_details(NOW), now_utc=NOW)
    assert observed == NOW
    first, confirmed = _candidate_confirmation(
        AutomationState(), fingerprint="plan", now_utc=NOW,
        observed_utc=observed, required_minutes=2,
    )
    assert not confirmed

    repeated, confirmed = _candidate_confirmation(
        first, fingerprint="plan", now_utc=NOW + timedelta(minutes=3),
        observed_utc=observed, required_minutes=2,
    )
    assert not confirmed
    assert repeated == first

    advanced, confirmed = _candidate_confirmation(
        repeated, fingerprint="plan", now_utc=NOW + timedelta(minutes=3),
        observed_utc=NOW + timedelta(minutes=2), required_minutes=2,
    )
    assert confirmed
    assert advanced.irrigation_change_candidate_observed_utc == (
        NOW + timedelta(minutes=2)
    ).isoformat()


def test_candidate_migration_and_large_source_gap_restart_the_proof() -> None:
    legacy = AutomationState(
        irrigation_change_candidate_hash="plan",
        irrigation_change_candidate_since_utc=(NOW - timedelta(minutes=2)).isoformat(),
    )
    restarted, confirmed = _candidate_confirmation(
        legacy, fingerprint="plan", now_utc=NOW, observed_utc=NOW,
        required_minutes=2,
    )
    assert not confirmed
    assert restarted.irrigation_change_candidate_since_utc == NOW.isoformat()

    gapped, confirmed = _candidate_confirmation(
        restarted, fingerprint="plan", now_utc=NOW + timedelta(hours=1),
        observed_utc=NOW + timedelta(hours=1), required_minutes=2,
    )
    assert not confirmed
    assert gapped.irrigation_change_candidate_since_utc == (
        NOW + timedelta(hours=1)
    ).isoformat()


def test_revalidation_keeps_the_ninety_second_budget_for_distinct_sources() -> None:
    first, ready = _record_suspension_revalidation_observation(
        AutomationState(), now_utc=NOW, observed_utc=NOW,
        max_gap_seconds=90, required_observations=2,
    )
    assert not ready
    repeated, ready = _record_suspension_revalidation_observation(
        first, now_utc=NOW + timedelta(seconds=89), observed_utc=NOW,
        max_gap_seconds=90, required_observations=2,
    )
    assert not ready and repeated == first

    second, ready = _record_suspension_revalidation_observation(
        repeated, now_utc=NOW + timedelta(seconds=89),
        observed_utc=NOW + timedelta(seconds=89),
        max_gap_seconds=90, required_observations=2,
    )
    assert ready
    assert second.irrigation_suspension_revalidation_observations == 2


def test_revalidation_rejects_a_source_from_before_the_suspend_command() -> None:
    state, ready = _record_suspension_revalidation_observation(
        AutomationState(), now_utc=NOW, observed_utc=NOW,
        not_before_utc=NOW, max_gap_seconds=90, required_observations=2,
    )
    assert not ready
    assert state.irrigation_suspension_revalidation_observations == 0


def test_state_does_not_extend_clear_evidence_from_a_repeated_cache_source() -> None:
    first = AutomationState().record_cycle(
        started_utc=NOW, success=True, decision_code="CLEAR",
        hydrawise_success_utc=NOW, hydrawise_observed_utc=NOW,
        hydrawise_clear=True, hydrawise_active_count=0,
    )
    repeated = first.record_cycle(
        started_utc=NOW + timedelta(seconds=181), success=True, decision_code="CLEAR",
        hydrawise_success_utc=NOW, hydrawise_observed_utc=NOW,
        hydrawise_clear=True, hydrawise_active_count=0,
    )
    assert repeated.hydrawise_clear_since_utc is None
    assert repeated.last_hydrawise_observed_utc == NOW.isoformat()


def test_repeated_source_does_not_restart_physical_drying() -> None:
    drying = NOW - timedelta(minutes=150)
    state = AutomationState(
        last_hydrawise_success_utc=NOW.isoformat(),
        last_hydrawise_observed_utc=NOW.isoformat(),
        last_hydrawise_active_count=0,
        hydrawise_clear_since_utc=drying.isoformat(),
        hydrawise_drying_since_utc=drying.isoformat(),
        hydrawise_clear_origin="IRRIGATION_END",
    )
    repeated = state.record_cycle(
        started_utc=NOW + timedelta(seconds=60), success=True, decision_code="CLEAR",
        hydrawise_success_utc=NOW, hydrawise_observed_utc=NOW,
        hydrawise_clear=True, hydrawise_active_count=0,
    )
    assert repeated.hydrawise_clear_since_utc == drying.isoformat()
    assert repeated.hydrawise_drying_since_utc == drying.isoformat()
    assert repeated.hydrawise_clear_origin == "IRRIGATION_END"


def test_valid_cache_hit_between_independent_polls_preserves_but_does_not_advance_proof():
    first = AutomationState().record_cycle(
        started_utc=NOW, success=True, decision_code="CLEAR", hydrawise_success_utc=NOW,
        hydrawise_observed_utc=NOW, hydrawise_clear=True, hydrawise_active_count=0,
    )
    repeat = first.record_cycle(
        started_utc=NOW + timedelta(minutes=1), success=True, decision_code="CLEAR",
        hydrawise_success_utc=NOW, hydrawise_observed_utc=NOW, hydrawise_clear=True, hydrawise_active_count=0,
    )
    assert repeat.hydrawise_clear_since_utc == first.hydrawise_clear_since_utc
    assert repeat.last_hydrawise_success_utc == first.last_hydrawise_success_utc
    advanced = repeat.record_cycle(
        started_utc=NOW + timedelta(minutes=2), success=True, decision_code="CLEAR",
        hydrawise_success_utc=NOW + timedelta(minutes=2), hydrawise_observed_utc=NOW + timedelta(minutes=2),
        hydrawise_clear=True, hydrawise_active_count=0,
    )
    assert advanced.hydrawise_clear_since_utc == first.hydrawise_clear_since_utc
    assert advanced.hydrawise_drying_since_utc == first.hydrawise_drying_since_utc


def test_zone_end_needs_observation_after_stop_and_restarts_after_a_long_gap():
    first, ready = _record_zone_clear_observation(
        AutomationState(), observed_utc=NOW, not_before_utc=NOW,
    )
    assert not ready and first.irrigation_zone_clear_since_utc is None
    first, ready = _record_zone_clear_observation(
        first, observed_utc=NOW + timedelta(minutes=1), not_before_utc=NOW,
    )
    assert not ready
    late, ready = _record_zone_clear_observation(
        first, observed_utc=NOW + timedelta(minutes=20), not_before_utc=NOW,
    )
    assert not ready
    assert late.irrigation_zone_clear_since_utc == (NOW + timedelta(minutes=20)).isoformat()
