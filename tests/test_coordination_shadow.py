from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch

from mower.coordination_shadow import capture_planning_inputs, compare_charging_window
from mower.planner import Block, merge_blocks
from mower.state import AutomationState
from scripts.replay_coordination_shadow import read_input, replay
from daily_safety_report import parse_cycle_rows


NOW = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)


def stamp(minutes):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def fixtures():
    mower = {'mower_id': 'fixture-only', 'activity': 'CHARGING', 'connected': True,
             'error_code': 0, 'state': 'IN_OPERATION', 'status_timestamp_ms': int(NOW.timestamp() * 1000)}
    zones = [{'relay_id': 1, 'run_seconds': 1200, 'scheduled_start_utc': stamp(90)},
             {'relay_id': 2, 'run_seconds': 1200, 'scheduled_start_utc': stamp(110)}]
    cycle = {'executed_at_utc': stamp(0), 'command_sent': False, 'details': {
        'mower': mower, 'hydrawise': {'zones': deepcopy(zones), 'safety': {
            'available': True, 'fresh': True, 'relay_set_valid': True, 'active_zone_count': 0, 'observed_at_utc': stamp(0)}},
        'coordination_shadow_input': {'schema_version': 1, 'captured_at_utc': stamp(0),
            'complete_from_utc': stamp(-120), 'complete_until_utc': stamp(1440), 'occupancy_complete': True,
            'state_available': True, 'manual_stop': False, 'uncertain_start': False, 'occupancy': []}}}
    previous = deepcopy(cycle)
    previous['executed_at_utc'] = stamp(-1)
    previous['details']['mower']['status_timestamp_ms'] -= 60000
    need = {'schema_version': 1, 'need_id': 'fixture-need', 'demand_reference': 'synthetic approval',
            'mower_id': 'fixture-only', 'required': True, 'timing_window_approved': True,
            'station_and_paths_checked': True, 'earliest_start_utc': stamp(0), 'original_start_utc': stamp(90),
            'latest_start_utc': stamp(120), 'valid_until_utc': stamp(120), 'zones': zones}
    estimate = {'estimated': True, 'source': 'OBSERVED_COMPLETED_CHARGING_SECTIONS',
                'sampleCount': 3, 'daysCovered': 2, 'at': stamp(120)}
    return cycle, previous, need, estimate


class CoordinationShadowTests(unittest.TestCase):
    def setUp(self):
        self.cycle, self.previous, self.need, self.estimate = fixtures()

    def compare(self):
        return compare_charging_window(cycle=self.cycle, previous_cycle=self.previous,
                                       need=self.need, charging_end_estimate=self.estimate)

    def test_charging_overlap_is_an_estimate_of_free_time_and_never_a_permission(self):
        result = self.compare()
        self.assertEqual(result['status'], 'SHADOW_PROPOSAL')
        self.assertEqual(result['selected_start_utc'], stamp(0))
        self.assertEqual(result['potential_freed_field_minutes'], 90)
        self.assertEqual(result['water_minutes_unchanged'], 40)
        self.assertIsNone(result['productive_mowing_gain_minutes'])
        self.assertFalse(result['permission_to_start'])
        self.assertFalse(result['execution_available'])
        self.assertIn('ORIGINAL_SCHEDULE_SUPPRESSION_CONFIRMED', result['execution_prerequisites'])

    def test_occupancy_overlap_is_unioned_once_in_gain(self):
        # The extra 60 min of the original drying period already overlap sport.
        self.cycle['details']['coordination_shadow_input']['occupancy'] = [
            {'start': stamp(200), 'end': stamp(260)}, {'start': stamp(210), 'end': stamp(250)}]
        self.assertEqual(self.compare()['potential_freed_field_minutes'], 30)

    def test_no_charge_estimate_no_proposal(self):
        self.estimate = None
        self.assertIn('EMPIRICAL_CHARGING_END_UNKNOWN', self.compare()['blockers'])

    def test_every_missing_approval_and_stop_blocks(self):
        for key in ('required', 'timing_window_approved', 'station_and_paths_checked'):
            with self.subTest(key=key):
                original = self.need.pop(key)
                self.assertIsNone(self.compare()['selected_start_utc'])
                self.need[key] = original
        for key in ('manual_stop', 'uncertain_start'):
            self.cycle['details']['coordination_shadow_input'][key] = True
            self.assertIsNone(self.compare()['selected_start_utc'])
            self.cycle['details']['coordination_shadow_input'][key] = False

    def test_source_move_change_of_zone_or_rain_reduction_invalidates_need(self):
        for key, value in (('run_seconds', 900), ('relay_id', 9), ('scheduled_start_utc', stamp(95))):
            with self.subTest(key=key):
                zone = self.cycle['details']['hydrawise']['zones'][0]
                original = zone[key]
                zone[key] = value
                self.assertIn('ORIGINAL_SCHEDULE_CHANGED', self.compare()['blockers'])
                zone[key] = original

    def test_missing_old_log_fields_are_unknown_instead_of_empty_occupancy(self):
        del self.cycle['details']['coordination_shadow_input']
        self.assertEqual(self.compare()['blockers'], ['COMPLETE_INPUT_CAPTURE_MISSING'])

    def test_same_vendor_sample_is_not_two_dock_observations(self):
        self.previous['details']['mower']['status_timestamp_ms'] = self.cycle['details']['mower']['status_timestamp_ms']
        self.assertIn('TWO_FRESH_CHARGING_OBSERVATIONS_REQUIRED', self.compare()['blockers'])

    def test_backward_vendor_sample_is_not_station_confirmation(self):
        self.cycle['details']['mower']['status_timestamp_ms'] -= 120000
        self.assertIn('TWO_FRESH_CHARGING_OBSERVATIONS_REQUIRED', self.compare()['blockers'])

    def test_operator_stop_in_final_controller_result_blocks_same_cycle(self):
        self.cycle['details']['automation_state'] = {'automation_park_source': 'operator', 'automation_restart_allowed': False}
        self.assertIn('MANUAL_STOP', self.compare()['blockers'])

    def test_capture_preserves_operator_stop_and_incomplete_parent_provenance(self):
        child = {'start': stamp(10), 'end': stamp(20), 'source': 'irrigation', 'title': 'water', 'details': {}}
        parent = Block(NOW, NOW + timedelta(minutes=60), 'special', 'Binding hold', {'items': [child]})
        captured = capture_planning_inputs(now_utc=NOW, blocks=[parent],
            runtime_inputs=SimpleNamespace(source_kind='azure_blob', manifest_etag='fixture',
                                          published_at_utc=stamp(-60), fallback_used=False),
            complete_from=NOW, complete_until=NOW + timedelta(days=1), special_available=True,
            state=AutomationState(parked_by_automation=True, automation_park_source='operator',
                                  automation_restart_allowed=False, last_decision_code='OPERATOR_PARK_HOLD'))
        self.assertTrue(captured['manual_stop'])
        self.assertEqual(captured['occupancy'], [{'start': stamp(0), 'end': stamp(60), 'source': 'special'}])

    def test_offline_replay_restart_is_identical_and_cannot_invent_charging_end(self):
        document = {'schema_version': 1, 'cycles': [self.previous, self.cycle]}
        first = replay(document, self.need)
        second = replay(deepcopy(document), self.need)
        self.assertEqual(first, second)
        self.assertIsNone(first['first_proposal'])
        self.assertEqual(first['production_actions'], [])
        self.assertIn('EMPIRICAL_CHARGING_END_UNKNOWN', first['status_counts'])

    def test_replay_rejects_repeated_or_out_of_order_input(self):
        for cycles in ([self.cycle, self.cycle], [self.cycle, self.previous]):
            with self.assertRaises(ValueError):
                replay({'schema_version': 1, 'cycles': cycles}, self.need)

    def test_large_replay_parses_each_cycle_once_and_skips_unusable_charging_history(self):
        cycles = []
        for index in range(2000):
            cycle = deepcopy(self.cycle)
            cycle['executed_at_utc'] = stamp(index)
            cycle['details'].pop('coordination_shadow_input')
            cycles.append(cycle)
        parsed = []
        def recording_parser(rows):
            parsed.extend(rows)
            return parse_cycle_rows(rows)
        with patch('scripts.replay_coordination_shadow.parse_cycle_rows', recording_parser), \
                patch('scripts.replay_coordination_shadow.charging_evidence') as charging:
            result = replay({'schema_version': 1, 'cycles': cycles}, self.need)
        self.assertEqual(len(parsed), 2000)
        charging.assert_not_called()
        self.assertEqual(result['cycle_count'], 2000)
        self.assertIsNone(result['first_proposal'])

    def test_replay_history_deduplicates_minutes_without_reading_a_future_cycle(self):
        cycles = [deepcopy(self.cycle) for _ in range(3)]
        times = [NOW, NOW + timedelta(seconds=30), NOW + timedelta(minutes=1)]
        for cycle, at in zip(cycles, times):
            cycle['executed_at_utc'] = at.isoformat()
        observed = []
        def record_evidence(history, now):
            observed.append([item.timestamp_utc for item in history])
            self.assertTrue(all(item.timestamp_utc <= now for item in history))
            return {}
        blocked = {'blockers': ['EMPIRICAL_CHARGING_END_UNKNOWN'], 'status': 'BLOCKED'}
        with patch('scripts.replay_coordination_shadow.compare_charging_window', side_effect=lambda **kwargs: deepcopy(blocked)), \
                patch('scripts.replay_coordination_shadow.charging_evidence', record_evidence), \
                patch('scripts.replay_coordination_shadow.estimate_charging_end', return_value=None):
            replay({'schema_version': 1, 'cycles': cycles}, self.need)
        self.assertEqual(observed, [[times[0]], [times[1]], [times[1], times[2]]])

    def test_replay_resource_limits_fail_without_a_partial_success(self):
        with patch('scripts.replay_coordination_shadow.monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(ValueError, 'time budget'):
                replay({'schema_version': 1, 'cycles': [self.cycle]}, self.need)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'oversized.json'
            source.write_bytes(b'{"schema_version":1}')
            with patch('scripts.replay_coordination_shadow.MAX_INPUT_BYTES', 10):
                with self.assertRaisesRegex(ValueError, 'size limit'):
                    read_input(source)
        with patch('scripts.replay_coordination_shadow.MAX_EVIDENCE_ROWS', 0):
            with self.assertRaisesRegex(ValueError, 'evidence budget'):
                replay({'schema_version': 1, 'cycles': [self.previous, self.cycle]}, self.need)

    def test_replay_final_hashing_cannot_outlive_budget_and_return_success(self):
        clock = [0]
        original_dumps = json.dumps
        def slow_final_hash(*args, **kwargs):
            encoded = original_dumps(*args, **kwargs)
            clock[0] = 31
            return encoded
        with patch('scripts.replay_coordination_shadow.monotonic', lambda: clock[0]), \
                patch('scripts.replay_coordination_shadow.json.dumps', slow_final_hash):
            with self.assertRaisesRegex(ValueError, 'time budget'):
                replay({'schema_version': 1, 'cycles': [self.previous, self.cycle]}, self.need)

    def test_stale_or_active_water_blocks(self):
        safety = self.cycle['details']['hydrawise']['safety']
        for key, value in (('observed_at_utc', stamp(-4)), ('active_zone_count', 1), ('fresh', False)):
            original = safety[key]
            safety[key] = value
            self.assertIn('WATER_STATE_UNKNOWN_OR_ACTIVE', self.compare()['blockers'])
            safety[key] = original

    def test_partial_horizon_and_new_sport_block_proposal(self):
        captured = self.cycle['details']['coordination_shadow_input']
        captured['complete_until_utc'] = stamp(100)
        self.assertIn('OCCUPANCY_HORIZON_TOO_SHORT', self.compare()['blockers'])
        captured['complete_until_utc'] = stamp(1440)
        captured['occupancy'] = [{'start': stamp(10), 'end': stamp(30)}]
        self.assertIn('PROPOSED_WATER_OR_DRYING_OVERLAPS_SPORT', self.compare()['blockers'])

    def test_invalid_timestamp_and_empty_input_are_blocked(self):
        self.need['earliest_start_utc'] = '2026-09-15T02:00:00'
        self.assertIn('INVALID_OR_INCOMPLETE_INPUT', self.compare()['blockers'])
        result = compare_charging_window(cycle={}, need={}, charging_end_estimate=None)
        self.assertIsNone(result['selected_start_utc'])

    def test_capture_does_not_lose_nested_training_or_unknown_blocks(self):
        blocks = [Block(NOW, NOW + timedelta(minutes=40), 'irrigation', 'water', {}),
                  Block(NOW + timedelta(minutes=20), NOW + timedelta(minutes=30), 'training', 'sport', {}),
                  Block(NOW + timedelta(minutes=50), NOW + timedelta(minutes=60), 'unknown', 'hold', {})]
        captured = capture_planning_inputs(now_utc=NOW, blocks=merge_blocks(blocks),
            runtime_inputs=SimpleNamespace(source_kind='azure_blob', manifest_etag='fixture',
                                          published_at_utc=stamp(-60), fallback_used=False),
            complete_from=NOW, complete_until=NOW + timedelta(days=1), special_available=True,
            state=SimpleNamespace(maintenance_mode=False, mower_start_pending_since_utc=None))
        self.assertEqual([b['source'] for b in captured['occupancy']], ['training', 'unknown'])
        self.assertEqual(captured['occupancy'][0]['start'], stamp(20))
        self.assertTrue(captured['occupancy_complete'])
        self.assertFalse(captured['execution_available'])


if __name__ == '__main__':
    unittest.main()
