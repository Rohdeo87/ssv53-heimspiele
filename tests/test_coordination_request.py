import copy
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

from mower.coordination_request import build_coordination_request, canonical_schedule_id
from mower.coordination_shadow import compare_charging_window

NOW = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)
def ts(minutes): return (NOW + timedelta(minutes=minutes)).isoformat()

def fixture():
    zones = [
        {"relay_id": 2, "run_seconds": 600, "scheduled_start_utc": ts(90)},
        {"relay_id": 1, "run_seconds": 1200, "scheduled_start_utc": ts(120)},
    ]
    mower = {"mower_id":"m1","activity":"CHARGING","connected":True,"error_code":0,
             "state":"IN_OPERATION","status_timestamp_ms":int(NOW.timestamp()*1000)}
    cycle = {"executed_at_utc":ts(0),"command_sent":False,"details":{"mower":mower,
      "hydrawise":{"zones":copy.deepcopy(zones),"safety":{"available":True,"fresh":True,
      "relay_set_valid":True,"active_zone_count":0,"observed_at_utc":ts(0)}},
      "coordination_shadow_input":{"schema_version":1,"captured_at_utc":ts(0),
      "complete_from_utc":ts(-120),"complete_until_utc":ts(1440),"occupancy_complete":True,
      "state_available":True,"manual_stop":False,"uncertain_start":False,"occupancy":[]}}}
    previous = copy.deepcopy(cycle); previous["executed_at_utc"] = ts(-1)
    previous["details"]["mower"]["status_timestamp_ms"] -= 60000
    need = {"schema_version":1,"need_id":"n1","demand_reference":"approved","mower_id":"m1",
      "required":True,"timing_window_approved":True,"station_and_paths_checked":True,
      "earliest_start_utc":ts(45),"original_start_utc":ts(90),"latest_start_utc":ts(120),
      "valid_until_utc":ts(120),"zones":copy.deepcopy(zones)}
    need["source_plan_id"] = canonical_schedule_id(zones)
    estimate={"estimated":True,"source":"OBSERVED_COMPLETED_CHARGING_SECTIONS","sampleCount":3,
              "daysCovered":2,"at":ts(120)}
    return cycle, previous, need, estimate

class CoordinationRequestTests(unittest.TestCase):
    def setUp(self): self.cycle,self.previous,self.need,self.estimate=fixture()
    def proposal(self):
        return compare_charging_window(cycle=self.cycle,need=self.need,previous_cycle=self.previous,
                                       charging_end_estimate=self.estimate)
    def build(self): return build_coordination_request(proposal=self.proposal(),cycle=self.cycle,
        need=self.need,previous_cycle=self.previous,charging_end_estimate=self.estimate)
    def test_draft_preserves_order_gaps_duration_and_is_command_free(self):
        out=self.build(); self.assertEqual(out["status"],"DRAFT")
        self.assertEqual([z["relay_id"] for z in out["zones"]],[2,1])
        self.assertEqual([z["offset_seconds"] for z in out["zones"]],[0,1800])
        self.assertEqual([z["run_seconds"] for z in out["zones"]],[600,1200])
        self.assertIsNone(out["queued_device_action"]); self.assertFalse(out["permission_to_start"])
    def test_rechecking_inputs_and_restore_are_idempotent(self): self.assertEqual(self.build(),self.build())
    def test_changed_input_hash_blocks(self):
        p=self.proposal(); self.cycle["details"]["hydrawise"]["zones"][0]["run_seconds"]=601
        out=build_coordination_request(proposal=p,cycle=self.cycle,need=self.need,previous_cycle=self.previous,charging_end_estimate=self.estimate)
        self.assertIn("ORIGINAL_SCHEDULE_CHANGED",out["blockers"])
    def test_existing_consumer_lead_is_enforced(self):
        self.need["earliest_start_utc"] = ts(44)
        self.need["latest_start_utc"] = ts(120)
        self.assertIn("EXISTING_CONSUMER_MINIMUM_LEAD", self.build()["blockers"])
        self.need["earliest_start_utc"] = ts(45)
        self.assertEqual(self.build()["status"], "DRAFT")
    def test_stale_bound_plan_id_blocks(self):
        self.need["source_plan_id"] = "stale"
        self.assertIn("SOURCE_PLAN_ID_MISMATCH", self.build()["blockers"])
    def test_expired_approval_blocks(self):
        self.need["valid_until_utc"] = ts(44)
        self.assertTrue(set(self.build()["blockers"]) & {"APPROVAL_EXPIRED", "APPROVAL_WINDOW_INVALID"})
    def test_stop_and_unknown_state_remain_blocked(self):
        self.cycle["details"]["coordination_shadow_input"]["manual_stop"]=True
        out=self.build(); self.assertEqual(out["status"],"BLOCKED"); self.assertFalse(out["execution_available"])
    def test_missing_dock_observation_blocks(self):
        self.previous["details"]["mower"]["activity"]="UNKNOWN"
        out=self.build(); self.assertEqual(out["status"],"BLOCKED")
    def test_none_proposal_is_blocked(self):
        out=build_coordination_request(proposal=None, cycle=self.cycle, need=self.need,
            previous_cycle=self.previous, charging_end_estimate=self.estimate)
        self.assertEqual(out["status"], "BLOCKED")
    def test_malformed_cycle_details_are_blocked(self):
        cycle=copy.deepcopy(self.cycle); cycle["details"]=[]
        out=build_coordination_request(proposal=self.proposal(), cycle=cycle, need=self.need,
            previous_cycle=self.previous, charging_end_estimate=self.estimate)
        self.assertEqual(out["status"], "BLOCKED")

if __name__ == "__main__": unittest.main()
