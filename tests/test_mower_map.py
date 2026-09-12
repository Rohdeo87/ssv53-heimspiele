import copy
import inspect
from unittest.mock import patch

import pytest
from mower.husqvarna import parse_display_position, parse_snapshot
from mower.dry_run import run_read_only_cycle
from mower.state_store import InMemoryStateStore
from platzwart_console import live_status
from test_platzwart_console import ENV, NOW, result


def item(point=None):
    return {'attributes': {'positions': [point or {'latitude':52.53,'longitude':13.12},
                                         {'latitude':53.0,'longitude':13.0}]}}


def test_only_latest_gps_point_is_used_and_no_history_is_copied():
    value=item()
    assert parse_display_position(value)=={'latitude':52.53,'longitude':13.12}
    assert 'position' not in parse_snapshot(value).to_dict()
    assert 'positions' not in parse_snapshot(value).to_dict()


@pytest.mark.parametrize('point', [None, {}, {'latitude':None,'longitude':13},
    {'latitude':True,'longitude':13}, {'latitude':'52.5','longitude':13},
    {'latitude':float('nan'),'longitude':13}, {'latitude':52,'longitude':float('inf')},
    {'latitude':91,'longitude':13}, {'latitude':52,'longitude':181}, {'latitude':0,'longitude':0}])
def test_bad_latest_point_does_not_fall_back_to_older_position(point):
    value=item();value['attributes']['positions'][0]=point
    assert parse_display_position(value) is None


@pytest.mark.parametrize('positions',[None,{},[],False])
def test_missing_position(positions):
    assert parse_display_position({'attributes':{'positions':positions}}) is None


def test_explicitly_unsupported_position_is_not_used():
    value=item();value['attributes']['capabilities']={'position':False}
    assert parse_display_position(value) is None


def test_authenticated_status_exposes_only_latest_point_without_changing_permissions():
    cycle=result(activity='MOWING',battery=70)
    store=InMemoryStateStore()
    with patch('platzwart_console.run_read_only_cycle',return_value=cycle), \
         patch('platzwart_console.AzureTableStateStore.from_environment',return_value=store), \
         patch('platzwart_console._clubhouse_events',return_value={'available':True,'events':[]}), \
         patch('platzwart_console._dashboard_statistics',return_value={'available':False}):
        before=live_status(ENV,NOW)
        cycle.details['mower']['position']=parse_display_position(item())
        after=live_status(ENV,NOW)
    assert before['mower']['position'] is None
    assert after['mower']['position']=={'latitude':52.53,'longitude':13.12}
    after['mower']['position']=None
    assert after==before


def test_position_is_not_in_regular_control_observations():
    # Guard the integration boundary: no extra fetch and no change to the
    # control snapshot serializer; only the nonpersisting UI source opts in.
    source=inspect.getsource(run_read_only_cycle)
    assert source.count('fetch_mowers(')==1
    assert 'if source in {"platzwart-status", "platzwart-status-display-only"} and not persist_observations:' in source
