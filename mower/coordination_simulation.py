"""Offline-only coordination model; no device adapters, network calls or runtime hooks.

The SQLite journal is a simulation of durable intent, not a production command
dispatcher. All timestamps become UTC instants. Modelled energy and wetness are
explicit assumptions and cannot establish a safe physical release.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


UTC = timezone.utc
MINUTE = timedelta(minutes=1)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timestamp must include an explicit timezone.")
    return value.astimezone(UTC)


def local_instant(value: str, timezone_name: str = "Europe/Berlin", *, fold: int | None = None) -> datetime:
    """Reject nonexistent local time and require fold for ambiguous local time."""
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        return utc(naive)
    zone = ZoneInfo(timezone_name)
    choices = {
        utc(naive.replace(tzinfo=zone, fold=index))
        for index in (0, 1)
        if utc(naive.replace(tzinfo=zone, fold=index)).astimezone(zone).replace(tzinfo=None) == naive
    }
    if not choices:
        raise ValueError("This local time does not exist because of the DST change.")
    if len(choices) > 1 and fold not in (0, 1):
        raise ValueError("This local time is ambiguous; supply fold=0 or fold=1.")
    return utc(naive.replace(tzinfo=zone, fold=fold or 0))


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.end <= self.start or not self.reason:
            raise ValueError("A labelled interval must have a positive duration.")

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < utc(end) and self.end > utc(start)


def union_minutes(intervals: Iterable[Interval]) -> float:
    """Measure elapsed real time once, even across overlap and DST transitions."""
    ordered = sorted(intervals, key=lambda item: item.start)
    if not ordered:
        return 0.0
    start, end = ordered[0].start, ordered[0].end
    seconds = 0.0
    for item in ordered[1:]:
        if item.start <= end:
            end = max(end, item.end)
        else:
            seconds += (end - start).total_seconds()
            start, end = item.start, item.end
    return (seconds + (end - start).total_seconds()) / 60


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    execution_enabled: bool = False
    drying_minutes: int = 150
    return_minutes: int = 4
    dock_confirmation_minutes: int = 1
    telemetry_max_age_seconds: int = 180
    minimum_productive_window_minutes: int = 30
    minimum_gain_minutes: int = 10
    maximum_advance_minutes: int = 120
    candidate_step_minutes: int = 5

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.execution_enabled) is not bool:
            raise ValueError("Planning flags must be explicit booleans.")
        if self.execution_enabled:
            raise ValueError("This module cannot enable device execution.")
        for value in (self.drying_minutes, self.return_minutes, self.dock_confirmation_minutes,
                      self.telemetry_max_age_seconds, self.minimum_productive_window_minutes,
                      self.minimum_gain_minutes, self.maximum_advance_minutes, self.candidate_step_minutes):
            if type(value) is not int or value <= 0:
                raise ValueError("Timing settings must be positive integers.")
        if self.drying_minutes < 150 or self.return_minutes < 4:
            raise ValueError("The offline model must preserve conservative timing limits.")


@dataclass(frozen=True)
class ZoneNeed:
    zone_id: str
    minutes: int

    def __post_init__(self) -> None:
        if not self.zone_id or type(self.minutes) is not int or not 1 <= self.minutes <= 120:
            raise ValueError("Each zone needs an identity and a bounded duration.")


@dataclass(frozen=True)
class WaterNeed:
    need_id: str
    demand_reference: str
    earliest_start: datetime
    original_start: datetime
    latest_start: datetime
    zones: tuple[ZoneNeed, ...]
    required: bool = False
    timing_window_approved: bool = False

    def __post_init__(self) -> None:
        for name in ("earliest_start", "original_start", "latest_start"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if not self.need_id or not self.demand_reference:
            raise ValueError("A stable upstream occurrence identity and demand reference are required.")
        if type(self.required) is not bool or type(self.timing_window_approved) is not bool:
            raise ValueError("Water demand and timing approval must be explicit booleans.")
        if not self.earliest_start <= self.original_start <= self.latest_start:
            raise ValueError("The baseline must lie within the approved timing window.")
        if not self.zones or len({zone.zone_id for zone in self.zones}) != len(self.zones):
            raise ValueError("A complete, unique set of required zones is required.")

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=sum(zone.minutes for zone in self.zones))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["zones"] = [asdict(zone) for zone in self.zones]
        for name in ("earliest_start", "original_start", "latest_start"):
            value[name] = getattr(self, name).isoformat()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WaterNeed":
        payload = dict(value)
        for name in ("earliest_start", "original_start", "latest_start"):
            payload[name] = datetime.fromisoformat(payload[name])
        payload["zones"] = tuple(ZoneNeed(**item) for item in payload["zones"])
        return cls(**payload)


@dataclass(frozen=True)
class SafetyEvidence:
    mower_observed_at: datetime
    irrigation_observed_at: datetime
    occupancy_observed_at: datetime
    dock_observations: tuple[datetime, ...]
    occupancy_complete_from: datetime
    occupancy_complete_until: datetime
    activity: str = "UNKNOWN"
    connected: bool = False
    own_park_confirmed: bool = False
    station_and_paths_safe: bool = False
    need_still_current: bool = False
    manual_stop: bool = False
    unexpected_active_zone: bool = False
    mower_held_until: datetime | None = None
    original_suppressed_until: datetime | None = None


def safety_blockers(need: WaterNeed, start: datetime, now: datetime, evidence: SafetyEvidence,
                    occupancy: Iterable[Interval], settings: Settings, *, before_start: bool) -> tuple[str, ...]:
    """A proposal is never permission; start validation adds confirmed controls."""
    start, now = utc(start), utc(now)
    end = start + need.duration
    release = end + timedelta(minutes=settings.drying_minutes)
    blockers: list[str] = []
    if not settings.enabled:
        blockers.append("DISABLED")
    if not need.required or not need.timing_window_approved or not evidence.need_still_current:
        blockers.append("NO_CONFIRMED_APPROVED_WATER_NEED")
    if start < now or not need.earliest_start <= start <= need.latest_start:
        blockers.append("OUTSIDE_APPROVED_WINDOW")
    if need.original_start - start > timedelta(minutes=settings.maximum_advance_minutes):
        blockers.append("ADVANCE_LIMIT_EXCEEDED")
    for name in ("mower", "irrigation", "occupancy"):
        age = (now - utc(getattr(evidence, f"{name}_observed_at"))).total_seconds()
        if not 0 <= age <= settings.telemetry_max_age_seconds:
            blockers.append(f"{name.upper()}_DATA_NOT_FRESH")
    observations = sorted({utc(value) for value in evidence.dock_observations})
    dock_valid = (
        len(observations) >= 2 and observations[-1] <= now
        and observations[-1] - observations[0] >= timedelta(minutes=settings.dock_confirmation_minutes)
        and now - observations[-1] <= timedelta(seconds=settings.telemetry_max_age_seconds)
        and all(second - first <= timedelta(seconds=settings.telemetry_max_age_seconds)
                for first, second in zip(observations, observations[1:]))
    )
    if not (evidence.connected and evidence.own_park_confirmed and dock_valid
            and evidence.activity in {"CHARGING", "PARKED_IN_CS"}):
        blockers.append("STATION_NOT_CONFIRMED")
    if not evidence.station_and_paths_safe:
        blockers.append("STATION_OR_PATH_ZONE_SAFETY_UNKNOWN")
    if evidence.manual_stop:
        blockers.append("MANUAL_STOP")
    if evidence.unexpected_active_zone:
        blockers.append("UNEXPECTED_WATER_ACTIVE")
    if utc(evidence.occupancy_complete_from) > start or utc(evidence.occupancy_complete_until) < release:
        blockers.append("OCCUPANCY_COVERAGE_INCOMPLETE")
    if any(block.overlaps(start, release) for block in occupancy):
        blockers.append("OCCUPANCY_CONFLICT")
    if before_start:
        if evidence.mower_held_until is None or utc(evidence.mower_held_until) < release:
            blockers.append("AUTONOMOUS_MOWER_RESTART_NOT_INHIBITED")
        suppression_until = max(need.original_start + need.duration, end) + timedelta(minutes=settings.drying_minutes)
        if evidence.original_suppressed_until is None or utc(evidence.original_suppressed_until) < suppression_until:
            blockers.append("ORIGINAL_DEVICE_SCHEDULE_NOT_CONFIRMED_SUPPRESSED")
    return tuple(dict.fromkeys(blockers))


@dataclass(frozen=True)
class Scenario:
    start: datetime
    end: datetime
    need: WaterNeed
    occupancy: tuple[Interval, ...]
    # These are declared model assumptions, not device specifications.
    productive_minutes_per_full_battery: int = 180
    full_charge_minutes: int = 120
    initial_battery_fraction: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.end <= self.start or (self.end - self.start).total_seconds() % 60:
            raise ValueError("The simulation interval must contain whole elapsed minutes.")
        if self.start.second or self.start.microsecond or self.end.second or self.end.microsecond:
            raise ValueError("The minute model requires timestamps on minute boundaries.")
        if not 0 < self.initial_battery_fraction <= 1:
            raise ValueError("Initial energy must be declared between zero and full.")
        if any(type(value) is not int or value <= 0 for value in
               (self.productive_minutes_per_full_battery, self.full_charge_minutes)):
            raise ValueError("Declared energy durations must be positive.")


def simulate_day(scenario: Scenario, irrigation_start: datetime, settings: Settings) -> dict[str, Any]:
    """Deterministic minute model with energy use, recharge and physical states.

    Water demand and field occupancy are identical for every policy. A full
    battery supplies the declared productive minutes, and partial recharge is
    proportional. No claims about battery percentage thresholds or water litres.
    """
    start = utc(irrigation_start)
    if start.second or start.microsecond or not scenario.need.earliest_start <= start <= scenario.need.latest_start:
        raise ValueError("The proposed water start must be a whole minute inside its approved window.")
    water_end = start + scenario.need.duration
    release = water_end + timedelta(minutes=settings.drying_minutes)
    field_blocks = (*(Interval(block.start, block.end, "OCCUPANCY") for block in scenario.occupancy),
                    Interval(start, water_end, "IRRIGATION"), Interval(water_end, release, "DRYING"))
    if any(block.overlaps(start, release) for block in scenario.occupancy):
        raise ValueError("Water and drying must not overlap an immutable occupancy block.")
    battery = scenario.initial_battery_fraction
    mode, returning = "MOWING", 0
    dock_since: datetime | None = None
    counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    violations: list[str] = []
    timeline: list[dict[str, Any]] = []
    actual_water_started = False
    now = scenario.start
    last_mode: str | None = None
    transitions = 0
    while now < scenario.end:
        active = {block.reason for block in field_blocks if block.start <= now < block.end}
        next_deadline = min(
            [scenario.end, *(
                block.start - timedelta(minutes=settings.return_minutes +
                                        (settings.dock_confirmation_minutes if block.reason == "IRRIGATION" else 0))
                for block in field_blocks if block.start > now and block.reason != "DRYING"
            )]
        )
        if mode == "MOWING" and (active or battery <= 1e-9 or now >= next_deadline):
            mode, returning = "RETURNING", settings.return_minutes
        if mode != "MOWING" and mode != "RETURNING":
            if battery < 1 - 1e-9:
                mode = "CHARGING"
            elif not active and (next_deadline - now) >= timedelta(minutes=settings.minimum_productive_window_minutes):
                mode, dock_since = "MOWING", None
            else:
                mode = "PARKED"
        if now == start:
            actual_water_started = mode in {"CHARGING", "PARKED"} and dock_since is not None and (
                now - dock_since >= timedelta(minutes=settings.dock_confirmation_minutes)
            )
            if not actual_water_started:
                violations.append("IRRIGATION_WITHOUT_CONFIRMED_DOCK")
        if "OCCUPANCY" in active and mode in {"MOWING", "RETURNING"}:
            violations.append("MOWER_ON_OCCUPIED_FIELD")
        if ("IRRIGATION" in active or "DRYING" in active) and mode == "MOWING":
            violations.append("MOWING_DURING_WATER_OR_DRYING")
        if mode != last_mode:
            transitions += int(last_mode is not None)
            last_mode = mode
        reasons = set(active)
        if mode in {"RETURNING", "CHARGING"}:
            reasons.add(mode)
        if mode == "PARKED" and not active:
            reasons.add("MINIMUM_WINDOW_OR_DOCK_CONFIRMATION")
        if mode == "MOWING":
            primary = "PRODUCTIVE_MOWING"
            battery = max(0.0, battery - 1 / scenario.productive_minutes_per_full_battery)
        else:
            primary = next((reason for reason in ("OCCUPANCY", "IRRIGATION", "DRYING", "RETURNING", "CHARGING",
                                                  "MINIMUM_WINDOW_OR_DOCK_CONFIRMATION") if reason in reasons), "UNKNOWN")
        counts[mode] += 1
        primary_counts[primary] += 1
        reason_counts.update(reasons)
        if not active:
            counts["FIELD_SUITABLE"] += 1
        segment = {"start_utc": now.isoformat(), "end_utc": (now + MINUTE).isoformat(),
                   "mower": mode, "primary": primary, "all_reasons": sorted(reasons)}
        if timeline and all(timeline[-1][key] == segment[key] for key in ("mower", "primary", "all_reasons")):
            timeline[-1]["end_utc"] = segment["end_utc"]
        else:
            timeline.append(segment)
        if mode == "RETURNING":
            returning -= 1
            if returning == 0:
                mode, dock_since = "PARKED", now + MINUTE
        elif mode == "CHARGING":
            battery = min(1.0, battery + 1 / scenario.full_charge_minutes)
        now += MINUTE
    total = int((scenario.end - scenario.start).total_seconds() / 60)
    fulfilled = actual_water_started and water_end <= scenario.end
    return {
        "simulation_only": True,
        "irrigation_start_utc": start.isoformat(), "irrigation_end_utc": water_end.isoformat(),
        "dry_until_utc": release.isoformat(), "elapsed_minutes": total,
        "productive_mowing_minutes": counts["MOWING"], "return_minutes": counts["RETURNING"],
        "charging_minutes": counts["CHARGING"], "parked_minutes": counts["PARKED"],
        "field_suitable_minutes": counts["FIELD_SUITABLE"],
        "field_window_utilization_percent": round(100 * counts["MOWING"] / counts["FIELD_SUITABLE"], 2) if counts["FIELD_SUITABLE"] else 0,
        "nonproductive_union_minutes": total - counts["MOWING"],
        "primary_reason_minutes_exclusive": dict(sorted(primary_counts.items())),
        "reason_minutes_including_overlaps": dict(sorted(reason_counts.items())),
        "ready_wait_without_field_block_minutes": reason_counts["MINIMUM_WINDOW_OR_DOCK_CONFIRMATION"],
        "irrigation_minutes": int(scenario.need.duration.total_seconds() / 60) if fulfilled else 0,
        "water_volume_litres": None, "water_needs_fulfilled": int(fulfilled), "water_needs_missed": int(not fulfilled),
        "mower_state_transitions": transitions, "final_battery_fraction": round(battery, 6),
        "safety_violations": sorted(set(violations)), "timeline": timeline,
    }


@dataclass(frozen=True)
class Proposal:
    strategy: str
    selected_start: datetime | None
    expected_gain_minutes: int
    candidate_count: int
    blockers: tuple[str, ...]
    shadow_only: bool = True


def propose(scenario: Scenario, *, now: datetime, evidence: SafetyEvidence, settings: Settings,
            strategy: str = "simple") -> Proposal:
    """Compare a single charging-event rule with bounded exhaustive lookahead."""
    if strategy not in {"simple", "predictive"}:
        raise ValueError("Unknown offline planning strategy.")
    now = utc(now)
    if not settings.enabled:
        return Proposal(strategy, None, 0, 0, ("DISABLED",))
    if evidence.activity != "CHARGING":
        return Proposal(strategy, None, 0, 0, ("NO_CONFIRMED_CHARGING_EVENT",))
    candidate = now.replace(second=0, microsecond=0)
    if candidate < now:
        candidate += MINUTE
    if strategy == "simple":
        candidates = [candidate]
    else:
        first = max(candidate, scenario.need.earliest_start,
                    scenario.need.original_start - timedelta(minutes=settings.maximum_advance_minutes))
        count = int((scenario.need.latest_start - first).total_seconds() // (60 * settings.candidate_step_minutes))
        candidates = [first + timedelta(minutes=index * settings.candidate_step_minutes) for index in range(max(0, count + 1))]
    baseline = simulate_day(scenario, scenario.need.original_start, settings)
    accepted: list[tuple[int, datetime]] = []
    rejected: list[str] = []
    for candidate in candidates:
        blockers = safety_blockers(scenario.need, candidate, now, evidence, scenario.occupancy, settings, before_start=False)
        if blockers:
            rejected.extend(blockers)
            continue
        result = simulate_day(scenario, candidate, settings)
        if result["safety_violations"] or not result["water_needs_fulfilled"]:
            rejected.append("SIMULATED_SAFETY_OR_WATER_FAILURE")
            continue
        gain = result["productive_mowing_minutes"] - baseline["productive_mowing_minutes"]
        accepted.append((gain, candidate))
    if not accepted:
        return Proposal(strategy, None, 0, len(candidates), tuple(dict.fromkeys(rejected)) or ("NO_CANDIDATE",))
    gain, selected = sorted(accepted, key=lambda item: (-item[0], item[1]))[0]
    if gain < settings.minimum_gain_minutes:
        return Proposal(strategy, None, gain, len(candidates), ("GAIN_BELOW_THRESHOLD",))
    return Proposal(strategy, selected, gain, len(candidates), ())


class JournalConflict(RuntimeError):
    pass


class SimulationJournal:
    """Durable, CAS-protected simulation records with no expiring command lease.

    One occurrence identity is reserved once. An uncertain response stays
    uncertain across restarts; neither a second scheduler nor the original
    timer gets another simulated attempt. No database value reaches a device.
    """

    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(str(path), timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS intents (need_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def load(self, need_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT revision,payload FROM intents WHERE need_id=?", (need_id,)).fetchone()
        return {**json.loads(row[1]), "revision": row[0]} if row else None

    def reserve(self, need: WaterNeed, proposal: Proposal) -> dict[str, Any]:
        if proposal.selected_start is None or proposal.blockers or not proposal.shadow_only:
            raise JournalConflict("Only a successful shadow proposal can be reserved.")
        payload = {"need": need.to_dict(), "planned_start": utc(proposal.selected_start).isoformat(),
                   "status": "RESERVED", "simulation_only": True}
        encoded = json.dumps(payload, sort_keys=True)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO intents VALUES (?,1,?)", (need.need_id, encoded))
            stored = self.load(need.need_id)
            if stored is None or stored["need"] != payload["need"] or stored["planned_start"] != payload["planned_start"]:
                raise JournalConflict("The occurrence is already reserved; automatic reshuffling is forbidden.")
        return stored

    def _change(self, need_id: str, revision: int, statuses: set[str], update) -> dict[str, Any]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.load(need_id)
            if current is None or current["revision"] != revision or current["status"] not in statuses:
                raise JournalConflict("Stale revision or an incompatible persistent intent.")
            changed = update(dict(current))
            changed.pop("revision", None)
            self.connection.execute("UPDATE intents SET revision=?,payload=? WHERE need_id=? AND revision=?",
                                    (revision + 1, json.dumps(changed, sort_keys=True), need_id, revision))
            self.connection.commit()
            return {**changed, "revision": revision + 1}
        except Exception:
            self.connection.rollback()
            raise

    def claim_simulated_start(self, need_id: str, revision: int, *, now: datetime, evidence: SafetyEvidence,
                              occupancy: Iterable[Interval], settings: Settings) -> dict[str, Any]:
        blocks = tuple(occupancy)

        def claim(current):
            if current.get("manual_stop_latched"):
                raise JournalConflict("MANUAL_STOP_LATCHED")
            need = WaterNeed.from_dict(current["need"])
            planned = utc(datetime.fromisoformat(current["planned_start"]))
            if utc(now) != planned:
                raise JournalConflict("A missed or premature start requires a new explicit review.")
            blockers = safety_blockers(need, planned, now, evidence, blocks, settings, before_start=True)
            if blockers:
                raise JournalConflict(",".join(blockers))
            return {**current, "status": "INTENT_RECORDED", "intent_at": utc(now).isoformat(),
                    "attempt_id": "SIMULATION_ONLY:" + hashlib.sha256(need_id.encode()).hexdigest()[:24]}

        return self._change(need_id, revision, {"RESERVED"}, claim)

    def latch_manual_stop(self, need_id: str, revision: int) -> dict[str, Any]:
        # Replanning and a cleared transient UI status cannot erase this latch.
        # A physical action already in flight still needs separate reconciliation.
        return self._change(need_id, revision,
                            {"RESERVED", "INTENT_RECORDED", "OUTCOME_UNKNOWN", "RUNNING_CONFIRMED",
                             "COMPLETED", "PARTIAL_NEEDS_REVIEW"},
                            lambda current: {**current, "manual_stop_latched": True})

    def response_lost(self, need_id: str, revision: int) -> dict[str, Any]:
        return self._change(need_id, revision, {"INTENT_RECORDED"}, lambda current: {**current, "status": "OUTCOME_UNKNOWN"})

    def confirm_started(self, need_id: str, revision: int, *, observed_at: datetime) -> dict[str, Any]:
        def started(current):
            if utc(observed_at) < utc(datetime.fromisoformat(current["intent_at"])):
                raise JournalConflict("Physical evidence cannot predate the simulated intent.")
            return {**current, "status": "RUNNING_CONFIRMED", "actual_start": utc(observed_at).isoformat()}

        return self._change(need_id, revision, {"INTENT_RECORDED", "OUTCOME_UNKNOWN"}, started)

    def confirm_zone_ends(self, need_id: str, revision: int, zone_runs: Iterable[Interval], *, settings: Settings) -> dict[str, Any]:
        runs = tuple(zone_runs)

        def completed(current):
            need = WaterNeed.from_dict(current["need"])
            if len(runs) != len(need.zones) or {run.reason for run in runs} != {zone.zone_id for zone in need.zones}:
                raise JournalConflict("Every required zone needs its own physical end evidence.")
            ordered = sorted(runs, key=lambda run: run.start)
            if ordered[0].start < utc(datetime.fromisoformat(current["actual_start"])):
                raise JournalConflict("Zone evidence predates the observed program start.")
            if any(second.start < first.end for first, second in zip(ordered, ordered[1:])):
                raise JournalConflict("Unexpected overlapping zones require investigation.")
            delivered = {run.reason: (run.end - run.start).total_seconds() / 60 for run in runs}
            fulfilled = all(delivered[zone.zone_id] >= zone.minutes for zone in need.zones)
            end = max(run.end for run in runs)
            return {**current, "status": "COMPLETED" if fulfilled else "PARTIAL_NEEDS_REVIEW",
                    "water_need_fulfilled": fulfilled, "actual_end": end.isoformat(),
                    "dry_until": (end + timedelta(minutes=settings.drying_minutes)).isoformat(),
                    "delivered_zone_minutes": delivered}

        return self._change(need_id, revision, {"RUNNING_CONFIRMED"}, completed)

    def original_disposition(self, need_id: str) -> str:
        return "SUPPRESSED_IN_SIMULATION" if self.load(need_id) else "BASELINE_ONLY"


@dataclass(frozen=True)
class DryingEvidence:
    """Separate physical deadline from telemetry confidence, for failure replay."""
    dry_until: datetime | None = None
    last_clear_observed_at: datetime | None = None
    unresolved_since: datetime | None = None

    def irrigation_ended(self, at: datetime, settings: Settings) -> "DryingEvidence":
        deadline = utc(at) + timedelta(minutes=settings.drying_minutes)
        return replace(self, dry_until=max(utc(self.dry_until), deadline) if self.dry_until else deadline)

    def status_failed(self, at: datetime) -> "DryingEvidence":
        return replace(self, unresolved_since=self.unresolved_since or self.last_clear_observed_at or utc(at))

    def clear_observed(self, at: datetime, settings: Settings, *, gap_water_impossible: bool = False,
                       complete_history_since: datetime | None = None,
                       observed_irrigation_ends: tuple[datetime, ...] = ()) -> "DryingEvidence":
        at = utc(at)
        if self.last_clear_observed_at and at <= utc(self.last_clear_observed_at):
            raise ValueError("Old telemetry must not rewrite the wetness timeline.")
        unknown_since = self.unresolved_since
        if self.last_clear_observed_at and at - utc(self.last_clear_observed_at) > timedelta(seconds=settings.telemetry_max_age_seconds):
            unknown_since = unknown_since or utc(self.last_clear_observed_at)
        if not self.last_clear_observed_at and complete_history_since is None and not gap_water_impossible:
            unknown_since = unknown_since or at
        covered = complete_history_since is not None and utc(complete_history_since) <= (unknown_since or at)
        result = replace(self, last_clear_observed_at=at,
                         unresolved_since=None if gap_water_impossible or covered else unknown_since)
        for end in observed_irrigation_ends:
            if utc(end) > at:
                raise ValueError("History cannot prove a future irrigation end.")
            result = result.irrigation_ended(end, settings)
        return result

    def conservatively_bound_unknown_end(self, all_zones_off_at: datetime, *, no_pending_commands: bool,
                                         settings: Settings) -> "DryingEvidence":
        if not no_pending_commands:
            raise ValueError("A delayed possible command prevents bounding the last water end.")
        result = self.irrigation_ended(all_zones_off_at, settings)
        return replace(result, last_clear_observed_at=utc(all_zones_off_at), unresolved_since=None)

    def release_blockers(self, now: datetime, settings: Settings) -> tuple[str, ...]:
        now = utc(now)
        blockers = []
        if self.dry_until and now < utc(self.dry_until):
            blockers.append("PHYSICAL_DRYING")
        if self.unresolved_since:
            blockers.append("POSSIBLE_UNOBSERVED_IRRIGATION")
        if self.last_clear_observed_at is None or not 0 <= (now - utc(self.last_clear_observed_at)).total_seconds() <= settings.telemetry_max_age_seconds:
            blockers.append("IRRIGATION_TELEMETRY_NOT_FRESH")
        return tuple(blockers)


def example_scenario() -> tuple[Scenario, datetime, SafetyEvidence]:
    """Declared synthetic day; every 'confirmed' input here is simulated."""
    def at(clock: str) -> datetime:
        return local_instant(f"2026-09-15T{clock}:00")

    need = WaterNeed(
        need_id="SIMULATION:rasen:2026-09-15:required-program-1",
        demand_reference="SIMULATED approved daily need, unchanged seven zone durations",
        earliest_start=at("03:00"), original_start=at("04:30"), latest_start=at("07:30"),
        zones=tuple(ZoneNeed(f"SIMULATED_ZONE_{index}", minutes)
                    for index, minutes in enumerate((20, 20, 20, 20, 20, 30, 30), 1)),
        required=True, timing_window_approved=True,
    )
    scenario = Scenario(at("00:00"), local_instant("2026-09-16T00:00:00"), need,
                        (Interval(at("16:30"), at("20:30"), "Training including existing buffers"),))
    now = at("03:05")
    evidence = SafetyEvidence(
        mower_observed_at=now, irrigation_observed_at=now, occupancy_observed_at=now,
        dock_observations=(at("03:04"), now),
        occupancy_complete_from=scenario.start, occupancy_complete_until=scenario.end,
        activity="CHARGING", connected=True, own_park_confirmed=True,
        station_and_paths_safe=True, need_still_current=True,
        mower_held_until=at("13:00"), original_suppressed_until=at("13:00"),
    )
    return scenario, now, evidence
