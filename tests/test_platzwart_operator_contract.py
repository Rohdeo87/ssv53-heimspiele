from dataclasses import replace
from unittest.mock import patch

import pytest

from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from mower.runtime import RuntimeSettings
from platzwart_console import PlatzwartError, live_status, request_action, _operator_module
from tests.test_full_failsafe import NOW, result

ENV = {
    "CONTROL_MODE": "OPERATOR_ONLY", "ENABLE_LIVE_READS": "true",
    "ENABLE_PARK_COMMANDS": "true", "ENABLE_START_COMMANDS": "false",
    "ENABLE_IRRIGATION_COMMANDS": "false",
    "ENABLE_OPERATOR_CUTTING_HEIGHT_COMMANDS": "true",
    "OPERATOR_CONTROL_CONFIRMATION": "SSV53-OPERATOR-PARK-HEIGHT-V1",
    "HUSQVARNA_MOWER_ID": "test-mower",
}


def snapshot():
    cycle = result(activity="MOWING")
    cycle.details["mower"].update(
        mower_id="test-mower", model="Automower 580 EPOS",
        target_work_area={"id": 1, "name": "Rasenfläche", "enabled": True,
                          "use_global_cutting_height": False, "cutting_height_percent": 13},
    )
    return cycle


def status(env=None, *, cycle=None, store=None, read_error=None):
    store = store or InMemoryStateStore()
    cycle = cycle or snapshot()
    with patch("platzwart_console.AzureTableStateStore.from_environment", return_value=store), \
         patch("platzwart_console.run_read_only_cycle", return_value=cycle, side_effect=read_error), \
         patch("platzwart_console._clubhouse_events", return_value={}), \
         patch("platzwart_console._dashboard_statistics", return_value={}), \
         patch("platzwart_console._dashboard_irrigation_statistics", return_value={}):
        return live_status(env or ENV, NOW)


def test_manual_capabilities_do_not_unlock_legacy_start_or_irrigation():
    payload = status()
    assert payload["mower"]["operationMode"] == "MANUAL"
    assert payload["mower"]["cuttingHeightMm"] == 25
    assert payload["deviceControlsAvailable"] is False
    caps = payload["actionCapabilities"]
    assert caps["PARK_MOWER"]["available"] is True
    assert caps["SET_CUTTING_HEIGHT"]["available"] is True
    assert caps["START_MOWING"]["available"] is False
    assert caps["START_IRRIGATION"]["available"] is False


def test_stale_height_telemetry_does_not_disable_park():
    cycle = snapshot()
    cycle.details["mower"]["status_timestamp_ms"] -= 181_000
    payload = status(cycle=cycle)
    assert payload["mower"]["operationMode"] == "UNKNOWN"
    assert payload["actionCapabilities"]["PARK_MOWER"]["available"] is True
    assert payload["actionCapabilities"]["SET_CUTTING_HEIGHT"]["available"] is False


def test_wrong_mower_identity_never_enables_manual_controls():
    payload = status({**ENV, "HUSQVARNA_MOWER_ID": "different"})
    assert not any(item["available"] for item in payload["actionCapabilities"].values())


def test_plan_api_failure_allows_independent_mower_get_and_park_without_fabricating_occupancy():
    cycle = snapshot()
    with patch("mower.operator_controls.read_operator_mower", return_value=cycle.details["mower"]):
        payload = status(read_error=RuntimeError("plan offline"))
    assert payload["occupancy"]["available"] is False
    assert payload["controlsAvailable"] is False
    assert payload["actionCapabilities"]["PARK_MOWER"]["available"] is True
    assert payload["actionCapabilities"]["START_MOWING"]["available"] is False


def test_state_unavailable_never_enables_manual_actions():
    class Broken:
        def load(self):
            raise RuntimeError("storage offline")
    payload = status(store=Broken())
    assert not any(item["available"] for item in payload["actionCapabilities"].values())


@pytest.mark.parametrize("action,extra", [("PARK_MOWER", {}), ("SET_CUTTING_HEIGHT", {"cutting_height_mm": 26})])
def test_new_requests_are_bound_to_new_journal_and_never_replay_legacy(action, extra):
    store = InMemoryStateStore(AutomationState(operator_request_action="START_MOWING", operator_request_status="PENDING"))
    with patch("platzwart_console.ConsoleTableStore.from_environment"):
        accepted = request_action(action, "fresh-request", action, ENV, NOW,
                                  state_store_factory=lambda _: store, client_contract_version=2, **extra)
    assert accepted["status"] == "QUEUED"
    assert store.load().operator_request_action == "START_MOWING"
    assert accepted["operatorCommand"]["requestId"] == "fresh-request"


@pytest.mark.parametrize("version", [None, 1, True, "2", 2.0])
def test_old_or_malformed_client_cannot_queue_device_action(version):
    store = InMemoryStateStore()
    with pytest.raises(PlatzwartError) as error:
        request_action("PARK_MOWER", "old-client", "PARK_MOWER", ENV, NOW,
                       client_contract_version=version, state_store_factory=lambda _: store)
    assert error.value.code == "APP_UPDATE_REQUIRED"
    assert store.load().revision == 0


@pytest.mark.parametrize("height", [25.9, True, "26", None, 19, 61])
def test_height_is_not_silently_rounded_or_coerced(height):
    with pytest.raises(PlatzwartError) as error:
        request_action("SET_CUTTING_HEIGHT", "bad-height", "SET_CUTTING_HEIGHT", ENV, NOW,
                       cutting_height_mm=height, client_contract_version=2)
    assert error.value.code == "CUTTING_HEIGHT_INVALID"


@pytest.mark.parametrize("action", ["START_MOWING", "START_IRRIGATION", "STOP_IRRIGATION_NOW", "RESET_BLADE_USAGE"])
def test_operator_mode_never_admits_unrelated_device_commands(action):
    store = InMemoryStateStore()
    with pytest.raises(PlatzwartError) as error:
        request_action(action, "not-permitted", action, ENV, NOW,
                       client_contract_version=2, state_store_factory=lambda _: store)
    assert error.value.code == "OPERATOR_CONTROL_LOCKED"
    assert store.load().revision == 0


def test_status_read_does_not_mutate_queued_command():
    from mower.operator_controls import queue_operator_action
    store = InMemoryStateStore()
    queue_operator_action(store, RuntimeSettings.from_mapping(ENV), "SET_CUTTING_HEIGHT", "height", NOW, mower_id="test-mower", cutting_height_mm=26)
    original = store.load()
    payload = status(store=store)
    assert payload["operatorCommands"]["SET_CUTTING_HEIGHT"]["status"] == "QUEUED"
    assert payload["mower"]["cuttingHeightMm"] == 25
    assert store.load() == original


def test_readonly_distribution_omits_executor_without_breaking_console_import():
    with patch("platzwart_console.importlib.import_module", side_effect=ModuleNotFoundError("missing", name="mower.operator_controls")):
        assert _operator_module() is None
    with patch("platzwart_console.importlib.import_module", side_effect=ModuleNotFoundError("dependency missing", name="unexpected_dependency")):
        with pytest.raises(ModuleNotFoundError):
            _operator_module()


def test_missing_target_cannot_queue_request_to_be_replayed_after_configuration():
    store = InMemoryStateStore()
    with pytest.raises(PlatzwartError) as error:
        request_action("PARK_MOWER", "unbound", "PARK_MOWER", {**ENV, "HUSQVARNA_MOWER_ID": ""}, NOW,
                       client_contract_version=2, state_store_factory=lambda _: store)
    assert error.value.code == "OPERATOR_CONTROL_LOCKED"
    assert store.load().revision == 0


@pytest.mark.parametrize("height", [True, 25.9, "26"])
def test_http_does_not_convert_invalid_height_to_an_integer(height):
    import json
    import azure.functions as func
    import function_app
    body = {"action": "SET_CUTTING_HEIGHT", "requestId": "invalid", "confirmation": "SET_CUTTING_HEIGHT", "cuttingHeightMm": height, "clientContractVersion": 2}
    request = func.HttpRequest(method="POST", url="https://example.test/api/platzwart/action", headers={"Authorization": "Bearer synthetic-test-token"}, params={}, body=json.dumps(body).encode())
    with patch.object(function_app, "require_platzwart_session", return_value={"did": "synthetic"}), patch.dict(function_app.os.environ, ENV):
        response = function_app.ssv53_platzwart_action(request)
    assert response.status_code == 400
    assert json.loads(response.get_body())["code"] == "CUTTING_HEIGHT_INVALID"


def test_unauthenticated_http_cannot_reach_manual_queue():
    import azure.functions as func
    import function_app
    request = func.HttpRequest(method="POST", url="https://example.test/api/platzwart/action", headers={}, params={}, body=b"{}")
    with patch.object(function_app, "require_platzwart_session", side_effect=PlatzwartError("SESSION_INVALID", "Anmeldung erforderlich", 401)), patch.object(function_app, "platzwart_request_action") as action:
        response = function_app.ssv53_platzwart_action(request)
    assert response.status_code == 401
    action.assert_not_called()
