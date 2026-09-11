from __future__ import annotations

import json

import azure.functions as func

from mower.start_recovery import RECOVERY_CONFIRMATION
from mower.state_store import InMemoryStateStore
from tests.test_start_recovery import QueryClient, proof_row, recoverable_state


RECOVERY_ENV = {
    "CONTROL_MODE": "FULL_FAILSAFE",
    "ENABLE_LIVE_READS": "true",
    "ENABLE_PARK_COMMANDS": "true",
    "ENABLE_START_COMMANDS": "true",
    "ENABLE_IRRIGATION_COMMANDS": "true",
    "FULL_MOWER_CONFIRMATION": "SSV53-TRAINING-MATCH-PARK-START",
    "FULL_FAILSAFE_CONFIRMATION": (
        "SSV53-MOWER-HYDRAWISE-7-ZONES-150-MINUTES-ADAPTIVE-V1"
    ),
    "ENABLE_MANUAL_SESSIONS": "true",
}


def request(method="GET", body=None):
    return func.HttpRequest(
        method=method,
        url="https://example.test/api/mower/recover-unsent-start",
        headers={"Content-Type": "application/json"},
        params={},
        body=json.dumps(body).encode("utf-8") if body is not None else b"",
    )


def factories(monkeypatch, function_app, store, client):
    for name, value in RECOVERY_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        function_app.AzureTableStateStore,
        "from_environment",
        lambda _environment: store,
    )
    monkeypatch.setattr(
        function_app.ApplicationInsightsQueryClient,
        "from_environment",
        lambda _environment: client,
    )


def test_get_is_dry_run_and_post_applies_exact_preview(monkeypatch):
    import function_app

    store = InMemoryStateStore(recoverable_state())
    client = QueryClient([proof_row()])
    factories(monkeypatch, function_app, store, client)

    inspected_response = function_app.ssv53_recover_unsent_start(request())
    assert inspected_response.status_code == 200
    inspected = json.loads(inspected_response.get_body())
    assert inspected["dryRun"] is True
    assert inspected["eligible"] is True
    assert store.load().mower_start_pending_since_utc is not None

    applied_response = function_app.ssv53_recover_unsent_start(
        request(
            "POST",
            {
                "confirmation": RECOVERY_CONFIRMATION,
                "expectedStateRevision": inspected["stateRevision"],
                "pendingFingerprint": inspected["pendingFingerprint"],
                "proofToken": inspected["proofToken"],
            },
        )
    )
    assert applied_response.status_code == 200
    applied = json.loads(applied_response.get_body())
    assert applied["applied"] is True
    assert store.load().mower_start_pending_since_utc is None
    assert store.load().automation_park_source == "training"

    replay = function_app.ssv53_recover_unsent_start(
        request(
            "POST",
            {
                "confirmation": RECOVERY_CONFIRMATION,
                "expectedStateRevision": inspected["stateRevision"],
                "pendingFingerprint": inspected["pendingFingerprint"],
                "proofToken": inspected["proofToken"],
            },
        )
    )
    assert replay.status_code == 409
    assert json.loads(replay.get_body())["code"] == "START_RECOVERY_STATE_CHANGED"


def test_invalid_post_is_rejected_before_state_or_trace_clients(monkeypatch):
    import function_app

    calls = []
    monkeypatch.setattr(
        function_app.AzureTableStateStore,
        "from_environment",
        lambda _environment: calls.append("state"),
    )
    monkeypatch.setattr(
        function_app.ApplicationInsightsQueryClient,
        "from_environment",
        lambda _environment: calls.append("traces"),
    )
    response = function_app.ssv53_recover_unsent_start(
        request("POST", {"confirmation": "FORCE_CLEAR"})
    )
    assert response.status_code == 400
    assert calls == []


def test_runtime_gate_blocks_read_only_and_park_only_without_clients(monkeypatch):
    import function_app

    calls = []
    monkeypatch.setenv("CONTROL_MODE", "PARK_ONLY")
    monkeypatch.setenv("ENABLE_LIVE_READS", "true")
    monkeypatch.setattr(
        function_app.AzureTableStateStore,
        "from_environment",
        lambda _environment: calls.append("state"),
    )
    monkeypatch.setattr(
        function_app.ApplicationInsightsQueryClient,
        "from_environment",
        lambda _environment: calls.append("traces"),
    )

    response = function_app.ssv53_recover_unsent_start(request())

    assert response.status_code == 409
    assert json.loads(response.get_body())["code"] == "START_RECOVERY_RUNTIME_LOCKED"
    assert calls == []


def test_missing_trace_is_read_only_ineligible_and_query_failure_is_503(monkeypatch):
    import function_app

    store = InMemoryStateStore(recoverable_state())
    factories(monkeypatch, function_app, store, QueryClient([]))
    missing = function_app.ssv53_recover_unsent_start(request())
    assert missing.status_code == 200
    payload = json.loads(missing.get_body())
    assert payload["eligible"] is False
    assert payload["proofToken"] is None
    assert store.load().mower_start_pending_since_utc is not None

    factories(
        monkeypatch,
        function_app,
        store,
        QueryClient(error=TimeoutError("query unavailable")),
    )
    unavailable = function_app.ssv53_recover_unsent_start(request())
    assert unavailable.status_code == 503
    assert json.loads(unavailable.get_body())["code"] == "START_RECOVERY_UNAVAILABLE"
    assert store.load().mower_start_pending_since_utc is not None
