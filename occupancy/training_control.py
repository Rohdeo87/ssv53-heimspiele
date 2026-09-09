"""Durable global season switch shared by every training consumer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, TYPE_CHECKING
from zoneinfo import ZoneInfo

from mower.state import AutomationState
if TYPE_CHECKING:
    from mower.state_store import StateStore


BERLIN = ZoneInfo("Europe/Berlin")
StateStoreFactory = Callable[[Mapping[str, str]], "StateStore"]
MAX_CAS_ATTEMPTS = 3
MAX_TRANSITIONS = 512


def _azure_state_store_from_environment(environment: Mapping[str, str]):
    from mower.state_store import AzureTableStateStore

    return AzureTableStateStore.from_environment(environment)


def control_enabled(environment: Mapping[str, str]) -> bool:
    return str(
        environment.get("WINTER_TRAINING_CONTROL_ENABLED", "false")
    ).strip().casefold() in {"1", "true", "yes", "on"}


def control_runtime_active(environment: Mapping[str, str]) -> bool:
    return str(environment.get("SHARED_TRAINING_MODE", "OFF")).strip().upper() == "ACTIVE"


@dataclass(frozen=True)
class TrainingTransition:
    effective_at_utc: str
    before_enabled: bool
    enabled: bool


@dataclass(frozen=True)
class TrainingControlSnapshot:
    available: bool
    winter_enabled: bool | None
    pending_enabled: bool | None
    effective_at_utc: str | None
    state_revision: int | None
    source: str
    reason_code: str | None = None
    training_revision: str | None = None
    next_effective_at_utc: str | None = None
    transitions: tuple[TrainingTransition, ...] = ()
    history_valid_from_utc: str | None = None

    @property
    def season(self) -> str | None:
        if not self.available or self.winter_enabled is None:
            return None
        return "Winter" if self.winter_enabled else "Sommer"

    def season_for_anchor(self, anchor_day: date) -> str | None:
        """Return the plan active at the start of one Berlin-local anchor day."""

        if not self.available or self.winter_enabled is None:
            return None
        if type(anchor_day) is not date:
            raise TypeError("anchor_day muss ein Kalendertag sein.")
        anchor_utc = datetime.combine(
            anchor_day, time.min, tzinfo=BERLIN
        ).astimezone(timezone.utc)
        if (
            self.history_valid_from_utc is None
            or anchor_utc < _parse_utc(self.history_valid_from_utc)
        ):
            return None
        if not self.transitions:
            return self.season
        enabled = self.transitions[0].before_enabled
        for transition in self.transitions:
            effective = _parse_utc(transition.effective_at_utc)
            if effective > anchor_utc:
                break
            enabled = transition.enabled
        return "Winter" if enabled else "Sommer"

    def metadata(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "winterEnabled": self.winter_enabled,
            "pendingEnabled": self.pending_enabled,
            "effectiveAt": self.effective_at_utc,
            "stateRevision": self.state_revision,
            "source": self.source,
            "reasonCode": self.reason_code,
            "season": self.season,
            "trainingRevision": self.training_revision,
            "nextEffectiveAt": self.next_effective_at_utc,
            "anchorTransitions": [
                {
                    "effectiveAt": item.effective_at_utc,
                    "beforeEnabled": item.before_enabled,
                    "enabled": item.enabled,
                }
                for item in self.transitions
            ],
            "historyValidFrom": self.history_valid_from_utc,
        }

    def public_payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "active": self.winter_enabled,
            "pending": self.pending_enabled,
            "effectiveAt": self.effective_at_utc,
            "stateRevision": self.state_revision,
            "trainingRevision": self.training_revision,
            "nextEffectiveAt": self.next_effective_at_utc,
        }


class TrainingControlChanged(RuntimeError):
    pass


def initialize_training_control(
    environment: Mapping[str, str],
    *,
    initial_enabled: bool,
    history_valid_from_day: date,
    approval_reference: str,
    request_id: str,
    expected_state_revision: int,
    now_utc: datetime,
    state_store_factory: StateStoreFactory = _azure_state_store_from_environment,
) -> TrainingControlSnapshot:
    """CAS-seed an explicitly approved manual-plan history boundary.

    This is a release operation, never a startup fallback. The caller must
    prove the plan in force on ``history_valid_from_day`` and should activate
    runtime flags only after that boundary also covers the previous anchor day
    needed by overnight sessions.
    """

    if type(initial_enabled) is not bool or type(history_valid_from_day) is not date:
        raise ValueError("TRAINING_CONTROL_INITIAL_VALUE_INVALID")
    reference = str(approval_reference or "").strip()
    if not reference or len(reference) > 256:
        raise ValueError("TRAINING_CONTROL_APPROVAL_REFERENCE_REQUIRED")
    if not request_id or len(request_id) > 64:
        raise ValueError("request_id ist ungültig.")
    if type(expected_state_revision) is not int or expected_state_revision < 1:
        raise ValueError("expected_state_revision ist ungültig.")
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    today = now_utc.astimezone(BERLIN).date()
    if history_valid_from_day > today:
        raise ValueError("TRAINING_CONTROL_HISTORY_CANNOT_START_IN_FUTURE")
    valid_from = datetime.combine(
        history_valid_from_day, time.min, tzinfo=BERLIN
    ).astimezone(timezone.utc).isoformat()
    store = state_store_factory(environment)
    original = store.load()
    if original.winter_training_history_valid_from_utc is not None:
        if (
            original.winter_training_enabled == initial_enabled
            and original.winter_training_history_valid_from_utc == valid_from
            and original.winter_training_history_approval_reference == reference
            and original.winter_training_request_id == request_id
            and original.winter_training_request_enabled == initial_enabled
        ):
            return snapshot_from_state(original, now_utc=now_utc)
        raise TrainingControlChanged("TRAINING_CONTROL_ALREADY_INITIALIZED")
    if original.revision != expected_state_revision:
        from mower.state_store import StateConflictError

        raise StateConflictError("Zustand wurde parallel verändert.")
    if (
        original.winter_training_pending_enabled is not None
        or original.winter_training_effective_utc is not None
        or original.winter_training_transitions_json is not None
    ):
        raise RuntimeError("TRAINING_CONTROL_UNINITIALIZED_STATE_INVALID")
    updated = replace(
        original,
        revision=original.revision + 1,
        winter_training_enabled=initial_enabled,
        winter_training_history_valid_from_utc=valid_from,
        winter_training_history_approval_reference=reference,
        winter_training_request_id=request_id,
        winter_training_request_enabled=initial_enabled,
        winter_training_requested_utc=now_utc.astimezone(timezone.utc).isoformat(),
        winter_training_control_revision=(
            original.winter_training_control_revision + 1
        ),
    )
    store.save(updated, expected_revision=original.revision)
    return snapshot_from_state(updated, now_utc=now_utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
    return parsed.astimezone(timezone.utc)


def _is_berlin_midnight(value: datetime) -> bool:
    local = value.astimezone(BERLIN)
    return local.timetz().replace(tzinfo=None) == time.min


def _load_transitions(state: AutomationState) -> tuple[TrainingTransition, ...]:
    raw = state.winter_training_transitions_json
    if raw is None:
        return ()
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
    transitions: list[TrainingTransition] = []
    previous_effective: datetime | None = None
    previous_enabled: bool | None = None
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "effectiveAt", "beforeEnabled", "enabled"
        }:
            raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
        before = item["beforeEnabled"]
        enabled = item["enabled"]
        if type(before) is not bool or type(enabled) is not bool or before == enabled:
            raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
        effective = _parse_utc(str(item["effectiveAt"]))
        if not _is_berlin_midnight(effective):
            raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
        if previous_effective is not None and effective <= previous_effective:
            raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
        if previous_enabled is not None and before != previous_enabled:
            raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
        transitions.append(TrainingTransition(effective.isoformat(), before, enabled))
        previous_effective = effective
        previous_enabled = enabled
    if transitions and transitions[-1].enabled != state.winter_training_enabled:
        raise ValueError("TRAINING_CONTROL_TRANSITION_INVALID")
    return tuple(transitions)


def _dump_transitions(transitions: tuple[TrainingTransition, ...]) -> str | None:
    if not transitions:
        return None
    return json.dumps([
        {
            "effectiveAt": item.effective_at_utc,
            "beforeEnabled": item.before_enabled,
            "enabled": item.enabled,
        }
        for item in transitions
    ], sort_keys=True, separators=(",", ":"))


def _training_revision(state: AutomationState) -> str:
    payload = {
        "enabled": state.winter_training_enabled,
        "pending": state.winter_training_pending_enabled,
        "effectiveAt": state.winter_training_effective_utc,
    }
    return sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def next_local_midnight(now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    local_day = now_utc.astimezone(BERLIN).date() + timedelta(days=1)
    return datetime.combine(local_day, time.min, tzinfo=BERLIN).astimezone(
        timezone.utc
    )


def snapshot_from_state(
    state: AutomationState,
    *,
    now_utc: datetime,
) -> TrainingControlSnapshot:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    if state.revision == 0:
        return TrainingControlSnapshot(
            False, None, None, None,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_STATE_MISSING",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    history_valid_from = state.winter_training_history_valid_from_utc
    if (
        history_valid_from is None
        or not str(state.winter_training_history_approval_reference or "").strip()
    ):
        return TrainingControlSnapshot(
            False, None, None, None,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_HISTORY_MISSING",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    try:
        history_valid = _parse_utc(history_valid_from)
    except (ValueError, TypeError):
        return TrainingControlSnapshot(
            False, None, None, None,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_STATE_INVALID",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    if not _is_berlin_midnight(history_valid):
        return TrainingControlSnapshot(
            False, None, None, None,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_STATE_INVALID",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    current_anchor = datetime.combine(
        now_utc.astimezone(BERLIN).date(), time.min, tzinfo=BERLIN
    ).astimezone(timezone.utc)
    if history_valid > current_anchor:
        return TrainingControlSnapshot(
            False, None, None, None,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_HISTORY_NOT_EFFECTIVE",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    pending = state.winter_training_pending_enabled
    effective_text = state.winter_training_effective_utc
    if (pending is None) != (effective_text is None):
        return TrainingControlSnapshot(
            False, None, pending, effective_text,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_STATE_INVALID",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    try:
        transitions = _load_transitions(state)
    except (ValueError, TypeError, json.JSONDecodeError):
        return TrainingControlSnapshot(
            False, None, pending, effective_text,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_STATE_INVALID",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    if transitions and (
        _parse_utc(transitions[0].effective_at_utc) < history_valid
        or _parse_utc(transitions[-1].effective_at_utc)
        > now_utc.astimezone(timezone.utc)
    ):
        return TrainingControlSnapshot(
            False, None, pending, effective_text,
            state.winter_training_control_revision,
            "automation_state", "TRAINING_CONTROL_STATE_INVALID",
            _training_revision(state), next_local_midnight(now_utc).isoformat(),
        )
    active = state.winter_training_enabled
    visible_pending = pending
    visible_effective = effective_text
    if pending is not None and effective_text is not None:
        try:
            effective = _parse_utc(effective_text)
        except (ValueError, TypeError):
            return TrainingControlSnapshot(
                False, None, pending, effective_text,
                state.winter_training_control_revision,
                "automation_state", "TRAINING_CONTROL_STATE_INVALID",
                _training_revision(state), next_local_midnight(now_utc).isoformat(),
            )
        if not _is_berlin_midnight(effective):
            return TrainingControlSnapshot(
                False, None, pending, effective_text,
                state.winter_training_control_revision,
                "automation_state", "TRAINING_CONTROL_STATE_INVALID",
                _training_revision(state), next_local_midnight(now_utc).isoformat(),
            )
        expected_before = transitions[-1].enabled if transitions else active
        if pending == expected_before:
            return TrainingControlSnapshot(
                False, None, pending, effective_text,
                state.winter_training_control_revision,
                "automation_state", "TRAINING_CONTROL_STATE_INVALID",
                _training_revision(state), next_local_midnight(now_utc).isoformat(),
            )
        transitions = transitions + (
            TrainingTransition(effective.isoformat(), expected_before, pending),
        )
        if now_utc.astimezone(timezone.utc) >= effective:
            active = pending
            visible_pending = None
            visible_effective = None
    return TrainingControlSnapshot(
        True,
        active,
        visible_pending,
        visible_effective,
        state.winter_training_control_revision,
        "automation_state",
        None,
        _training_revision(state),
        next_local_midnight(now_utc).isoformat(),
        transitions,
        history_valid.isoformat(),
    )


def resolve_training_control(
    environment: Mapping[str, str],
    *,
    now_utc: datetime,
    state_store_factory: StateStoreFactory = _azure_state_store_from_environment,
) -> TrainingControlSnapshot:
    if not control_enabled(environment):
        return TrainingControlSnapshot(
            False, None, None, None, None, "disabled",
            "TRAINING_CONTROL_DISABLED", None,
            next_local_midnight(now_utc).isoformat(),
        )
    if not control_runtime_active(environment):
        return TrainingControlSnapshot(
            False, None, None, None, None, "runtime_mode",
            "TRAINING_CONTROL_REQUIRES_ACTIVE_RUNTIME", None,
            next_local_midnight(now_utc).isoformat(),
        )
    try:
        state = state_store_factory(environment).load()
        return snapshot_from_state(state, now_utc=now_utc)
    except Exception as exc:
        return TrainingControlSnapshot(
            False, None, None, None, None, "automation_state",
            f"TRAINING_CONTROL_UNAVAILABLE:{type(exc).__name__}",
            None, next_local_midnight(now_utc).isoformat(),
        )


def schedule_winter_training(
    environment: Mapping[str, str],
    *,
    enabled: bool,
    request_id: str,
    expected_training_revision: str,
    now_utc: datetime,
    state_store_factory: StateStoreFactory = _azure_state_store_from_environment,
) -> TrainingControlSnapshot:
    if type(enabled) is not bool:
        raise ValueError("winterTrainingEnabled muss boolesch sein.")
    if not request_id or len(request_id) > 64:
        raise ValueError("request_id ist ungültig.")
    if (
        not isinstance(expected_training_revision, str)
        or len(expected_training_revision) != 64
        or any(character not in "0123456789abcdef" for character in expected_training_revision)
    ):
        raise ValueError("trainingRevision ist ungültig.")
    if not control_enabled(environment):
        raise RuntimeError("WINTER_TRAINING_CONTROL_LOCKED")
    if not control_runtime_active(environment):
        raise RuntimeError("TRAINING_CONTROL_REQUIRES_ACTIVE_RUNTIME")
    store = state_store_factory(environment)
    last_conflict: Exception | None = None
    for _attempt in range(MAX_CAS_ATTEMPTS):
        original = store.load()
        current = snapshot_from_state(original, now_utc=now_utc)
        if not current.available or current.winter_enabled is None:
            raise RuntimeError("TRAINING_CONTROL_STATE_INVALID")
        if original.winter_training_request_id == request_id:
            if original.winter_training_request_enabled == enabled:
                return current
            raise TrainingControlChanged("TRAINING_CONTROL_REQUEST_ID_REUSED")
        if expected_training_revision != _training_revision(original):
            raise TrainingControlChanged("TRAINING_CONTROL_CHANGED")

        # A matured pending value becomes the new base in this same CAS. No
        # background finalizer and no second source are required.
        base = current.winter_enabled
        stored_transitions = _load_transitions(original)
        if (
            original.winter_training_pending_enabled is not None
            and original.winter_training_effective_utc is not None
            and _parse_utc(original.winter_training_effective_utc)
            <= now_utc.astimezone(timezone.utc)
        ):
            before = (
                stored_transitions[-1].enabled
                if stored_transitions
                else original.winter_training_enabled
            )
            stored_transitions = stored_transitions + (
                TrainingTransition(
                    _parse_utc(original.winter_training_effective_utc).isoformat(),
                    before,
                    original.winter_training_pending_enabled,
                ),
            )
        if len(stored_transitions) >= MAX_TRANSITIONS:
            raise RuntimeError("TRAINING_CONTROL_HISTORY_CAPACITY_REACHED")
        if enabled == base:
            pending = None
            effective = None
        else:
            pending = enabled
            effective = next_local_midnight(now_utc).isoformat()
        updated = replace(
            original,
            revision=original.revision + 1,
            winter_training_enabled=base,
            winter_training_pending_enabled=pending,
            winter_training_effective_utc=effective,
            winter_training_transitions_json=_dump_transitions(stored_transitions),
            winter_training_request_id=request_id,
            winter_training_request_enabled=enabled,
            winter_training_requested_utc=now_utc.astimezone(timezone.utc).isoformat(),
            winter_training_control_revision=(
                original.winter_training_control_revision + 1
            ),
        )
        try:
            store.save(updated, expected_revision=original.revision)
        except Exception as exc:
            from mower.state_store import StateConflictError

            if not isinstance(exc, StateConflictError):
                raise
            # Retry only if a fresh load proves that the three control fields
            # still match the operator's token. The replacement write is then
            # based on the latest complete state and preserves unrelated data.
            last_conflict = exc
            continue
        return snapshot_from_state(updated, now_utc=now_utc)
    if last_conflict is not None:
        raise last_conflict
    from mower.state_store import StateConflictError

    raise StateConflictError("Zustand wurde parallel verändert.")


__all__ = [
    "TrainingControlSnapshot",
    "TrainingTransition",
    "TrainingControlChanged",
    "control_enabled",
    "control_runtime_active",
    "initialize_training_control",
    "next_local_midnight",
    "resolve_training_control",
    "schedule_winter_training",
    "snapshot_from_state",
]
