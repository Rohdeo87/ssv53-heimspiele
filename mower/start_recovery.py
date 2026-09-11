"""Administrative recovery for one proven legacy unsent mower start.

This module has no mower or irrigation client dependency. It can only remove
an exact durable start latch after an independently queried completed control
trace proves that the corresponding invocation returned before transport.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from mower.state import AutomationState
from mower.state_store import StateConflictError


RECOVERY_CONFIRMATION = "RECOVER_VERIFIED_UNSENT_MOWER_START"
PROTECTIVE_PARK_TRACE_TYPE = "ParkUntilFurtherNotice"

# Each entry is a reviewed historical incident, not a version-wide promise.
# Extending recovery requires adding another independently verified tuple.
VERIFIED_INCIDENTS = frozenset(
    {
        (
            "8b17b7d232de8a32593c0c2aee55b3cd8527d29338c2be9626199ef72fac3d77",
            "2026-09-10T20:00:00.004203+00:00",
            "2026-09-11T01:49:00.004203+00:00",
            "MOWER_STATUS_STALE",
        ),
    }
)


class StartRecoveryUnavailable(RuntimeError):
    """State or independent trace evidence could not be read."""


class StartRecoveryChanged(RuntimeError):
    """The inspected state/proof no longer matches the requested recovery."""


@dataclass(frozen=True)
class StartRecoveryProof:
    trace_id: str
    trace_timestamp_utc: datetime
    manifest_sha256: str
    reason_code: str
    pending_since_utc: str
    pending_deadline_utc: str
    park_source: str
    operator_request_id: str | None
    operator_request_action: str | None


@dataclass(frozen=True)
class StartRecoveryInspection:
    state: AutomationState
    eligible: bool
    reasons: tuple[str, ...]
    pending_fingerprint: str | None
    proof_token: str | None
    proof: StartRecoveryProof | None
    unresolved_device_send_count: int | None
    manual_session_status: str

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dryRun": True,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "stateRevision": self.state.revision,
            "pendingFingerprint": self.pending_fingerprint,
            "proofToken": self.proof_token,
            "journal": {
                "unresolvedDeviceSendCount": self.unresolved_device_send_count,
            },
            "manualSessionStatus": self.manual_session_status,
            "operatorRequestStatus": self.state.operator_request_status,
            "delta": _recovery_delta(self.state, self.proof),
        }
        if self.proof is not None:
            payload["proof"] = {
                "traceId": self.proof.trace_id,
                "traceTimestampUtc": self.proof.trace_timestamp_utc.isoformat(),
                "manifestSha256": self.proof.manifest_sha256,
                "reasonCode": self.proof.reason_code,
            }
        else:
            payload["proof"] = None
        return payload


@dataclass(frozen=True)
class StartRecoveryResult:
    previous: AutomationState
    current: AutomationState
    proof: StartRecoveryProof
    delta: Mapping[str, Any]


def _utc_text(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _utc(value: object) -> datetime | None:
    text = _utc_text(value)
    return datetime.fromisoformat(text) if text is not None else None


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _strict_false(value: object) -> bool:
    return value is False


def _strict_true(value: object) -> bool:
    return value is True


def _pending_fingerprint(state: AutomationState) -> str | None:
    if state.mower_start_pending_since_utc is None:
        return None
    values = {
        "revision": state.revision,
        "pendingSinceUtc": state.mower_start_pending_since_utc,
        "pendingDeadlineUtc": state.mower_start_pending_deadline_utc,
        "pendingSessionId": state.mower_start_pending_session_id,
        "pendingSessionEpoch": state.mower_start_pending_session_epoch,
        "parkedByAutomation": state.parked_by_automation,
        "parkSource": state.automation_park_source,
        "restartAllowed": state.automation_restart_allowed,
        "lastDecisionCode": state.last_decision_code,
        "operatorRequestId": state.operator_request_id,
        "operatorRequestAction": state.operator_request_action,
        "operatorRequestedUtc": state.operator_requested_utc,
        "lastStartCommandUtc": state.last_start_command_utc,
        "manualSessionJson": state.manual_session_json,
        "deviceSendJournalJson": state.device_send_journal_json,
    }
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _proof_token(fingerprint: str, proof: StartRecoveryProof) -> str:
    values = {
        "pendingFingerprint": fingerprint,
        "traceId": proof.trace_id,
        "traceTimestampUtc": proof.trace_timestamp_utc.isoformat(),
        "manifestSha256": proof.manifest_sha256,
        "reasonCode": proof.reason_code,
        "pendingSinceUtc": proof.pending_since_utc,
        "pendingDeadlineUtc": proof.pending_deadline_utc,
    }
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trace_query(pending_since_utc: str) -> str:
    start = datetime.fromisoformat(pending_since_utc)
    end = start + timedelta(hours=24)
    start_text = start.isoformat().replace("+00:00", "Z")
    end_text = end.isoformat().replace("+00:00", "Z")
    return f"""
traces
| where timestamp between (datetime({start_text}) .. datetime({end_text}))
| where message startswith "SSV53_CONTROL_CYCLE "
| extend p=parse_json(replace_string(message, "SSV53_CONTROL_CYCLE ", ""))
| where tostring(p.details.automation_state.mower_start_pending_since_utc) == "{pending_since_utc}"
| project timestamp,
    trace_id=itemId,
    decision_code=tostring(p.decision_code),
    command_sent=tobool(p.command_sent),
    start_action_type=tostring(p.details.start_action.type),
    start_outcome=tostring(p.details.start_action.outcome),
    reason_code=tostring(p.details.start_action.reason_code),
    park_action_type=tostring(p.details.park_action.type),
    pending_since_utc=tostring(p.details.automation_state.mower_start_pending_since_utc),
    pending_deadline_utc=tostring(p.details.automation_state.mower_start_pending_deadline_utc),
    parked_by_automation=tobool(p.details.automation_state.parked_by_automation),
    park_source=tostring(p.details.automation_state.automation_park_source),
    restart_allowed=tobool(p.details.automation_state.automation_restart_allowed),
    maintenance_mode=tobool(p.details.automation_state.maintenance_mode),
    operator_request_id=tostring(p.details.automation_state.operator_request_id),
    operator_request_action=tostring(p.details.automation_state.operator_request_action),
    manual_session_status=tostring(p.details.manual_session.status),
    manifest_sha256=tostring(p.build_provenance.package_manifest_sha256)
| order by timestamp asc
""".strip()


def _proof_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    pending_since_utc: str,
    pending_deadline_utc: str,
) -> StartRecoveryProof | None:
    pending = datetime.fromisoformat(pending_since_utc)
    window_end = pending + timedelta(hours=24)
    matches: list[StartRecoveryProof] = []
    conflicting_dispatch_evidence = False
    for row in rows:
        timestamp = _utc(row.get("timestamp"))
        manifest = _optional_text(row.get("manifest_sha256"))
        reason = _optional_text(row.get("reason_code"))
        row_pending = _utc_text(row.get("pending_since_utc"))
        row_deadline = _utc_text(row.get("pending_deadline_utc"))
        incident = (manifest, row_pending, row_deadline, reason)
        if row_pending == pending_since_utc and row_deadline == pending_deadline_utc:
            start_type = _optional_text(row.get("start_action_type"))
            outcome = _optional_text(row.get("start_outcome"))
            decision = _optional_text(row.get("decision_code")) or ""
            protective_park = (
                row.get("command_sent") is True
                and decision == "MOWER_START_OUTCOME_UNCONFIRMED"
                and start_type is None
                and outcome is None
                and row.get("park_action_type") == PROTECTIVE_PARK_TRACE_TYPE
            )
            if (
                (row.get("command_sent") is True and not protective_park)
                or (outcome is not None and outcome != "PRE_SEND_BLOCKED")
                or (
                    start_type is not None
                    and not (
                        decision == "MOWER_START_SEND_BLOCKED"
                        and outcome == "PRE_SEND_BLOCKED"
                    )
                )
                or decision.endswith("_START_SENT")
                or decision == "CONTINUOUS_MOWING_FAILSAFE_REFRESHED"
            ):
                conflicting_dispatch_evidence = True
        if (
            timestamp is None
            or not pending < timestamp <= window_end
            or incident not in VERIFIED_INCIDENTS
            or row_pending != pending_since_utc
            or row_deadline != pending_deadline_utc
            or row.get("decision_code") != "MOWER_START_SEND_BLOCKED"
            or not _strict_false(row.get("command_sent"))
            or row.get("start_outcome") != "PRE_SEND_BLOCKED"
            or not _strict_true(row.get("parked_by_automation"))
            or not _strict_true(row.get("restart_allowed"))
            or not _strict_false(row.get("maintenance_mode"))
            or row.get("manual_session_status") not in {None, "", "ENDED"}
        ):
            continue
        trace_id = _optional_text(row.get("trace_id"))
        park_source = _optional_text(row.get("park_source"))
        if trace_id is None or park_source is None:
            continue
        matches.append(
            StartRecoveryProof(
                trace_id=trace_id,
                trace_timestamp_utc=timestamp,
                manifest_sha256=str(manifest),
                reason_code=str(reason),
                pending_since_utc=row_pending,
                pending_deadline_utc=row_deadline,
                park_source=park_source,
                operator_request_id=_optional_text(row.get("operator_request_id")),
                operator_request_action=_optional_text(row.get("operator_request_action")),
            )
        )
    return (
        matches[0]
        if len(matches) == 1 and not conflicting_dispatch_evidence
        else None
    )


def _manual_status(state: AutomationState) -> tuple[str, bool]:
    raw = state.manual_session_json
    if raw is None:
        return "ABSENT", True
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16_384:
            raise ValueError
        session = json.loads(raw)
        if (
            not isinstance(session, dict)
            or session.get("version") != 1
            or not isinstance(session.get("session_id"), str)
            or not session["session_id"]
            or type(session.get("epoch")) is not int
            or session["epoch"] < 1
            or session.get("kind") not in {"START", "PARK"}
            or session.get("status") != "ENDED"
            or _utc(session.get("ended_at_utc")) is None
        ):
            return str(session.get("status") or "INVALID"), False
    except (TypeError, ValueError, json.JSONDecodeError):
        return "INVALID", False
    return "ENDED", True


def _unresolved_count(state: AutomationState) -> tuple[int | None, bool]:
    raw = state.device_send_journal_json
    if raw in (None, ""):
        return 0, True
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16_384:
            raise ValueError
        entries = json.loads(raw)
        if not isinstance(entries, list) or len(entries) > 64:
            raise ValueError
        statuses = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError
            status = entry.get("status")
            if status not in {
                "RESERVED", "DISPATCHING", "SENT_UNCONFIRMED", "UNKNOWN",
                "CONFIRMED", "REJECTED",
            }:
                raise ValueError
            statuses.append(status)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, False
    unresolved = sum(
        status in {"RESERVED", "DISPATCHING", "SENT_UNCONFIRMED", "UNKNOWN"}
        for status in statuses
    )
    return unresolved, unresolved == 0


def _eligibility_reasons(
    state: AutomationState,
    proof: StartRecoveryProof | None,
    *,
    manual_ok: bool,
    journal_ok: bool,
) -> list[str]:
    reasons: list[str] = []
    pending = _utc(state.mower_start_pending_since_utc)
    if pending is None or _utc(state.mower_start_pending_deadline_utc) is None:
        reasons.append("START_PENDING_MISSING_OR_INVALID")
    if state.mower_start_pending_session_id is not None or state.mower_start_pending_session_epoch is not None:
        reasons.append("START_PENDING_MANUAL_SESSION_PRESENT")
    if proof is None:
        reasons.append("START_PRE_SEND_PROOF_MISSING")
    elif (
        state.mower_start_pending_since_utc != proof.pending_since_utc
        or state.mower_start_pending_deadline_utc != proof.pending_deadline_utc
    ):
        reasons.append("START_PENDING_CHANGED")
    if not state.parked_by_automation:
        reasons.append("AUTOMATION_PARK_NOT_HELD")
    if state.automation_park_source != "start_outcome_unknown":
        reasons.append("UNKNOWN_START_HOLD_MISSING")
    if state.automation_restart_allowed:
        reasons.append("AUTOMATION_RESTART_ALREADY_ALLOWED")
    if state.maintenance_mode:
        reasons.append("MAINTENANCE_MODE")
    if state.last_decision_code != "MOWER_START_OUTCOME_UNCONFIRMED":
        reasons.append("UNKNOWN_START_DECISION_MISSING")
    if not manual_ok:
        reasons.append("MANUAL_SESSION_ACTIVE_OR_INVALID")
    if not journal_ok:
        reasons.append("DEVICE_SEND_OUTCOME_UNCONFIRMED")
    if proof is not None and (
        state.operator_request_id != proof.operator_request_id
        or state.operator_request_action != proof.operator_request_action
    ):
        reasons.append("OPERATOR_REQUEST_CHANGED")
    requested = _utc(state.operator_requested_utc)
    if pending is not None and requested is not None and requested >= pending:
        reasons.append("OPERATOR_REQUEST_AFTER_PENDING_START")
    last_start = _utc(state.last_start_command_utc)
    if pending is not None and last_start is not None and last_start >= pending:
        reasons.append("START_COMMAND_RECORDED_AFTER_PENDING_START")
    return reasons


def _recovery_delta(
    state: AutomationState,
    proof: StartRecoveryProof | None,
) -> dict[str, Any] | None:
    if proof is None:
        return None
    return {
        "mowerStartPendingSinceUtc": {
            "from": state.mower_start_pending_since_utc,
            "to": None,
        },
        "mowerStartPendingDeadlineUtc": {
            "from": state.mower_start_pending_deadline_utc,
            "to": None,
        },
        "mowerStartPendingSessionId": {
            "from": state.mower_start_pending_session_id,
            "to": None,
        },
        "mowerStartPendingSessionEpoch": {
            "from": state.mower_start_pending_session_epoch,
            "to": None,
        },
        "automationParkSource": {
            "from": state.automation_park_source,
            "to": proof.park_source,
        },
        "automationRestartAllowed": {
            "from": state.automation_restart_allowed,
            "to": True,
        },
    }


def inspect_unsent_start_recovery(*, store: Any, query_client: Any) -> StartRecoveryInspection:
    try:
        state = store.load()
    except Exception as exc:
        raise StartRecoveryUnavailable("START_RECOVERY_STATE_UNAVAILABLE") from exc
    pending_since = _utc_text(state.mower_start_pending_since_utc)
    pending_deadline = _utc_text(state.mower_start_pending_deadline_utc)
    proof = None
    if pending_since is not None and pending_deadline is not None:
        try:
            rows = query_client.execute(_trace_query(pending_since), timespan="P1D")
        except Exception as exc:
            raise StartRecoveryUnavailable("START_RECOVERY_PROOF_UNAVAILABLE") from exc
        proof = _proof_from_rows(
            rows,
            pending_since_utc=pending_since,
            pending_deadline_utc=pending_deadline,
        )
    manual_status, manual_ok = _manual_status(state)
    unresolved_count, journal_ok = _unresolved_count(state)
    reasons = _eligibility_reasons(
        state,
        proof,
        manual_ok=manual_ok,
        journal_ok=journal_ok,
    )
    fingerprint = _pending_fingerprint(state)
    return StartRecoveryInspection(
        state=state,
        eligible=not reasons,
        reasons=tuple(reasons),
        pending_fingerprint=fingerprint,
        proof_token=(
            _proof_token(fingerprint, proof)
            if not reasons and fingerprint is not None and proof is not None
            else None
        ),
        proof=proof,
        unresolved_device_send_count=unresolved_count,
        manual_session_status=manual_status,
    )


def recover_unsent_start(
    *,
    store: Any,
    query_client: Any,
    expected_revision: int,
    pending_fingerprint: str,
    proof_token: str,
    confirmation: str,
) -> StartRecoveryResult:
    if confirmation != RECOVERY_CONFIRMATION:
        raise ValueError("START_RECOVERY_CONFIRMATION_INVALID")
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("START_RECOVERY_REVISION_INVALID")
    for value in (pending_fingerprint, proof_token):
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("START_RECOVERY_TOKEN_INVALID")
        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError("START_RECOVERY_TOKEN_INVALID") from exc

    inspection = inspect_unsent_start_recovery(store=store, query_client=query_client)
    if inspection.state.revision != expected_revision:
        raise StartRecoveryChanged("START_RECOVERY_STATE_CHANGED")
    if not inspection.eligible or inspection.proof is None:
        raise StartRecoveryChanged("START_RECOVERY_NOT_ELIGIBLE")
    if (
        inspection.pending_fingerprint != pending_fingerprint
        or inspection.proof_token != proof_token
    ):
        raise StartRecoveryChanged("START_RECOVERY_PROOF_CHANGED")

    previous = inspection.state
    current = replace(
        previous,
        revision=previous.revision + 1,
        mower_start_pending_since_utc=None,
        mower_start_pending_deadline_utc=None,
        mower_start_pending_session_id=None,
        mower_start_pending_session_epoch=None,
        automation_park_source=inspection.proof.park_source,
        automation_restart_allowed=True,
    )
    try:
        store.save(current, expected_revision=previous.revision)
    except StateConflictError as exc:
        raise StartRecoveryChanged("START_RECOVERY_CAS_CONFLICT") from exc
    except Exception as exc:
        raise StartRecoveryUnavailable("START_RECOVERY_STATE_UNAVAILABLE") from exc
    return StartRecoveryResult(
        previous=previous,
        current=current,
        proof=inspection.proof,
        delta=_recovery_delta(previous, inspection.proof) or {},
    )
