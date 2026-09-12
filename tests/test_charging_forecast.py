from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import replace
import json
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError, ResourceModifiedError
from mower.charging_forecast import advance, predict, quality, own_display, wilson, VERSION, DAY
from mower.charging_forecast_store import record, display, encode, decode, _CACHE
from tests.test_full_failsafe import result

START = datetime(2026,1,1,tzinfo=timezone.utc).timestamp()


def mower(at, battery=9, activity="CHARGING", **changes):
    return {"mower_id":"one", "activity":activity, "state":"IN_OPERATION", "connected":True,
            "error_code":0, "status_timestamp_ms":int(at*1000), "battery_percent":battery,
            "remaining_charging_seconds":600, **changes}


def charge(state, start, *, duration=50, bias=600, fault=None):
    state = advance(state, mower(start-60,activity="GOING_HOME"), start-60)
    samples=[]
    for minute in range(duration+1):
        at=start+minute*60
        data=mower(at,battery=9+(91*minute//duration), activity="LEAVING" if minute==duration else "CHARGING",
                   remaining_charging_seconds=max(1,(duration-minute)*60+bias))
        if fault and minute==10:
            data.update(fault)
        state=advance(state,data,at)
        samples.append(deepcopy(state))
    return state,samples


def outcomes(n=60, *, error=60, vendor=600):
    return [{"end":START+i*DAY, "bucket":0,"valid":True,"ownError":error,
             "vendorError":vendor,"signedError":error} for i in range(n)]


def test_sixty_independent_sessions_and_fourteen_days_required():
    now=START+60*DAY
    assert quality(outcomes(),9,now)["approved"]
    assert not quality(outcomes(59),9,now)["approved"]
    rows=outcomes();rows[-1]["ownError"]=301
    assert not quality(rows,9,now)["approved"]
    assert wilson(60,60)>.95 and wilson(59,60)<.95
    rows=outcomes()
    for r in rows:r["end"]=now-60
    assert not quality(rows,9,now)["approved"]


@pytest.mark.parametrize("failure", ["missing","censored","worse","no_margin","other_bucket","old"])
def test_accuracy_alone_cannot_promote_missing_or_inferior_predictions(failure):
    rows=outcomes();now=START+60*DAY
    if failure=="missing": rows[-1]["ownError"]=None
    elif failure=="censored": rows[-1]["valid"]=False
    elif failure=="worse":
        for r in rows:r["vendorError"]=30
    elif failure=="no_margin":
        for r in rows:r["vendorError"]=80
    elif failure=="other_bucket":
        for r in rows:r["bucket"]=3
    else:now+=8*DAY
    assert not quality(rows,9,now)["approved"]


def test_prospective_collection_promotes_only_after_outcomes_and_keeps_anchor():
    state=None
    for index in range(70):
        state,steps=charge(state,START+index*DAY/2)
        if index<60:
            assert not steps[0]["active"]["ownDisplay"]
    assert len(state["history"])<=10
    assert state["outcomes"][-1]["ownError"]==0
    start=START+71*DAY/2
    state=advance(state,mower(start-60,activity="GOING_HOME"),start-60)
    state=advance(state,mower(start),start)
    assert state["active"]["ownDisplay"]
    before=deepcopy(state)
    assert own_display(state,mower(start),start)["displayOnly"]
    deadline=state["active"]["prediction"]
    # Future training additions and cache restarts cannot rewrite this forecast.
    reconstructed=json.loads(json.dumps(state))
    reconstructed["history"].append({"end":start+99999,"points":[[start,9],[start+99999,100]]})
    later=advance(reconstructed,mower(start+60,battery=11),start+60)
    assert later["active"]["prediction"]==deadline
    assert state==before
    assert own_display(later,mower(deadline,battery=98),deadline) is None


@pytest.mark.parametrize("fault", [{"connected":False},{"state":"ERROR"},{"error_code":93},
                                  {"battery_percent":None},{"battery_percent":3}])
def test_bad_episode_is_counted_but_never_training(fault):
    state,_=charge(None,START,fault=fault)
    assert state["history"]==[]
    assert len(state["outcomes"])==1 and not state["outcomes"][0]["valid"]


def test_cold_start_gap_abort_and_duplicate_have_no_false_completions():
    state=advance(None,mower(START),START)
    assert not state["active"]["valid"]
    state=advance(state,mower(START+60,activity="PARKED_IN_CS",battery=10),START+60)
    assert state["active"] is not None and not state["outcomes"]
    unchanged=advance(state,mower(START),START)
    assert unchanged==state
    state=advance(state,mower(START+120),START+120)
    state=advance(state,mower(START+400,battery=100,activity="LEAVING"),START+400)
    assert not state["outcomes"][-1]["valid"]
    assert not state["history"]


def test_vendor_repeated_snapshot_counted_once_and_real_revision_not_hidden():
    state=advance(None,mower(START-60,activity="GOING_HOME"),START-60)
    state=advance(state,mower(START),START)
    state=advance(state,mower(START),START+60)
    assert len(state["active"]["vendors"])==1
    state=advance(state,mower(START+120,battery=14,remaining_charging_seconds=2712),START+120)
    assert len(state["active"]["vendors"])==2
    assert state["active"]["vendors"][-1][1]==START+120+2712


def test_parked_while_charging_stays_one_episode_and_stop_does_not_split_it():
    for middle in ("PARKED_IN_CS","STOPPED"):
        state=advance(None,mower(START-60,activity="GOING_HOME"),START-60)
        for minute in range(51):
            at=START+minute*60
            activity="LEAVING" if minute==50 else middle if minute in (10,11) else "CHARGING"
            state=advance(state,mower(at,battery=9+91*minute//50,activity=activity),at)
        assert len(state["outcomes"])==1
        assert state["outcomes"][0]["valid"] is (middle=="PARKED_IN_CS")


def test_synthetic_zero_battery_never_creates_a_reference():
    state=advance(None,mower(START,battery=0),START)
    assert state["active"] is None


def test_paired_vendor_error_uses_same_initial_horizon_not_later_revision():
    state,steps=charge(None,START,bias=600)
    outcome=state["outcomes"][-1]
    assert outcome["vendorError"]==600
    assert outcome["vendorTrajectoryError"]==600
    # Missing manufacturer data at the forecast's first observation does not
    # turn a later report into a comparable first-observation baseline.
    state=advance(None,mower(START-60,activity="GOING_HOME"),START-60)
    for minute in range(51):
        at=START+minute*60
        state=advance(state,mower(at,battery=9+91*minute//50,
                      activity="LEAVING" if minute==50 else "CHARGING",
                      remaining_charging_seconds=0 if minute==0 else (50-minute)*60+120),at)
    assert state["outcomes"][-1]["vendorError"] is None
    assert state["outcomes"][-1]["vendorTrajectoryError"]==120


def test_other_mower_and_algorithm_version_cannot_inherit_qualification():
    state,_=charge(None,START)
    changed=advance(state,mower(START+DAY,mower_id="two"),START+DAY)
    assert not changed["history"] and not changed["outcomes"]
    state["version"]=999
    changed=advance(state,mower(START+DAY),START+DAY)
    assert changed["version"]==VERSION and not changed["history"]


def test_first_full_observation_is_used_even_when_departure_is_late():
    state=None
    for minute in range(-1,55):
        at=START+minute*60
        state=advance(state,mower(at,battery=max(9,min(100,9+minute*2)),
             activity="GOING_HOME" if minute<0 else "LEAVING" if minute==54 else "CHARGING"),at)
    assert state["history"][-1]["end"]==START+46*60


class Entity(dict):
    metadata={"etag":"version-1"}


class Table:
    def __init__(self, entity=None, conflict=False):self.entity=entity;self.conflict=conflict;self.writes=[]
    def get_entity(self,*a,**k):
        if self.entity is None:raise ResourceNotFoundError("absent")
        return Entity(self.entity)
    def create_entity(self,entity,**kw):self.entity=entity;self.writes.append(kw)
    def update_entity(self,entity,**kw):
        if self.conflict:raise ResourceModifiedError("changed")
        self.entity=entity;self.writes.append(kw)


def cycle(at,data):
    return replace(result(),executed_at_utc=datetime.fromtimestamp(at,timezone.utc).isoformat(),details={"mower":data})


def test_store_is_separate_bounded_and_optimistic_not_last_writer_wins():
    _CACHE.clear();table=Table()
    first=cycle(START,mower(START))
    summary=record(first,{},client=table)
    assert summary["recorded"] and table.entity["PartitionKey"]=="ssv53-charging-calibration-v1"
    stored=decode(table.entity)
    record(cycle(START+60,mower(START+60,battery=11)),{},client=table)
    assert table.writes[-1]["match_condition"]==MatchConditions.IfNotModified
    table.conflict=True
    with pytest.raises(ResourceModifiedError):record(cycle(START+120,mower(START+120,battery=13)),{},client=table)
    assert decode(table.entity)["lastTick"]==START+60
    assert record(first,{"CHARGING_LEARNER_ENABLED":"false"},client=table)=={"enabled":False}
    assert display(mower(START),datetime.fromtimestamp(START,timezone.utc),{},client=table) is None
    assert decode(encode(stored,"one"))==stored


def test_forecast_store_failure_only_removes_optional_display():
    _CACHE.clear()
    with patch("mower.charging_forecast_store._table_client",side_effect=RuntimeError("storage unavailable")):
        assert display(mower(START),datetime.fromtimestamp(START,timezone.utc),{}) is None


def test_dense_history_and_bounded_outcomes_fit_existing_table_properties():
    state={"version":1,"history":[],"outcomes":outcomes(120),"active":None}
    for r in state["outcomes"]:
        r.update(end=r["end"]+.999,ownError=21599.9,signedError=-21599.9,vendorError=21599.9,
                 vendorTrajectoryError=21599.9,vendorReports=240,prediction=1999999999)
    for day in range(10):
        start=START+day*DAY+.123456
        state["history"].append({"end":start+6000,"points":[[start+i*60,i] for i in range(101)]})
    encoded=encode(state,"one")
    assert all(len(v.encode("utf-16-le"))<=64000 for v in encoded.values())


def test_expired_forecast_on_first_receipt_does_not_count_as_prediction():
    state=advance(None,mower(START-60,activity="GOING_HOME"),START-60)
    with patch("mower.charging_forecast.predict",return_value=START-1):
        state=advance(state,mower(START),START)
    assert state["active"]["prediction"] is None


def test_optional_journal_failure_never_retries_the_controller():
    import function_app
    with patch.object(function_app,"run_control_cycle",return_value=result()) as control, \
         patch.object(function_app,"record_irrigation_observation"), \
         patch.object(function_app,"record_charging_forecast",side_effect=RuntimeError("journal down")), \
         patch.object(function_app,"_build_provenance",return_value={}), \
         patch.object(function_app.LOGGER,"info") as logged:
        function_app.ssv53_mower_timer(SimpleNamespace(past_due=False),SimpleNamespace(invocation_id="one"))
    control.assert_called_once()
    payload=json.loads(logged.call_args.args[1])
    assert payload["charging_calibration"]["recorded"] is False
