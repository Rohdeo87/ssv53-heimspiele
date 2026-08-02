from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mower.state import AutomationState


@dataclass(frozen=True)
class CommandIntent:
    action: str
    target: str
    reason: str
    not_before_utc: datetime | None = None
    valid_until_utc: datetime | None = None

    def __post_init__(self) -> None:
        normalized_action = self.action.strip().upper()
        if normalized_action not in {"PARK", "START"}:
            raise ValueError("action muss PARK oder START sein.")
        if not self.target.strip():
            raise ValueError("target darf nicht leer sein.")
        if not self.reason.strip():
            raise ValueError("reason darf nicht leer sein.")
        if self.not_before_utc is not None:
            _as_utc(self.not_before_utc, "not_before_utc")
        if self.valid_until_utc is not None:
            _as_utc(self.valid_until_utc, "valid_until_utc")
        if (
            self.not_before_utc is not None
            and self.valid_until_utc is not None
            and _as_utc(self.valid_until_utc, "valid_until_utc")
            <= _as_utc(self.not_before_utc, "not_before_utc")
        ):
            raise ValueError("valid_until_utc muss nach not_before_utc liegen.")

    @property
    def normalized_action(self) -> str:
        return self.action.strip().upper()

    @property
    def fingerprint(self) -> str:
        payload = {
            "action": self.normalized_action,
            "target": self.target.strip(),
            "reason": self.reason.strip(),
            "not_before_utc": (
                _as_utc(self.not_before_utc, "not_before_utc").isoformat()
                if self.not_before_utc is not None
                else None
            ),
            "valid_until_utc": (
                _as_utc(self.valid_until_utc, "valid_until_utc").isoformat()
                if self.valid_until_utc is not None
                else None
            ),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CommandGateDecision:
    allowed: bool
    code: str
    reason: str


def evaluate_command_gate(
    *,
    state: AutomationState,
    intent: CommandIntent,
    now_utc: datetime,
    dedupe_minutes: int = 10,
) -> CommandGateDecision:
    now = _as_utc(now_utc, "now_utc")
    if dedupe_minutes < 1 or dedupe_minutes > 1440:
        raise ValueError("dedupe_minutes muss zwischen 1 und 1440 liegen.")

    if state.maintenance_mode:
        return CommandGateDecision(
            False,
            "MAINTENANCE_MODE",
            "Wartungsmodus ist aktiv; Steuerbefehle sind gesperrt.",
        )

    if intent.not_before_utc is not None:
        not_before = _as_utc(intent.not_before_utc, "not_before_utc")
        if now < not_before:
            return CommandGateDecision(
                False,
                "TOO_EARLY",
                "Der Befehl ist noch nicht freigegeben.",
            )

    if intent.valid_until_utc is not None:
        valid_until = _as_utc(intent.valid_until_utc, "valid_until_utc")
        if now >= valid_until:
            return CommandGateDecision(
                False,
                "INTENT_EXPIRED",
                "Die Befehlsabsicht ist abgelaufen.",
            )

    if intent.normalized_action == "START" and not state.parked_by_automation:
        return CommandGateDecision(
            False,
            "START_NOT_OWNED",
            "Automatischer Start ist nur nach eigener Parkierung erlaubt.",
        )

    if (
        state.last_command_fingerprint == intent.fingerprint
        and state.last_command_utc is not None
    ):
        last_command = datetime.fromisoformat(
            state.last_command_utc.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        if now - last_command < timedelta(minutes=dedupe_minutes):
            return CommandGateDecision(
                False,
                "DUPLICATE_COMMAND",
                "Ein identischer Befehl wurde innerhalb des Schutzfensters bereits gesendet.",
            )

    return CommandGateDecision(
        True,
        "COMMAND_ALLOWED",
        "Alle Zustands- und Zeitprüfungen sind erfüllt.",
    )


def is_heartbeat_stale(
    *,
    last_success_utc: str | None,
    now_utc: datetime,
    max_age_minutes: int = 3,
) -> bool:
    now = _as_utc(now_utc, "now_utc")
    if max_age_minutes < 1:
        raise ValueError("max_age_minutes muss mindestens 1 sein.")
    if not last_success_utc:
        return True
    last = datetime.fromisoformat(
        last_success_utc.replace("Z", "+00:00")
    )
    if last.tzinfo is None or last.utcoffset() is None:
        raise ValueError("last_success_utc muss zeitzonenbewusst sein.")
    return now - last.astimezone(timezone.utc) > timedelta(
        minutes=max_age_minutes
    )


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} muss zeitzonenbewusst sein.")
    return value.astimezone(timezone.utc)
