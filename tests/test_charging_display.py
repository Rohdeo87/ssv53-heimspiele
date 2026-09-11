from copy import deepcopy
from datetime import timedelta
import pytest
from daily_safety_report import estimate_charging_display_end, estimate_charging_end
from mower.husqvarna import parse_snapshot
from tests.test_charging_estimate import NOW, current, evidence, history, live_mower
from tests.test_husqvarna_readonly import mower_item


def test_manufacturer_seconds_use_report_time_and_do_not_drift_on_refresh():
    mower = live_mower(remaining_charging_seconds=420)
    first = estimate_charging_display_end(None, mower, NOW)
    assert first['at'] == (NOW + timedelta(minutes=7)).isoformat()
    assert first['source'] == 'HUSQVARNA_REMAINING_CHARGING_TIME'
    assert first['displayOnly'] and first['estimated']
    assert estimate_charging_display_end(None, mower, NOW + timedelta(minutes=2)) == first
    assert estimate_charging_display_end(None, mower, NOW + timedelta(minutes=4)) is None


def test_valid_numeric_strings_do_not_break_optional_display_path():
    mower=live_mower(remaining_charging_seconds=420)
    mower['battery_percent']='60'
    mower['status_timestamp_ms']=str(mower['status_timestamp_ms'])+'.0'
    assert estimate_charging_display_end(None,mower,NOW)['at'] == (NOW+timedelta(minutes=7)).isoformat()


@pytest.mark.parametrize('seconds', [None, 0, -1, True, '420', 0.5, float('nan'), float('inf'), 21601])
def test_invalid_or_unsupported_manufacturer_time_is_not_a_clock(seconds):
    assert estimate_charging_display_end(None, live_mower(remaining_charging_seconds=seconds), NOW) is None


@pytest.mark.parametrize('changes', [{'activity':'MOWING'},{'activity':'PARKED_IN_CS'},
    {'connected':False},{'error_code':93},{'state':'ERROR'},{'battery_percent':100}])
def test_manufacturer_time_never_overrides_actual_status(changes):
    assert estimate_charging_display_end(None, live_mower(remaining_charging_seconds=420, **changes), NOW) is None


def test_two_measured_reference_charges_are_display_only():
    model = evidence(history((1,2)) + current())
    before = deepcopy(model)
    assert estimate_charging_end(model, live_mower(), NOW) is None
    estimate = estimate_charging_display_end(model, live_mower(), NOW)
    assert estimate['at'] == (NOW + timedelta(minutes=20)).isoformat()
    assert estimate['sampleCount'] == 2 and estimate['daysCovered'] == 2
    assert estimate['source'] == 'OBSERVED_CHARGING_DISPLAY' and estimate['displayOnly']
    assert model == before


def test_briefly_delayed_past_report_does_not_discard_display_for_rest_of_charge():
    rows = history((1,2)) + current()
    rows[-4]['mower_status_timestamp_ms'] -= 182000
    model = evidence(rows)
    assert model['ongoing'] is None and model['displayOngoing'] is not None
    assert estimate_charging_end(model, live_mower(), NOW) is None
    estimate = estimate_charging_display_end(model, live_mower(), NOW)
    assert estimate['at'] == (NOW + timedelta(minutes=20)).isoformat()
    # No display clock while the current live reading itself is stale.
    assert estimate_charging_display_end(model, live_mower(status_timestamp_ms=int(NOW.timestamp()*1000)-182000), NOW) is None


@pytest.mark.parametrize('fault', ['gap','old','error','battery','offline'])
def test_gap_or_unreliable_charge_does_not_gain_a_display_estimate(fault):
    rows = history((1,2)) + current()
    if fault == 'gap': del rows[-4]
    elif fault == 'old': rows[-4]['mower_status_timestamp_ms'] -= 301000
    elif fault == 'error': rows[-4]['error_code'] = 93
    elif fault == 'battery': rows[-4]['battery_percent'] = None
    else: rows[-4]['mower_connected'] = False
    assert estimate_charging_display_end(evidence(rows), live_mower(), NOW) is None


def test_display_expiry_keeps_original_anchor_after_reconstruction():
    start = NOW - timedelta(minutes=10)
    deadline = NOW + timedelta(minutes=20)
    for extra in (0,2,4):
        later = deadline + timedelta(minutes=extra)
        rows = current(start=start,until=later)
        for index,row in enumerate(rows[1:]): row['battery_percent'] = min(99,40+index)
        model = evidence(history((1,2)) + rows, later)
        assert estimate_charging_display_end(model, live_mower(later, rows[-1]['battery_percent']), later) is None


@pytest.mark.parametrize('value,expected', [(420,420),(0,0),(None,None),('420',None),(True,None),(1.5,None),(-1,None),(21601,None)])
def test_readonly_parser_preserves_optional_seconds_without_coercion(value,expected):
    item=mower_item('mower-1',name='Schaf',model='580 EPOS')
    item['attributes']['battery']['remainingChargingTime']=value
    assert parse_snapshot(item).to_dict()['remaining_charging_seconds'] == expected
