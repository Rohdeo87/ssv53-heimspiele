from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from mower.manual_session import dump_manual_session, new_session, with_session_status
from mower.start_recovery import (
    RECOVERY_CONFIRMATION,
    StartRecoveryChanged,
    StartRecoveryUnavailable,
    inspect_unsent_start_recovery,
    recover_unsent_start,
)
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, StateConflictError


PENDING = "2026-09-10T20:00:00.004203+00:00"
DEADLINE = "2026-09-11T01:49:00.004203+00:00"
TRACE_AT = "2026-09-10T20:00:03.620109+00:00"
MANIFEST = "8b17b7d232de8a32593c0c2aee55b3cd8527d29338c2be9626199ef72fac3d77"
OPERATOR_ID = "e8a4378f-1f28-4f47-b531-3494c768d91c"


class QueryClient:
    def __init__(self, rows=None, error=None):
        self.rows = list(rows or [])
        self.error = error
        self.calls = []

    def execute(self, query, *, timespan):
        self.calls.append((query, timespan))
        if self.error is not None:
            raise self.error
        return list(self.rows)


def proof_row(**changes):
    row = {
        "timestamp": TRACE_AT,
        "trace_id": "trace-2000-presend-blocked",
        "decision_code": "MOWER_START_SEND_BLOCKED",
        "command_sent": False,
        "start_action_type": "StartInWorkArea",
        "start_outcome": "PRE_SEND_BLOCKED",
        "reason_code": "MOWER_STATUS_STALE",
        "park_action_type": None,
        "pending_since_utc": PENDING,
        "pending_deadline_utc": DEADLINE,
        "parked_by_automation": True,
        "park_source": "training",
        "restart_allowed": True,
        "maintenance_mode": False,
        "operator_request_id": OPERATOR_ID,
        "operator_request_action": "SET_CUTTING_HEIGHT",
        "manual_session_status": "ENDED",
        "manifest_sha256": MANIFEST,
    }
    row.update(changes)
    return row


def recoverable_state(**changes):
    state = AutomationState(
        revision=44000,
        last_decision_code="MOWER_START_OUTCOME_UNCONFIRMED",
        parked_by_automation=True,
        automation_park_source="start_outcome_unknown",
        automation_restart_allowed=False,
        park_command_sent_utc="2026-09-10T20:02:00+00:00",
        park_confirmed_utc="2026-09-10T20:03:00+00:00",
        park_confirmed_observations=900,
        last_start_command_utc="2026-09-09T18:00:00+00:00",
        mower_start_pending_since_utc=PENDING,
        mower_start_pending_deadline_utc=DEADLINE,
        operator_request_id=OPERATOR_ID,
        operator_request_action="SET_CUTTING_HEIGHT",
        operator_requested_utc="2026-09-10T18:00:00+00:00",
        operator_request_status="EXPIRED",
        hydrawise_clear_since_utc="2026-09-10T13:55:00+00:00",
        hydrawise_drying_since_utc="2026-09-10T13:55:00+00:00",
        irrigation_phase="COMPLETE_HOLD",
        irrigation_plan_id="water-plan",
        irrigation_plan_json='{"preserve":true}',
        device_send_journal_json="[]",
    )
    return replace(state, **changes)


def preview(store=None, rows=None):
    return inspect_unsent_start_recovery(
        store=store or InMemoryStateStore(recoverable_state()),
        query_client=QueryClient(rows or [proof_row()]),
    )


def test_get_inspection_is_read_only_bounded_and_exposes_no_operator_identity():
    store = InMemoryStateStore(recoverable_state())
    client = QueryClient([proof_row()])

    inspected = inspect_unsent_start_recovery(store=store, query_client=client)
    payload = inspected.public_payload()

    assert inspected.eligible is True
    assert store.load().revision == 44000
    assert client.calls[0][1] == "P1D"
    assert "2026-09-10T20:00:00.004203Z" in client.calls[0][0]
    assert "2026-09-11T20:00:00.004203Z" in client.calls[0][0]
    serialized = json.dumps(payload)
    assert OPERATOR_ID not in serialized
    assert "SET_CUTTING_HEIGHT" not in serialized
    assert payload["pendingFingerprint"]
    assert payload["proofToken"]
    assert payload["delta"]["automationParkSource"]["to"] == "training"


def test_apply_changes_only_latch_and_original_ownership_fields():
    original = recoverable_state()
    store = InMemoryStateStore(original)
    client = QueryClient([proof_row()])
    inspected = inspect_unsent_start_recovery(store=store, query_client=client)

    result = recover_unsent_start(
        store=store,
        query_client=client,
        expected_revision=inspected.state.revision,
        pending_fingerprint=inspected.pending_fingerprint,
        proof_token=inspected.proof_token,
        confirmation=RECOVERY_CONFIRMATION,
    )

    current = result.current
    changed = {
        key
        for key, value in original.to_dict().items()
        if current.to_dict()[key] != value
    }
    assert changed == {
        "revision",
        "mower_start_pending_since_utc",
        "mower_start_pending_deadline_utc",
        "automation_park_source",
        "automation_restart_allowed",
    }
    assert current.mower_start_pending_since_utc is None
    assert current.mower_start_pending_deadline_utc is None
    assert current.automation_park_source == "training"
    assert current.automation_restart_allowed is True
    assert current.irrigation_plan_json == original.irrigation_plan_json
    assert current.hydrawise_drying_since_utc == original.hydrawise_drying_since_utc
    assert current.operator_request_id == original.operator_request_id
    assert current.device_send_journal_json == original.device_send_journal_json


@pytest.mark.parametrize(
    "row_change",
    [
        {"manifest_sha256": "66e3626f7dd633ccf6b005e7826a19af73afae1c2f1b695b86843682c151b916"},
        {"command_sent": True},
        {"pending_since_utc": "2026-09-10T20:00:01+00:00"},
        {"pending_deadline_utc": "2026-09-11T01:50:00+00:00"},
        {"reason_code": "HYDRAWISE_STATUS_STALE"},
        {"start_outcome": "UNCONFIRMED"},
        {"decision_code": "MOWER_START_OUTCOME_UNCONFIRMED"},
        {"timestamp": PENDING},
    ],
)
def test_wrong_version_pending_or_log_facts_never_prove_recovery(row_change):
    inspected = preview(rows=[proof_row(**row_change)])
    assert inspected.eligible is False
    assert inspected.proof_token is None
    assert "START_PRE_SEND_PROOF_MISSING" in inspected.reasons


def test_later_same_pending_transport_evidence_invalidates_original_proof():
    later = proof_row(
        timestamp="2026-09-10T20:00:04+00:00",
        trace_id="trace-later",
        decision_code="MOWER_START_OUTCOME_UNCONFIRMED",
        command_sent=True,
        start_outcome="UNCONFIRMED",
    )
    inspected = preview(rows=[proof_row(), later])
    assert inspected.eligible is False
    assert "START_PRE_SEND_PROOF_MISSING" in inspected.reasons


def test_later_confirmed_protective_park_does_not_invalidate_start_proof():
    later_park = proof_row(
        timestamp="2026-09-10T20:02:04+00:00",
        trace_id="trace-protective-park",
        decision_code="MOWER_START_OUTCOME_UNCONFIRMED",
        command_sent=True,
        start_action_type=None,
        start_outcome=None,
        reason_code=None,
        park_action_type="ParkUntilFurtherNotice",
        manifest_sha256=(
            "66e3626f7dd633ccf6b005e7826a19af73afae1c2f1b695b86843682c151b916"
        ),
    )
    inspected = preview(rows=[proof_row(), later_park])
    assert inspected.eligible is True


@pytest.mark.parametrize(
    "later",
    [
        proof_row(
            timestamp="2026-09-10T20:02:04+00:00",
            trace_id="trace-ambiguous-start",
            decision_code="MOWER_START_OUTCOME_UNCONFIRMED",
            command_sent=True,
            start_action_type="StartInWorkArea",
            start_outcome="UNCONFIRMED",
            reason_code=None,
        ),
        proof_row(
            timestamp="2026-09-10T20:02:04+00:00",
            trace_id="trace-success-start",
            decision_code="CONTINUOUS_MOWING_START_SENT",
            command_sent=True,
            start_action_type="StartInWorkArea",
            start_outcome=None,
            reason_code=None,
        ),
    ],
)
def test_later_ambiguous_or_successful_start_invalidates_proof(later):
    inspected = preview(rows=[proof_row(), later])
    assert inspected.eligible is False
    assert "START_PRE_SEND_PROOF_MISSING" in inspected.reasons


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"operator_request_action": "PARK_MOWER"}, "OPERATOR_REQUEST_CHANGED"),
        (
            {"operator_requested_utc": "2026-09-10T20:00:00.004203+00:00"},
            "OPERATOR_REQUEST_AFTER_PENDING_START",
        ),
        (
            {"last_start_command_utc": "2026-09-10T20:00:00.004203+00:00"},
            "START_COMMAND_RECORDED_AFTER_PENDING_START",
        ),
        ({"maintenance_mode": True}, "MAINTENANCE_MODE"),
        ({"automation_park_source": "manual"}, "UNKNOWN_START_HOLD_MISSING"),
    ],
)
def test_changed_operator_manual_stop_or_new_start_is_ineligible(change, reason):
    inspected = preview(store=InMemoryStateStore(recoverable_state(**change)))
    assert inspected.eligible is False
    assert reason in inspected.reasons


def test_active_manual_session_and_any_unresolved_device_journal_are_denied():
    created = datetime(2026, 9, 10, 19, 59, tzinfo=timezone.utc)
    active = dump_manual_session(
        new_session(
            session_id="manual-start",
            epoch=1,
            mower_id="mower-1",
            kind="START",
            source="APP",
            now_utc=created,
        )
    )
    manual = preview(
        store=InMemoryStateStore(recoverable_state(manual_session_json=active))
    )
    assert "MANUAL_SESSION_ACTIVE_OR_INVALID" in manual.reasons

    unresolved = json.dumps(
        [
            {
                "version": 1,
                "id": "send-1",
                "kind": "PARK",
                "target": "mower-1",
                "intent_key": "park-1",
                "control_binding": "binding-1",
                "reserved_at_utc": "2026-09-10T20:02:00+00:00",
                "deadline_utc": "2026-09-10T20:03:00+00:00",
                "status": "UNKNOWN",
            }
        ]
    )
    journal = preview(
        store=InMemoryStateStore(
            recoverable_state(device_send_journal_json=unresolved)
        )
    )
    assert "DEVICE_SEND_OUTCOME_UNCONFIRMED" in journal.reasons


def test_ended_manual_session_is_allowed():
    created = datetime(2026, 9, 10, 18, 0, tzinfo=timezone.utc)
    ended = with_session_status(
        new_session(
            session_id="manual-start",
            epoch=1,
            mower_id="mower-1",
            kind="START",
            source="APP",
            now_utc=created,
        ),
        "ENDED",
        now_utc=created + timedelta(minutes=1),
    )
    inspected = preview(
        store=InMemoryStateStore(
            recoverable_state(manual_session_json=dump_manual_session(ended))
        )
    )
    assert inspected.eligible is True
    assert inspected.manual_session_status == "ENDED"


def test_replayed_post_and_changed_token_are_rejected():
    store = InMemoryStateStore(recoverable_state())
    client = QueryClient([proof_row()])
    inspected = inspect_unsent_start_recovery(store=store, query_client=client)
    arguments = {
        "store": store,
        "query_client": client,
        "expected_revision": inspected.state.revision,
        "pending_fingerprint": inspected.pending_fingerprint,
        "proof_token": inspected.proof_token,
        "confirmation": RECOVERY_CONFIRMATION,
    }
    recover_unsent_start(**arguments)
    with pytest.raises(StartRecoveryChanged, match="START_RECOVERY_STATE_CHANGED"):
        recover_unsent_start(**arguments)

    fresh = InMemoryStateStore(recoverable_state())
    inspected = inspect_unsent_start_recovery(store=fresh, query_client=client)
    with pytest.raises(StartRecoveryChanged, match="START_RECOVERY_PROOF_CHANGED"):
        recover_unsent_start(
            **{
                **arguments,
                "store": fresh,
                "pending_fingerprint": "0" * 64,
                "expected_revision": inspected.state.revision,
            }
        )


def test_cas_race_and_query_failure_fail_closed():
    class RacingStore(InMemoryStateStore):
        def save(self, state, *, expected_revision):
            raise StateConflictError("raced")

    store = RacingStore(recoverable_state())
    client = QueryClient([proof_row()])
    inspected = inspect_unsent_start_recovery(store=store, query_client=client)
    with pytest.raises(StartRecoveryChanged, match="START_RECOVERY_CAS_CONFLICT"):
        recover_unsent_start(
            store=store,
            query_client=client,
            expected_revision=inspected.state.revision,
            pending_fingerprint=inspected.pending_fingerprint,
            proof_token=inspected.proof_token,
            confirmation=RECOVERY_CONFIRMATION,
        )
    assert store.load().mower_start_pending_since_utc == PENDING

    with pytest.raises(StartRecoveryUnavailable, match="START_RECOVERY_PROOF_UNAVAILABLE"):
        inspect_unsent_start_recovery(
            store=InMemoryStateStore(recoverable_state()),
            query_client=QueryClient(error=TimeoutError("logs unavailable")),
        )
