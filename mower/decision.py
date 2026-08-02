from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


MOWING_ACTIVITIES = {"MOWING", "LEAVING"}
PARKABLE_ACTIVITIES = {"MOWING", "LEAVING"}
SAFE_PARK_ACTIVITIES = {"PARKED_IN_CS", "CHARGING", "GOING_HOME"}
PARK_OVERRIDE_ACTIONS = {
    "FORCE_PARK",
    "PARK_UNTIL_FURTHER_NOTICE",
    "PARK_UNTIL_NEXT_SCHEDULE",
    "PARK",
}
AUTOMATION_EXTERNAL_REASON = 253053
MANUAL_ACTIVITIES = {"STOPPED_IN_GARDEN", "NOT_APPLICABLE"}
MANUAL_STATES = {"PAUSED", "STOPPED", "OFF", "NOT_APPLICABLE"}
ERROR_STATES = {
    "ERROR",
    "FATAL_ERROR",
    "ERROR_AT_POWER_UP",
    "WAIT_UPDATING",
    "WAIT_POWER_UP",
}
NO_OVERRIDE = {"", "NOT_ACTIVE", "NONE", "NULL"}


@dataclass(frozen=True)
class Decision:
    code: str
    title: str
    reason: str
    hypothetical_command: str | None = None
    hypothetical_duration_minutes: int | None = None


def current_context(
    day_plans: Iterable[Any],
    now: datetime,
) -> tuple[Any | None, Any | None]:
    plans = list(day_plans)
    active_block = next(
        (
            block
            for plan in plans
            for block in plan.blocked
            if block.start <= now < block.end
        ),
        None,
    )
    active_window = next(
        (
            window
            for plan in plans
            for window in plan.mowing_windows
            if window.start <= now < window.end
        ),
        None,
    )
    if active_window is None:
        return active_block, None

    extended_end = active_window.end
    all_windows = sorted(
        (
            window
            for plan in plans
            for window in plan.mowing_windows
        ),
        key=lambda window: (window.start, window.end),
    )
    while True:
        adjacent = next(
            (
                window
                for window in all_windows
                if window.start == extended_end
            ),
            None,
        )
        if adjacent is None:
            break
        extended_end = adjacent.end

    if extended_end != active_window.end:
        active_window = type(active_window)(
            start=active_window.start,
            end=extended_end,
        )
    return active_block, active_window


def next_block_after(day_plans: Iterable[Any], now: datetime) -> Any | None:
    candidates = [
        block
        for plan in day_plans
        for block in plan.blocked
        if block.start > now
    ]
    return min(candidates, key=lambda block: block.start, default=None)


def parking_block_for(
    *,
    active_block: Any | None,
    next_block: Any | None,
    now: datetime,
    lookahead_minutes: int,
) -> Any | None:
    if active_block is not None:
        return active_block
    if next_block is None:
        return None
    minutes_until = (next_block.start - now).total_seconds() / 60
    if 0 <= minutes_until <= lookahead_minutes:
        return next_block
    return None


def classify_decision(
    *,
    now: datetime,
    active_block: Any | None,
    parking_block: Any | None,
    active_window: Any | None,
    activity: str,
    state: str,
    error_code: int,
    override_action: str,
    automation_owned_park: bool,
    battery: int,
    minimum_remaining_minutes: int,
) -> Decision:
    activity = activity.upper()
    state = state.upper()
    override_action = override_action.upper()

    manual_lock = (
        activity in MANUAL_ACTIVITIES
        or state in MANUAL_STATES
    )
    error_or_maintenance = error_code != 0 or state in ERROR_STATES
    override_active = (
        override_action not in NO_OVERRIDE
        and not automation_owned_park
    )

    if error_or_maintenance:
        return Decision(
            code="MANUAL_ATTENTION",
            title="Manuelle Prüfung erforderlich",
            reason=(
                f"Zustand {state}, Aktivität {activity}, "
                f"Fehlercode {error_code}. Automatischer Start wäre verboten."
            ),
        )

    if parking_block is not None:
        block_is_active = active_block is not None
        if block_is_active:
            block_context = (
                f"Der Platz ist wegen „{parking_block.title}“ gesperrt."
            )
        else:
            minutes_until_block = max(
                0,
                int((parking_block.start - now).total_seconds() // 60),
            )
            block_context = (
                f"Die Sperre „{parking_block.title}“ beginnt um "
                f"{parking_block.start.strftime('%H:%M')} Uhr "
                f"(in rund {minutes_until_block} Minuten)."
            )

        if manual_lock:
            return Decision(
                code=(
                    "MANUAL_STOP_DURING_BLOCK"
                    if block_is_active
                    else "MANUAL_LOCK_BEFORE_BLOCK"
                ),
                title="Manuellen Stopp respektieren und Standort prüfen",
                reason=(
                    f"{block_context} Der Mäher meldet einen Zustand, "
                    "der einen manuellen Eingriff erfordert. "
                    "Kein automatischer Neustart."
                ),
            )
        if activity in SAFE_PARK_ACTIVITIES:
            return Decision(
                code=(
                    "AUTOMATION_PARK_ACTIVE"
                    if automation_owned_park
                    else "ALREADY_SAFE"
                ),
                title="Keine Aktion erforderlich",
                reason=(
                    f"{block_context} Der Mäher ist bereits sicher "
                    f"({activity})."
                ),
            )
        if override_action in PARK_OVERRIDE_ACTIONS:
            return Decision(
                code="ALREADY_PARK_REQUESTED",
                title="Parkbefehl ist bereits aktiv",
                reason=(
                    f"{block_context} Der Planner-Override lautet "
                    f"{override_action}."
                ),
            )
        if activity not in PARKABLE_ACTIVITIES:
            return Decision(
                code="PARK_STATE_UNCLEAR",
                title="Status unklar – kein automatischer Befehl",
                reason=(
                    f"{block_context} Die Aktivität {activity} ist "
                    "nicht eindeutig als laufender Mähbetrieb klassifiziert."
                ),
            )
        return Decision(
            code="WOULD_PARK",
            title="Mäher muss sicher geparkt werden",
            reason=f"{block_context} Aktuelle Aktivität: {activity}.",
            hypothetical_command="PARK",
        )

    if manual_lock:
        return Decision(
            code="MANUAL_LOCK",
            title="Manuellen Stopp respektieren",
            reason=(
                f"Der Platz ist frei, aber der Mäher meldet {state}/{activity}. "
                "Ein automatischer Neustart wäre ausdrücklich verboten."
            ),
        )

    if automation_owned_park:
        if active_window is None:
            return Decision(
                code="AUTOMATION_PARK_WAIT",
                title="Eigene Parkierung noch nicht freigeben",
                reason=(
                    "Die Parkierung stammt von der SSV53-Automatik, "
                    "aber aktuell besteht kein ausreichend langes freies Mähfenster."
                ),
            )

        remaining = int((active_window.end - now).total_seconds() // 60)
        if remaining < minimum_remaining_minutes:
            return Decision(
                code="AUTOMATION_PARK_WINDOW_TOO_SHORT",
                title="Eigene Parkierung noch nicht freigeben",
                reason=(
                    f"Bis zum Ende des freien Fensters verbleiben nur "
                    f"{remaining} Minuten."
                ),
            )
        if activity == "GOING_HOME":
            return Decision(
                code="AUTOMATION_PARK_GOING_HOME",
                title="Rückfahrt zunächst abschließen lassen",
                reason=(
                    "Die Parkierung stammt von der SSV53-Automatik, "
                    "aber der Mäher fährt noch zur Station."
                ),
            )
        if activity not in {"PARKED_IN_CS", "CHARGING"}:
            return Decision(
                code="AUTOMATION_PARK_STATE_UNCLEAR",
                title="Eigene Parkierung nicht automatisch freigeben",
                reason=(
                    "Die Automationskennung ist vorhanden, aber die "
                    f"Aktivität lautet {activity}."
                ),
            )
        if battery < 90:
            return Decision(
                code="AUTOMATION_PARK_CHARGING",
                title="Vor automatischer Freigabe weiter laden",
                reason=(
                    f"Der Akku liegt erst bei {battery} %. "
                    "Die eigene Parkierung bleibt zunächst aktiv."
                ),
            )

        safe_duration = max(0, remaining - 5)
        if safe_duration < minimum_remaining_minutes:
            return Decision(
                code="AUTOMATION_PARK_WINDOW_TOO_SHORT",
                title="Eigene Parkierung noch nicht freigeben",
                reason=(
                    "Nach dem technischen Fünf-Minuten-Puffer bleibt "
                    "kein ausreichendes Mähfenster."
                ),
            )
        return Decision(
            code="WOULD_START_AFTER_AUTOMATION_PARK",
            title="Eigene Parkierung könnte automatisch freigegeben werden",
            reason=(
                f"Der Platz ist frei, der Akku liegt bei {battery} %, "
                f"und das freie Fenster umfasst noch rund {remaining} Minuten."
            ),
            hypothetical_command="START_IN_WORK_AREA",
            hypothetical_duration_minutes=safe_duration,
        )

    if override_active:
        return Decision(
            code="EXTERNAL_OVERRIDE",
            title="Vorhandene Übersteuerung nicht automatisch aufheben",
            reason=(
                f"Planner-Override „{override_action}“ ist aktiv. "
                "Er wird als manuelle oder externe Bedienung behandelt."
            ),
        )

    if active_window is None:
        return Decision(
            code="NO_VALID_WINDOW",
            title="Nicht starten",
            reason=(
                "Aktuell liegt kein freies Mähfenster von mindestens "
                "der konfigurierten Mindestdauer vor."
            ),
        )

    remaining = int((active_window.end - now).total_seconds() // 60)
    if remaining < minimum_remaining_minutes:
        return Decision(
            code="WINDOW_TOO_SHORT",
            title="Nicht mehr neu starten",
            reason=(
                f"Bis zum Ende des freien Fensters verbleiben nur "
                f"{remaining} Minuten."
            ),
        )

    if activity in MOWING_ACTIVITIES:
        return Decision(
            code="ALREADY_MOWING",
            title="Keine Aktion erforderlich",
            reason=(
                f"Der Mäher nutzt das freie Fenster bereits ({activity}); "
                f"noch rund {remaining} Minuten verfügbar."
            ),
        )

    if activity == "GOING_HOME":
        return Decision(
            code="LET_RETURN_HOME",
            title="Rückfahrt nicht unterbrechen",
            reason=(
                "Der Mäher fährt bereits zur Station. "
                "Die Automatik würde diesen Vorgang nicht umkehren."
            ),
        )

    if battery < 90:
        return Decision(
            code="LET_CHARGE",
            title="Zunächst weiter laden",
            reason=(
                f"Der Platz ist frei, der Akku liegt aber erst bei {battery} %. "
                "Kein Start während des Ladebedarfs."
            ),
        )

    safe_duration = max(0, remaining - 5)
    if safe_duration < minimum_remaining_minutes:
        return Decision(
            code="WINDOW_TOO_SHORT_AFTER_MARGIN",
            title="Nicht mehr neu starten",
            reason=(
                "Nach dem technischen Fünf-Minuten-Puffer bleibt kein "
                "ausreichendes Mähfenster."
            ),
        )

    return Decision(
        code="WOULD_START_WORK_AREA",
        title="Mäher könnte im Arbeitsbereich gestartet werden",
        reason=(
            f"Der Platz ist frei, der Akku liegt bei {battery} %, "
            f"und bis zum nächsten Sperrfenster verbleiben rund "
            f"{remaining} Minuten."
        ),
        hypothetical_command="START_IN_WORK_AREA",
        hypothetical_duration_minutes=safe_duration,
    )
