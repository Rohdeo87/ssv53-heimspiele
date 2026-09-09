from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest

from mower.coordination_inputs import _OBSERVATIONS, prepare_coordination_inputs
from mower.coordination_request import build_coordination_request
from mower.coordination_shadow import compare_charging_window
from tests.test_coordination_request import NOW, fixture, ts


@pytest.fixture(autouse=True)
def fresh_buffer():
    _OBSERVATIONS.clear()
    yield
    _OBSERVATIONS.clear()


def context():
    cycle, previous, need, estimate = fixture()
    document = {"coordination": {"schema_version": 1, "enabled": True, "needs": [need]}}
    environment = {"COORDINATION_EXECUTION_ENABLED": "true", "SSV53_APP_INSIGHTS_APP_ID": "fixture"}
    return cycle, previous, need, estimate, document, environment


def test_disabled_has_no_io_or_observation_side_effect():
    cycle, _, _, _, config, _ = context()
    loader = Mock(side_effect=AssertionError("no query"))
    assert prepare_coordination_inputs(cycle=cycle, config=config, environment={},
                                       source_fresh=True, statistics_loader=loader) is None
    loader.assert_not_called()
    assert not _OBSERVATIONS


def test_ready_input_reuses_statistics_and_never_grants_permission():
    cycle, previous, _, estimate, config, env = context()
    loader = Mock(return_value={"available": True, "_chargingEvidence": {"observed": True}})
    first = prepare_coordination_inputs(cycle=previous, config=config, environment=env,
                                       source_fresh=True, statistics_loader=loader)
    assert first["previous_cycle"] is None
    loader.assert_not_called()
    with patch("mower.coordination_inputs.estimate_charging_end", return_value=estimate) as estimator:
        output = prepare_coordination_inputs(cycle=cycle, config=config, environment=env,
                                            source_fresh=True, statistics_loader=loader)
    assert output["charging_end_estimate"] == estimate
    assert output["previous_cycle"]["details"]["mower"] == previous["details"]["mower"]
    assert output["available"] and not output["permission_to_start"]
    loader.assert_called_once_with(env, NOW)
    estimator.assert_called_once()
    output["needs"][0]["need_id"] = "mutated"
    assert config["coordination"]["needs"][0]["need_id"] == "n1"


@pytest.mark.parametrize("change", ["stale", "disabled", "duplicate_id", "duplicate_plan", "bad_format", "oversize"])
def test_untrusted_or_ambiguous_approval_never_queries_or_admits(change):
    cycle, _, need, _, config, env = context()
    if change == "disabled":
        config["coordination"]["enabled"] = False
    elif change.startswith("duplicate"):
        other = deepcopy(need)
        if change == "duplicate_id":
            other["source_plan_id"] = "a" * 64
        else:
            other["need_id"] = "another"
        config["coordination"]["needs"].append(other)
    elif change == "bad_format":
        config["coordination"]["needs"] = "none"
    elif change == "oversize":
        need["demand_reference"] = "x" * 32768
    loader = Mock(side_effect=AssertionError("no query"))
    output = prepare_coordination_inputs(cycle=cycle, config=config, environment=env,
                                        source_fresh=change != "stale", statistics_loader=loader)
    assert output["need"] is None and output["needs"] == [] and not output["available"]
    loader.assert_not_called()


def test_suspended_source_retains_approval_for_execution_revalidation():
    cycle, _, need, _, config, env = context()
    cycle["details"]["mower"]["activity"] = "PARKED_IN_CS"
    cycle["details"]["hydrawise"]["zones"][0]["scheduled_start_utc"] = ts(1440)
    loader = Mock(side_effect=AssertionError("no query"))
    output = prepare_coordination_inputs(cycle=cycle, config=config, environment=env,
                                        source_fresh=True, statistics_loader=loader)
    assert output["available"] and output["needs"] == [need]
    assert output["need"] is None
    loader.assert_not_called()


def test_repeated_or_backward_source_sample_is_not_a_new_dock_proof():
    cycle, previous, _, _, config, env = context()
    prepare_coordination_inputs(cycle=previous, config=config, environment=env, source_fresh=True)
    cycle["details"]["mower"]["status_timestamp_ms"] = previous["details"]["mower"]["status_timestamp_ms"]
    output = prepare_coordination_inputs(cycle=cycle, config=config, environment=env, source_fresh=True)
    assert output["previous_cycle"] is None
    backwards = deepcopy(previous)
    backwards["executed_at_utc"] = ts(-2)
    output = prepare_coordination_inputs(cycle=backwards, config=config, environment=env, source_fresh=True)
    assert output["previous_cycle"] is None


def test_other_account_and_restart_require_new_dock_samples():
    cycle, previous, _, _, config, env = context()
    prepare_coordination_inputs(cycle=previous, config=config, environment=env, source_fresh=True)
    output = prepare_coordination_inputs(cycle=cycle, config=config,
        environment={**env, "SSV53_APP_INSIGHTS_APP_ID": "different"}, source_fresh=True)
    assert output["previous_cycle"] is None
    _OBSERVATIONS.clear()
    assert prepare_coordination_inputs(cycle=cycle, config=config, environment=env,
                                       source_fresh=True)["previous_cycle"] is None


def test_statistics_failure_does_not_break_the_primary_controller():
    cycle, previous, _, _, config, env = context()
    prepare_coordination_inputs(cycle=previous, config=config, environment=env, source_fresh=True)
    output = prepare_coordination_inputs(cycle=cycle, config=config, environment=env,
        source_fresh=True, statistics_loader=Mock(side_effect=TimeoutError("offline")))
    assert output["available"] and output["charging_end_estimate"] is None
    assert "EMPIRICAL_CHARGING_END_UNKNOWN" in output["blockers"]


def test_consumer_lead_selects_a_useful_later_time_in_the_approved_window():
    cycle, previous, need, estimate, _, _ = context()
    need["earliest_start_utc"] = ts(0)
    kwargs = dict(cycle=cycle, previous_cycle=previous, need=need,
                  charging_end_estimate=estimate, minimum_lead_minutes=45)
    proposal = compare_charging_window(**kwargs)
    draft = build_coordination_request(proposal=proposal, **kwargs)
    assert draft["status"] == "DRAFT" and draft["selected_start_utc"] == ts(45)
    assert proposal["potential_freed_field_minutes"] == 45
    assert [zone["offset_seconds"] for zone in draft["zones"]] == [0, 1800]


@pytest.mark.parametrize("lead", [-1, True, 121, "45"])
def test_invalid_lead_is_fail_closed(lead):
    cycle, previous, need, estimate, _, _ = context()
    result = compare_charging_window(cycle=cycle, previous_cycle=previous, need=need,
                                    charging_end_estimate=estimate, minimum_lead_minutes=lead)
    assert result["status"] == "BLOCKED"


def test_fixed_reservation_does_not_validate_a_different_later_slot():
    cycle, previous, need, estimate, _, _ = context()
    cycle["details"]["coordination_shadow_input"]["occupancy"] = [
        {"start": ts(44), "end": ts(46), "source": "match"}]
    later = compare_charging_window(cycle=cycle, previous_cycle=previous, need=need,
        charging_end_estimate=estimate, fixed_start_utc=ts(46))
    assert later["status"] == "SHADOW_PROPOSAL"
    original = compare_charging_window(cycle=cycle, previous_cycle=previous, need=need,
        charging_end_estimate=estimate, fixed_start_utc=ts(45))
    assert original["status"] == "BLOCKED"
    assert "PROPOSED_WATER_OR_DRYING_OVERLAPS_SPORT" in original["blockers"]


def test_fixed_reservation_cannot_be_backdated():
    cycle, previous, need, estimate, _, _ = context()
    need["earliest_start_utc"] = ts(-5)
    output = compare_charging_window(cycle=cycle, previous_cycle=previous, need=need,
        charging_end_estimate=estimate, fixed_start_utc=ts(-1))
    assert output["status"] == "BLOCKED"
