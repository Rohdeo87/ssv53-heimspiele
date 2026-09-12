"""Check the actual package import boundary, not only source-list membership."""
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

import platzwart_console as console
from scripts.build_azure_full_failsafe_package import build_package as build_full
from scripts.build_azure_source_package import build_package as build_read_only
from tests.test_onsite_dock_proof import ENV, NOW, bound_state, mower
from mower.state_store import InMemoryStateStore
from mower import onsite_dock_proof


@pytest.mark.parametrize("builder,available", [(build_read_only, False), (build_full, True)])
def test_packaged_console_imports_with_its_actual_capabilities(tmp_path, builder, available):
    artifact = tmp_path / "source.zip"
    builder(Path(__file__).resolve().parents[1], artifact)
    installed = tmp_path / "installed"
    with zipfile.ZipFile(artifact) as archive:
        archive.extractall(installed)
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import platzwart_console; "
        f"assert (platzwart_console._onsite_module() is not None) is {available!r}"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed)],
        cwd=installed, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_missing_component_never_accepts_a_water_request_even_if_flag_is_enabled():
    with patch.object(console, "_onsite_module", return_value=None):
        with pytest.raises(console.PlatzwartError) as error:
            console.request_action(
                environment=ENV, now_utc=NOW, action="START_IRRIGATION", confirmation="START_IRRIGATION",
                request_id="missing-component", client_contract_version=4,
                manual_control={"operation": "CONFIRM_DOCK_FOR_IRRIGATION", "confirmed": True,
                                "contextToken": "a" * 64},
            )
    assert error.value.code == "ONSITE_DOCK_COMPONENT_UNAVAILABLE"


def test_optional_import_does_not_hide_a_broken_installed_dependency():
    failure = ModuleNotFoundError("missing journal dependency", name="mower.device_send_guard")
    with patch.object(console.importlib, "import_module", side_effect=failure):
        with pytest.raises(ModuleNotFoundError):
            console._onsite_module()


def test_new_operator_stop_request_keeps_the_running_water_stop_target():
    state = replace(bound_state(), irrigation_phase="RUNNING", irrigation_current_relay_id=1,
                    operator_request_status="COMPLETED")
    store = InMemoryStateStore(state)
    with patch.object(console.ConsoleTableStore, "from_environment"), patch.object(
        console.AzureTableStateStore, "from_environment", return_value=store,
    ):
        console.request_action(
            "STOP_IRRIGATION_NOW", "new-stop", "STOP_IRRIGATION_NOW", ENV, NOW,
            client_contract_version=2, state_store_factory=lambda _environment: store,
        )
    assert store.load().irrigation_onsite_dock_proof_json == state.irrigation_onsite_dock_proof_json
    revoked = onsite_dock_proof.observe(store.load(), mower(), ENV, NOW)
    assert onsite_dock_proof.invalidated_for_active_plan(revoked)
