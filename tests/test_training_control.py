from __future__ import annotations

from dataclasses import replace
import copy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, StateConflictError
from occupancy.service import build_occupancy_payload
from occupancy.training_calendar import (
    calendar_digest,
    is_brandenburg_statutory_holiday,
    legacy_source_hashes,
    resolve_training_calendar,
)
from occupancy.training_control import (
    TrainingControlChanged,
    initialize_training_control,
    next_local_midnight,
    resolve_training_control,
    schedule_winter_training,
    snapshot_from_state,
)
from occupancy.training_runtime import (
    ENVELOPE_KEY,
    make_training_envelope,
    resolve_runtime_training,
)
from platzwart_console import PlatzwartError, live_status, request_action
from test_training_calendar import approve_synthetic, fixture


UTC = timezone.utc
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
ACTIVE_ENV = {
    "WINTER_TRAINING_CONTROL_ENABLED": "true",
    "SHARED_TRAINING_MODE": "ACTIVE",
}
SHADOW_ENV = {**ACTIVE_ENV, "SHARED_TRAINING_MODE": "SHADOW"}
HISTORY_START = "2026-08-10T22:00:00+00:00"
ROOT = Path(__file__).resolve().parents[1]


def control_state(**values):
    return AutomationState(
        revision=values.pop("revision", 1),
        winter_training_history_valid_from_utc=HISTORY_START,
        winter_training_history_approval_reference="test-approval",
        **values,
    )


def test_shadow_reads_confirmed_control_but_keeps_it_non_active():
    disabled = resolve_training_control({}, now_utc=NOW)
    assert disabled.public_payload() == {
        "available": False,
        "active": None,
        "pending": None,
        "effectiveAt": None,
        "stateRevision": None,
        "trainingRevision": None,
        "nextEffectiveAt": "2026-09-09T22:00:00+00:00",
    }
    class ReadOnlyStore(InMemoryStateStore):
        def save(self, *_args, **_kwargs):
            raise AssertionError("SHADOW must never write training control")

    shadow = resolve_training_control(
        SHADOW_ENV, now_utc=NOW,
        state_store_factory=lambda _env: ReadOnlyStore(control_state()),
    )
    assert shadow.available
    assert shadow.season == "Sommer"
    assert shadow.pending_enabled is None


@pytest.mark.parametrize(
    ("winter_enabled", "expected_ids"),
    [(False, ["test-e1", "test-a"]), (True, ["test-winter"])],
)
def test_shadow_uses_confirmed_persistent_season_only_for_candidate(
    winter_enabled, expected_ids,
):
    store = InMemoryStateStore(control_state(winter_training_enabled=winter_enabled))
    snapshot = resolve_training_control(
        SHADOW_ENV, now_utc=NOW, state_store_factory=lambda _env: store,
    )
    document, occupancy, mower = fixture()
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW,
    )
    result = resolve_runtime_training(
        {ENVELOPE_KEY: envelope}, consumer="mower", environment=SHADOW_ENV,
        legacy_config=mower, range_start=datetime(2026, 9, 8, tzinfo=UTC),
        range_end=datetime(2026, 9, 10, tzinfo=UTC), now_utc=NOW,
        control_snapshot=snapshot,
    )
    assert result.batch is None
    assert result.candidate is not None
    assert [event["scheduleId"] for event in result.candidate.events] == expected_ids
    assert store.load().revision == 1


def test_shadow_keeps_missing_history_fail_closed_without_candidate():
    store = InMemoryStateStore(AutomationState(revision=7))
    snapshot = resolve_training_control(
        SHADOW_ENV, now_utc=NOW, state_store_factory=lambda _env: store,
    )
    assert not snapshot.available
    assert snapshot.reason_code == "TRAINING_CONTROL_HISTORY_MISSING"
    document, occupancy, mower = fixture()
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW,
    )
    result = resolve_runtime_training(
        {ENVELOPE_KEY: envelope}, consumer="mower", environment=SHADOW_ENV,
        legacy_config=mower, range_start=datetime(2026, 9, 8, tzinfo=UTC),
        range_end=datetime(2026, 9, 10, tzinfo=UTC), now_utc=NOW,
        control_snapshot=snapshot,
    )
    assert result.batch is None and result.candidate is None
    assert "TRAINING_CONTROL_HISTORY_MISSING" in result.blockers


@pytest.mark.parametrize(
    ("winter_enabled", "expected_ids"),
    [(False, ["test-e1", "test-a"]), (True, ["test-winter"])],
)
def test_shadow_manual_calendar_uses_confirmed_season_after_history_anchor(
    winter_enabled, expected_ids,
):
    """Manual calendars intentionally omit all season date periods."""

    # D15 is the first later Tuesday: the D9 history boundary must still
    # select the manual season after the initialisation day.
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    store = InMemoryStateStore(AutomationState(
        revision=7,
        winter_training_enabled=winter_enabled,
        winter_training_history_valid_from_utc="2026-09-08T22:00:00+00:00",
        winter_training_history_approval_reference="approved-d9-anchor",
    ))
    snapshot = resolve_training_control(
        SHADOW_ENV, now_utc=now, state_store_factory=lambda _env: store,
    )
    document, occupancy, mower = fixture()
    document["season_periods"] = []
    approve_synthetic(document)
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=now,
        manual_season_control=True,
    )
    result = resolve_runtime_training(
        {ENVELOPE_KEY: envelope}, consumer="mower", environment=SHADOW_ENV,
        legacy_config=mower,
        range_start=datetime.fromisoformat("2026-09-15T00:00:00+02:00"),
        range_end=datetime.fromisoformat("2026-09-16T00:00:00+02:00"),
        now_utc=now, control_snapshot=snapshot,
    )
    assert result.batch is None
    assert result.candidate is not None
    assert [item["scheduleId"] for item in result.candidate.events] == expected_ids
    assert store.load().revision == 7


def test_shadow_manual_calendar_rejects_previous_anchor_before_history():
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    store = InMemoryStateStore(AutomationState(
        revision=7,
        winter_training_enabled=False,
        winter_training_history_valid_from_utc="2026-09-08T22:00:00+00:00",
        winter_training_history_approval_reference="approved-d9-anchor",
    ))
    snapshot = resolve_training_control(
        SHADOW_ENV, now_utc=now, state_store_factory=lambda _env: store,
    )
    document, occupancy, mower = fixture()
    document["season_periods"] = []
    approve_synthetic(document)
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=now,
        manual_season_control=True,
    )
    result = resolve_runtime_training(
        {ENVELOPE_KEY: envelope}, consumer="mower", environment=SHADOW_ENV,
        legacy_config=mower,
        range_start=datetime.fromisoformat("2026-09-09T00:00:00+02:00"),
        range_end=datetime.fromisoformat("2026-09-10T00:00:00+02:00"),
        now_utc=now, control_snapshot=snapshot,
    )
    assert result.batch is None and result.candidate is None
    assert "TRAINING_CONTROL_SEASON_UNAVAILABLE" in result.blockers


@pytest.mark.parametrize(
    "now_utc, expected",
    [
        (datetime(2027, 3, 28, 10, tzinfo=UTC), datetime(2027, 3, 28, 22, tzinfo=UTC)),
        (datetime(2026, 10, 25, 10, tzinfo=UTC), datetime(2026, 10, 25, 23, tzinfo=UTC)),
    ],
)
def test_next_local_midnight_is_dst_safe(now_utc, expected):
    assert next_local_midnight(now_utc) == expected


def test_toggle_is_persisted_idempotently_and_only_takes_effect_at_midnight():
    store = InMemoryStateStore(control_state(maintenance_mode=True))
    initial = resolve_training_control(
        ACTIVE_ENV, now_utc=NOW, state_store_factory=lambda _env: store
    )
    scheduled = schedule_winter_training(
        ACTIVE_ENV,
        enabled=True,
        request_id="winter-1",
        expected_training_revision=initial.training_revision,
        now_utc=NOW,
        state_store_factory=lambda _env: store,
    )
    assert scheduled.winter_enabled is False
    assert scheduled.pending_enabled is True
    assert scheduled.effective_at_utc == "2026-09-09T22:00:00+00:00"
    assert scheduled.next_effective_at_utc == scheduled.effective_at_utc
    assert store.load().maintenance_mode is True
    assert store.load().winter_training_control_revision == 1

    replay = schedule_winter_training(
        ACTIVE_ENV,
        enabled=True,
        request_id="winter-1",
        expected_training_revision="0" * 64,
        now_utc=NOW + timedelta(minutes=1),
        state_store_factory=lambda _env: store,
    )
    assert replay.training_revision == scheduled.training_revision
    assert store.load().revision == 2
    with pytest.raises(TrainingControlChanged, match="REQUEST_ID_REUSED"):
        schedule_winter_training(
            ACTIVE_ENV,
            enabled=False,
            request_id="winter-1",
            expected_training_revision=scheduled.training_revision,
            now_utc=NOW + timedelta(minutes=2),
            state_store_factory=lambda _env: store,
        )

    before = snapshot_from_state(
        store.load(), now_utc=datetime(2026, 9, 9, 21, 59, 59, tzinfo=UTC)
    )
    after = snapshot_from_state(
        store.load(), now_utc=datetime(2026, 9, 9, 22, 0, tzinfo=UTC)
    )
    assert before.season == "Sommer" and before.pending_enabled is True
    assert after.season == "Winter" and after.pending_enabled is None


def test_restart_reads_the_same_pending_switch(tmp_path):
    from mower.state_store import JsonFileStateStore

    path = tmp_path / "state.json"
    path.write_text(json.dumps(control_state().to_dict()), encoding="utf-8")
    store = JsonFileStateStore(path)
    initial = resolve_training_control(
        ACTIVE_ENV, now_utc=NOW, state_store_factory=lambda _env: store
    )
    expected = schedule_winter_training(
        ACTIVE_ENV,
        enabled=True,
        request_id="restart-1",
        expected_training_revision=initial.training_revision,
        now_utc=NOW,
        state_store_factory=lambda _env: store,
    )
    restarted = resolve_training_control(
        ACTIVE_ENV,
        now_utc=NOW + timedelta(hours=1),
        state_store_factory=lambda _env: JsonFileStateStore(path),
    )
    assert restarted.training_revision == expected.training_revision
    assert restarted.pending_enabled is True


def test_unrelated_state_race_is_retried_without_lost_update():
    class UnrelatedRaceStore(InMemoryStateStore):
        raced = False

        def save(self, state, *, expected_revision):
            if not self.raced:
                self.raced = True
                current = self.load()
                super().save(
                    replace(
                        current,
                        revision=current.revision + 1,
                        maintenance_mode=True,
                    ),
                    expected_revision=current.revision,
                )
                raise StateConflictError("unrelated writer")
            return super().save(state, expected_revision=expected_revision)

    store = UnrelatedRaceStore(control_state())
    initial = snapshot_from_state(store.load(), now_utc=NOW)
    scheduled = schedule_winter_training(
        ACTIVE_ENV,
        enabled=True,
        request_id="race-unrelated",
        expected_training_revision=initial.training_revision,
        now_utc=NOW,
        state_store_factory=lambda _env: store,
    )
    assert scheduled.pending_enabled is True
    assert store.load().maintenance_mode is True
    assert store.load().revision == 3


def test_competing_training_writer_is_never_overwritten():
    class TrainingRaceStore(InMemoryStateStore):
        raced = False

        def save(self, state, *, expected_revision):
            if not self.raced:
                self.raced = True
                current = self.load()
                super().save(
                    replace(
                        current,
                        revision=current.revision + 1,
                        winter_training_pending_enabled=True,
                        winter_training_effective_utc="2026-09-09T22:00:00+00:00",
                        winter_training_control_revision=1,
                    ),
                    expected_revision=current.revision,
                )
                raise StateConflictError("training writer")
            return super().save(state, expected_revision=expected_revision)

    store = TrainingRaceStore(control_state())
    initial = snapshot_from_state(store.load(), now_utc=NOW)
    with pytest.raises(TrainingControlChanged):
        schedule_winter_training(
            ACTIVE_ENV,
            enabled=True,
            request_id="race-training",
            expected_training_revision=initial.training_revision,
            now_utc=NOW,
            state_store_factory=lambda _env: store,
        )
    assert store.load().winter_training_pending_enabled is True


def test_state_read_failure_is_unknown_and_runtime_fails_closed():
    class BrokenStore:
        def load(self):
            raise RuntimeError("secret storage detail")

    snapshot = resolve_training_control(
        ACTIVE_ENV, now_utc=NOW, state_store_factory=lambda _env: BrokenStore()
    )
    assert not snapshot.available and snapshot.winter_enabled is None
    document, occupancy, mower = fixture()
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW
    )
    runtime = resolve_runtime_training(
        {ENVELOPE_KEY: envelope},
        consumer="occupancy",
        environment=ACTIVE_ENV,
        legacy_config=occupancy,
        range_start=datetime(2026, 9, 8, tzinfo=UTC),
        range_end=datetime(2026, 9, 10, tzinfo=UTC),
        now_utc=NOW,
        control_snapshot=snapshot,
    )
    assert runtime.batch is None and runtime.blocking_required
    assert "secret storage detail" not in " ".join(runtime.blockers)


@pytest.mark.parametrize(
    "holiday",
    [
        date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 5),
        date(2026, 4, 6), date(2026, 5, 14), date(2026, 5, 24),
        date(2026, 5, 25), date(2026, 10, 31), date(2026, 12, 26),
        date(2027, 3, 26), date(2027, 3, 28), date(2027, 3, 29),
        date(2027, 5, 6), date(2027, 5, 16), date(2027, 5, 17),
    ],
)
def test_brandenburg_statutory_holiday_rules_2026_2027(holiday):
    assert is_brandenburg_statutory_holiday(holiday)


def test_school_holiday_is_not_blanket_statutory_holiday():
    assert not is_brandenburg_statutory_holiday(date(2026, 7, 20))


def _friday_calendar():
    document, occupancy, mower = fixture()
    summer = [{
        "id": "friday-training",
        "weekday": "Freitag",
        "start": "17:00",
        "end": "18:30",
        "team": "Testteam",
        "resource_id": "rasen",
    }]
    winter = [{**summer[0], "id": "friday-winter", "resource_id": "kunstrasen"}]
    document["weekly_patterns"] = {"Sommer": summer, "Winter": winter}
    occupancy["seasons"]["Sommer"]["weekly"] = summer
    occupancy["seasons"]["Winter"]["weekly"] = winter
    mower["training"]["weekly"] = [
        {key: value for key, value in summer[0].items() if key != "resource_id"}
    ]
    document["legacy_sources"] = legacy_source_hashes(occupancy, mower)
    approve_synthetic(document)
    return document, occupancy, mower


def test_holiday_removes_training_but_never_removes_a_match(tmp_path):
    document, occupancy, mower = _friday_calendar()
    start = datetime.fromisoformat("2026-12-25T00:00:00+01:00")
    end = datetime.fromisoformat("2026-12-26T00:00:00+01:00")
    batch = resolve_training_calendar(
        document,
        range_start=start,
        range_end=end,
        now_utc=NOW,
        occupancy_config=occupancy,
        mower_config=mower,
        season_override="Sommer",
    ).batch
    assert batch is not None and batch.events == ()

    occupancy["matches"] = {
        "resource_id": "rasen",
        "buffer_before_minutes": 60,
        "buffer_after_minutes": 60,
    }
    occupancy["schema_version"] = 1
    config_path = tmp_path / "occupancy.json"
    matches_path = tmp_path / "matches.json"
    config_path.write_text(json.dumps(occupancy), encoding="utf-8")
    matches_path.write_text(json.dumps({
        "schemaVersion": 2,
        "status": "ok",
        "matches": [{
            "id": "holiday-game",
            "calendar": "Rasen",
            "place": "rasen",
            "title": "Testspiel",
            "team": "SSV 53",
            "teamCategory": "Herren",
            "teamRole": "home",
            "homeTeam": "SSV 53",
            "awayTeam": "Gast",
            "competition": "Test",
            "competitionFormat": "11er",
            "matchType": "Testspiel",
            "matchDurationMinutes": 90,
            "durationRule": "synthetic-test",
            "start": "2026-12-25T15:00:00+01:00",
            "kickoff": "2026-12-25T15:00:00+01:00",
            "end": "2026-12-25T16:30:00+01:00",
            "occupancyStart": "2026-12-25T14:00:00+01:00",
            "occupancyEnd": "2026-12-25T17:30:00+01:00",
        }],
    }), encoding="utf-8")
    payload = build_occupancy_payload(
        config_path=config_path,
        matches_path=matches_path,
        start="2026-12-25",
        end="2026-12-26",
        generated_at=NOW,
        training_batch=batch,
    )
    assert [item["source"] for item in payload["events"]] == ["match"]


def test_manual_snapshot_replaces_date_season_for_all_five_consumers():
    document, occupancy, mower = fixture()
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW
    )
    snapshot = snapshot_from_state(control_state(), now_utc=NOW)
    start = datetime.fromisoformat("2026-11-03T00:00:00+01:00")
    end = datetime.fromisoformat("2026-11-04T00:00:00+01:00")
    batches = []
    for label in ("app", "conflict", "cancellation", "relocation", "mower"):
        consumer = "mower" if label == "mower" else "occupancy"
        result = resolve_runtime_training(
            {ENVELOPE_KEY: envelope},
            consumer=consumer,
            environment=ACTIVE_ENV,
            legacy_config=mower if consumer == "mower" else occupancy,
            range_start=start,
            range_end=end,
            now_utc=NOW,
            control_snapshot=snapshot,
        )
        assert result.control_snapshot is snapshot
        assert result.batch is not None
        batches.append(result.batch)
    expected_ids = [item["id"] for item in batches[0].events]
    assert len(expected_ids) == 2
    assert all([item["id"] for item in batch.events] == expected_ids for batch in batches)
    assert all(batch.content_sha256 == batches[0].content_sha256 for batch in batches)


def test_pending_transition_changes_tomorrows_planning_before_midnight():
    now = datetime(2026, 9, 7, 12, tzinfo=UTC)  # Monday, 14:00 local
    store = InMemoryStateStore(control_state())
    initial = resolve_training_control(
        ACTIVE_ENV, now_utc=now, state_store_factory=lambda _env: store
    )
    pending = schedule_winter_training(
        ACTIVE_ENV,
        enabled=True,
        request_id="tomorrow-winter",
        expected_training_revision=initial.training_revision,
        now_utc=now,
        state_store_factory=lambda _env: store,
    )
    document, occupancy, mower = fixture()
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=now
    )
    runtime = resolve_runtime_training(
        {ENVELOPE_KEY: envelope},
        consumer="mower",
        environment=ACTIVE_ENV,
        legacy_config=mower,
        range_start=datetime.fromisoformat("2026-09-07T00:00:00+02:00"),
        range_end=datetime.fromisoformat("2026-09-09T00:00:00+02:00"),
        now_utc=now,
        control_snapshot=pending,
    )
    assert runtime.batch is not None
    assert [item["scheduleId"] for item in runtime.batch.events] == ["test-winter"]
    assert pending.season_for_anchor(date(2026, 9, 7)) == "Sommer"
    assert pending.season_for_anchor(date(2026, 9, 8)) == "Winter"

    shadow_snapshot = resolve_training_control(
        SHADOW_ENV, now_utc=now, state_store_factory=lambda _env: store,
    )
    shadow = resolve_runtime_training(
        {ENVELOPE_KEY: envelope}, consumer="mower", environment=SHADOW_ENV,
        legacy_config=mower,
        range_start=datetime.fromisoformat("2026-09-07T00:00:00+02:00"),
        range_end=datetime.fromisoformat("2026-09-09T00:00:00+02:00"),
        now_utc=now, control_snapshot=shadow_snapshot,
    )
    assert shadow.batch is None
    assert shadow.candidate is not None
    assert [item["scheduleId"] for item in shadow.candidate.events] == ["test-winter"]
    assert shadow_snapshot.pending_enabled is True


def _overnight_transition_calendar():
    document, occupancy, mower = fixture()
    summer = [{
        "id": "summer-overnight",
        "weekday": "Montag",
        "start": "23:00",
        "end": "01:00",
        "team": "Sommer spät",
        "resource_id": "rasen",
    }]
    winter = [{
        "id": "winter-tuesday",
        "weekday": "Dienstag",
        "start": "17:00",
        "end": "18:00",
        "team": "Winter Dienstag",
        "resource_id": "kunstrasen",
    }]
    document["weekly_patterns"] = {"Sommer": summer, "Winter": winter}
    occupancy["seasons"]["Sommer"]["weekly"] = summer
    occupancy["seasons"]["Winter"]["weekly"] = winter
    mower["training"]["weekly"] = [
        {key: value for key, value in summer[0].items() if key != "resource_id"}
    ]
    document["legacy_sources"] = legacy_source_hashes(occupancy, mower)
    approve_synthetic(document)
    return document, occupancy, mower


def test_second_toggle_keeps_previous_anchor_overnight_transition_history():
    monday = datetime(2026, 9, 7, 12, tzinfo=UTC)
    tuesday_after_midnight = datetime(2026, 9, 7, 22, 15, tzinfo=UTC)
    store = InMemoryStateStore(control_state())
    initial = resolve_training_control(
        ACTIVE_ENV, now_utc=monday, state_store_factory=lambda _env: store
    )
    schedule_winter_training(
        ACTIVE_ENV,
        enabled=True,
        request_id="winter-on",
        expected_training_revision=initial.training_revision,
        now_utc=monday,
        state_store_factory=lambda _env: store,
    )
    matured_token = snapshot_from_state(
        store.load(), now_utc=tuesday_after_midnight
    ).training_revision
    toggled_back = schedule_winter_training(
        ACTIVE_ENV,
        enabled=False,
        request_id="winter-off-next-day",
        expected_training_revision=matured_token,
        now_utc=tuesday_after_midnight,
        state_store_factory=lambda _env: store,
    )
    assert toggled_back.season_for_anchor(date(2026, 9, 7)) == "Sommer"
    assert toggled_back.season_for_anchor(date(2026, 9, 8)) == "Winter"
    assert toggled_back.season_for_anchor(date(2026, 9, 9)) == "Sommer"
    assert len(toggled_back.metadata()["anchorTransitions"]) == 2

    document, occupancy, mower = _overnight_transition_calendar()
    resolved = resolve_training_calendar(
        document,
        range_start=datetime.fromisoformat("2026-09-08T00:00:00+02:00"),
        range_end=datetime.fromisoformat("2026-09-08T02:00:00+02:00"),
        now_utc=tuesday_after_midnight,
        occupancy_config=occupancy,
        mower_config=mower,
        season_selector=toggled_back.season_for_anchor,
    )
    assert resolved.batch is not None
    assert [item["scheduleId"] for item in resolved.batch.events] == [
        "summer-overnight"
    ]


def test_query_before_manual_history_never_invents_a_season():
    document, occupancy, mower = fixture()
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW
    )
    snapshot = snapshot_from_state(control_state(), now_utc=NOW)
    runtime = resolve_runtime_training(
        {ENVELOPE_KEY: envelope},
        consumer="occupancy",
        environment=ACTIVE_ENV,
        legacy_config=occupancy,
        range_start=datetime.fromisoformat("2026-08-04T00:00:00+02:00"),
        range_end=datetime.fromisoformat("2026-08-05T00:00:00+02:00"),
        now_utc=NOW,
        control_snapshot=snapshot,
    )
    assert runtime.batch is None and runtime.blocking_required
    assert "TRAINING_CONTROL_SEASON_UNAVAILABLE" in runtime.blockers


def test_manual_control_candidate_is_exact_and_only_waits_for_final_approval():
    from occupancy.training_calendar import load_calendar, validate_calendar

    candidate = load_calendar(
        ROOT / "occupancy/training_calendar.manual-control.candidate.json"
    )
    occupancy = json.loads(
        (ROOT / "occupancy/config.json").read_text(encoding="utf-8")
    )
    mower = json.loads((ROOT / "mower/config.json").read_text(encoding="utf-8"))
    validation = validate_calendar(
        candidate,
        now_utc=NOW,
        occupancy_config=occupancy,
        mower_config=mower,
        require_season_periods=False,
    )
    assert validation.errors == ()
    blockers = " ".join(validation.activation_blockers)
    assert "SEASON_DATES_UNCONFIRMED" not in blockers
    assert "HOLIDAY_POLICY_UNCONFIRMED" not in blockers
    assert "LEGACY_SOURCE_CHANGED" not in blockers
    assert "CALENDAR_APPROVAL_REQUIRED" in blockers
    assert "APPROVAL_CONTENT_MISMATCH" in blockers
    assert "APPROVAL_TIME_REQUIRED" in blockers
    with pytest.raises(ValueError, match="nicht freigegeben"):
        make_training_envelope(
            candidate,
            occupancy_config=occupancy,
            mower_config=mower,
            now_utc=NOW,
            manual_season_control=True,
        )
    approved_for_test = copy.deepcopy(candidate)
    approved_for_test["approval"] = {
        "status": "approved",
        "reference": "TEST ONLY",
        "approved_at_utc": "2026-09-09T11:00:00+00:00",
        "content_sha256": validation.content_sha256,
    }
    envelope = make_training_envelope(
        approved_for_test,
        occupancy_config=occupancy,
        mower_config=mower,
        now_utc=NOW,
        manual_season_control=True,
    )
    assert envelope["calendar"]["weekly_patterns"] == candidate["weekly_patterns"]


def test_console_action_requires_active_runtime_and_returns_updated_control(monkeypatch):
    store = InMemoryStateStore(control_state())

    class AuditStore:
        def audit(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        "platzwart_console.ConsoleTableStore.from_environment",
        lambda _env: AuditStore(),
    )
    token = snapshot_from_state(store.load(), now_utc=NOW).training_revision
    response = request_action(
        "SET_WINTER_TRAINING",
        "console-winter-1",
        "SET_WINTER_TRAINING",
        ACTIVE_ENV,
        NOW,
        winter_training_enabled=True,
        training_revision=token,
        state_store_factory=lambda _env: store,
    )
    assert response["status"] == "SCHEDULED"
    assert response["trainingControl"]["pending"] is True

    with pytest.raises(PlatzwartError) as caught:
        request_action(
            "SET_WINTER_TRAINING",
            "console-shadow-1",
            "SET_WINTER_TRAINING",
            {**ACTIVE_ENV, "SHARED_TRAINING_MODE": "SHADOW"},
            NOW,
            winter_training_enabled=True,
            training_revision=token,
            state_store_factory=lambda _env: store,
        )
    assert caught.value.code == "TRAINING_CONTROL_UNAVAILABLE"
    assert store.load().revision == 2


def test_invalid_persisted_boolean_is_not_interpreted_as_true():
    with pytest.raises(ValueError, match="boolesch"):
        AutomationState.from_mapping({"winter_training_enabled": "false"})


def test_missing_state_entity_is_unknown_when_control_is_enabled():
    snapshot = resolve_training_control(
        ACTIVE_ENV,
        now_utc=NOW,
        state_store_factory=lambda _env: InMemoryStateStore(),
    )
    assert not snapshot.available
    assert snapshot.reason_code == "TRAINING_CONTROL_STATE_MISSING"


def test_initializer_is_explicit_cas_idempotent_and_preserves_other_state():
    store = InMemoryStateStore(
        AutomationState(revision=7, maintenance_mode=True)
    )
    initialized = initialize_training_control(
        {},
        initial_enabled=False,
        history_valid_from_day=date(2026, 9, 8),
        approval_reference="calendar-approval-2026-09-09",
        request_id="initialize-training-1",
        expected_state_revision=7,
        now_utc=NOW,
        state_store_factory=lambda _env: store,
    )
    assert initialized.available and initialized.season == "Sommer"
    assert initialized.history_valid_from_utc == "2026-09-07T22:00:00+00:00"
    saved = store.load()
    assert saved.maintenance_mode is True
    assert saved.revision == 8
    assert saved.winter_training_history_approval_reference == (
        "calendar-approval-2026-09-09"
    )
    replay = initialize_training_control(
        {},
        initial_enabled=False,
        history_valid_from_day=date(2026, 9, 8),
        approval_reference="calendar-approval-2026-09-09",
        request_id="initialize-training-1",
        expected_state_revision=7,
        now_utc=NOW,
        state_store_factory=lambda _env: store,
    )
    assert replay.training_revision == initialized.training_revision
    assert store.load().revision == 8


def test_initializer_rejects_future_or_unproved_history_and_state_races():
    store = InMemoryStateStore(AutomationState(revision=3))
    with pytest.raises(ValueError, match="APPROVAL_REFERENCE"):
        initialize_training_control(
            {}, initial_enabled=False,
            history_valid_from_day=date(2026, 9, 9), approval_reference="",
            request_id="init", expected_state_revision=3, now_utc=NOW,
            state_store_factory=lambda _env: store,
        )
    with pytest.raises(ValueError, match="CANNOT_START_IN_FUTURE"):
        initialize_training_control(
            {}, initial_enabled=False,
            history_valid_from_day=date(2026, 9, 10), approval_reference="approved",
            request_id="init", expected_state_revision=3, now_utc=NOW,
            state_store_factory=lambda _env: store,
        )
    with pytest.raises(StateConflictError):
        initialize_training_control(
            {}, initial_enabled=False,
            history_valid_from_day=date(2026, 9, 9), approval_reference="approved",
            request_id="init", expected_state_revision=2, now_utc=NOW,
            state_store_factory=lambda _env: store,
        )


def test_status_flag_off_disables_switch_and_active_status_reuses_one_snapshot(
    monkeypatch,
):
    from test_full_failsafe import ENV as FULL_ENV, result as cycle_result

    observed = []
    store = InMemoryStateStore(control_state())

    def read_cycle(**kwargs):
        observed.append(kwargs["training_control_snapshot"])
        return cycle_result(activity="CHARGING")

    monkeypatch.setattr("platzwart_console.run_read_only_cycle", read_cycle)
    monkeypatch.setattr(
        "platzwart_console._clubhouse_events",
        lambda *_args, **_kwargs: {"available": True, "events": [], "message": None},
    )
    monkeypatch.setattr(
        "platzwart_console._dashboard_statistics",
        lambda *_args, **_kwargs: {"available": True},
    )
    monkeypatch.setattr(
        "platzwart_console.AzureTableStateStore.from_environment",
        lambda _env: store,
    )
    disabled = live_status(FULL_ENV, NOW)
    assert disabled["trainingControl"]["available"] is False
    assert observed[-1].reason_code == "TRAINING_CONTROL_DISABLED"

    enabled = live_status({**FULL_ENV, **ACTIVE_ENV}, NOW)
    assert enabled["trainingControl"] == observed[-1].public_payload()
    assert enabled["trainingControl"]["available"] is True

    shadow = live_status({**FULL_ENV, **SHADOW_ENV}, NOW)
    assert shadow["trainingControl"]["available"] is False
    assert observed[-1].reason_code == "TRAINING_CONTROL_REQUIRES_ACTIVE_RUNTIME"
    assert store.load().revision == 1


def test_initializer_cli_requires_admin_route_and_passes_explicit_proof(capsys):
    from scripts.initialize_training_control import (
        CONFIRMATION,
        FUNCTION_ADMIN_URL,
        HOST_KEY_ENV,
        main,
    )

    captured = {}

    def sender(**kwargs):
        captured.update(kwargs)
        return {"initialized": True}

    arguments = [
        "--initial-plan", "Sommer",
        "--history-valid-from", "2026-09-08",
        "--approval-reference", "approved-calendar-digest",
        "--request-id", "init-cli-1",
        "--expected-state-revision", "17",
        "--confirmation", CONFIRMATION,
        "--function-admin-url", FUNCTION_ADMIN_URL,
    ]
    assert main(
        arguments,
        environment={HOST_KEY_ENV: "host-key"},
        function_sender=sender,
    ) == 0
    assert captured["url"] == FUNCTION_ADMIN_URL
    assert captured["host_key"] == "host-key"
    assert captured["payload"] == {
        "initialPlan": "Sommer",
        "historyValidFrom": "2026-09-08",
        "approvalReference": "approved-calendar-digest",
        "requestId": "init-cli-1",
        "expectedStateRevision": 17,
        "confirmation": CONFIRMATION,
    }
    assert json.loads(capsys.readouterr().out)["initialized"] is True

    bad = list(arguments)
    bad[bad.index(CONFIRMATION)] = "WRONG"
    with pytest.raises(SystemExit, match="confirmation muss exakt"):
        main(
            bad,
            environment={HOST_KEY_ENV: "host-key"},
            function_sender=sender,
        )

    without_path = arguments[:-2]
    with pytest.raises(SystemExit, match="exakt freigegebene"):
        main(
            without_path,
            environment={HOST_KEY_ENV: "host-key"},
            function_sender=sender,
        )


def _function_request(method="POST", body=None):
    import azure.functions as func

    return func.HttpRequest(
        method=method,
        url=(
            "https://example.test/api/training-control/initialize"
        ),
        headers={"Content-Type": "application/json"},
        params={},
        body=(json.dumps(body).encode("utf-8") if body is not None else b""),
    )


def _initialization_body(**changes):
    value = {
        "initialPlan": "Sommer",
        "historyValidFrom": "2026-09-08",
        "approvalReference": "approved-calendar-digest",
        "requestId": "initialize-http-1",
        "expectedStateRevision": 7,
        "confirmation": "INITIALIZE_WINTER_TRAINING_CONTROL",
    }
    value.update(changes)
    return value


def test_admin_initialization_route_binding_and_function_count():
    import azure.functions as func
    import function_app

    functions = function_app.app.get_functions()
    assert len(functions) == 16
    route = next(
        item
        for item in functions
        if item.get_function_name() == "ssv53_training_control_initialize"
    )
    trigger = next(
        binding.get_dict_repr()
        for binding in route.get_bindings()
        if binding.get_dict_repr().get("type") == "httpTrigger"
    )
    assert trigger["route"] == "training-control/initialize"
    assert trigger["authLevel"] is func.AuthLevel.ADMIN
    assert {str(method.value) for method in trigger["methods"]} == {"GET", "POST"}
    recovery = next(
        item
        for item in functions
        if item.get_function_name() == "ssv53_recover_unsent_start"
    )
    recovery_trigger = next(
        binding.get_dict_repr()
        for binding in recovery.get_bindings()
        if binding.get_dict_repr().get("type") == "httpTrigger"
    )
    assert recovery_trigger["route"] == "mower/recover-unsent-start"
    assert recovery_trigger["authLevel"] is func.AuthLevel.ADMIN
    assert {str(method.value) for method in recovery_trigger["methods"]} == {
        "GET",
        "POST",
    }


def test_admin_initialization_gate_defaults_off(monkeypatch):
    import function_app

    monkeypatch.delenv("WINTER_TRAINING_INITIALIZATION_ENABLED", raising=False)
    called = []
    monkeypatch.setattr(
        function_app,
        "initialize_training_control",
        lambda *_args, **_kwargs: called.append(True),
    )
    response = function_app.ssv53_training_control_initialize(
        _function_request(body=_initialization_body())
    )
    assert response.status_code == 403
    assert json.loads(response.get_body())["code"] == (
        "TRAINING_CONTROL_INITIALIZATION_DISABLED"
    )
    assert called == []


def test_admin_get_inspects_only_revision_and_training_control(monkeypatch):
    import function_app

    store = InMemoryStateStore(control_state(revision=9))
    monkeypatch.setenv("WINTER_TRAINING_INITIALIZATION_ENABLED", "true")
    monkeypatch.setattr(
        function_app.AzureTableStateStore,
        "from_environment",
        lambda _env: store,
    )
    response = function_app.ssv53_training_control_initialize(
        _function_request(method="GET")
    )
    assert response.status_code == 200
    payload = json.loads(response.get_body())
    assert set(payload) == {"stateRevision", "initialized", "trainingControl"}
    assert payload["stateRevision"] == 9
    assert payload["initialized"] is True
    assert "maintenance_mode" not in response.get_body().decode("utf-8")


def test_admin_post_schema_cas_and_replay(monkeypatch):
    import function_app
    from occupancy.training_control import initialize_training_control as initialize

    store = InMemoryStateStore(AutomationState(revision=7, maintenance_mode=True))
    monkeypatch.setenv("WINTER_TRAINING_INITIALIZATION_ENABLED", "true")

    def invoke(environment, **kwargs):
        return initialize(
            environment,
            **kwargs,
            state_store_factory=lambda _env: store,
        )

    monkeypatch.setattr(function_app, "initialize_training_control", invoke)
    request = _function_request(body=_initialization_body())
    first = function_app.ssv53_training_control_initialize(request)
    replay = function_app.ssv53_training_control_initialize(request)
    assert first.status_code == replay.status_code == 200
    assert store.load().revision == 8
    assert store.load().maintenance_mode is True
    conflict = function_app.ssv53_training_control_initialize(
        _function_request(
            body=_initialization_body(requestId="different", expectedStateRevision=7)
        )
    )
    assert conflict.status_code == 409
    assert json.loads(conflict.get_body())["code"] == "TRAINING_CONTROL_CHANGED"

    invalid = function_app.ssv53_training_control_initialize(
        _function_request(body={**_initialization_body(), "extra": True})
    )
    assert invalid.status_code == 400
    assert json.loads(invalid.get_body())["code"] == (
        "TRAINING_CONTROL_INITIALIZATION_INVALID"
    )

    for changes in (
        {"confirmation": "WRONG"},
        {"initialPlan": "Frühling"},
        {"historyValidFrom": "2026-09-08T00:00:00"},
        {"expectedStateRevision": True},
        {"approvalReference": 123},
    ):
        response = function_app.ssv53_training_control_initialize(
            _function_request(body=_initialization_body(**changes))
        )
        assert response.status_code == 400


def test_initializer_cli_http_inspect_and_post_never_expose_key(capsys):
    from scripts.initialize_training_control import (
        CONFIRMATION,
        FUNCTION_ADMIN_URL,
        HOST_KEY_ENV,
        main,
    )

    calls = []

    def sender(**kwargs):
        calls.append(kwargs)
        return {"stateRevision": 7} if kwargs["inspect"] else {"initialized": True}

    environment = {HOST_KEY_ENV: "host-secret-value"}
    assert main(
        ["--function-admin-url", FUNCTION_ADMIN_URL, "--inspect"],
        environment=environment,
        function_sender=sender,
    ) == 0
    assert json.loads(capsys.readouterr().out) == {"stateRevision": 7}
    assert calls[-1]["host_key"] == "host-secret-value"
    assert calls[-1]["inspect"] is True

    arguments = [
        "--initial-plan", "Sommer",
        "--history-valid-from", "2026-09-08",
        "--approval-reference", "approved-calendar-digest",
        "--request-id", "init-http-1",
        "--expected-state-revision", "7",
        "--confirmation", CONFIRMATION,
        "--function-admin-url", FUNCTION_ADMIN_URL,
    ]
    assert main(
        arguments, environment=environment, function_sender=sender
    ) == 0
    assert json.loads(capsys.readouterr().out) == {"initialized": True}
    assert calls[-1]["inspect"] is False
    assert calls[-1]["payload"] == _initialization_body(requestId="init-http-1")


def test_initializer_http_transport_has_no_redirect_or_retry_and_hides_key():
    from urllib.error import URLError
    from scripts.initialize_training_control import (
        FUNCTION_ADMIN_URL,
        _NoRedirects,
        _function_request as send,
    )

    calls = []

    class BrokenOpener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            raise URLError("offline")

    policies = []

    def opener_factory(policy):
        policies.append(policy)
        return BrokenOpener()

    secret = "do-not-print-host-key"
    with pytest.raises(RuntimeError, match="nicht erneut gesendet") as caught:
        send(
            url=FUNCTION_ADMIN_URL,
            host_key=secret,
            inspect=True,
            payload=None,
            opener_factory=opener_factory,
        )
    assert len(calls) == 1
    assert len(policies) == 1 and isinstance(policies[0], _NoRedirects)
    assert policies[0].redirect_request(None, None, 302, "", {}, "https://other") is None
    assert secret not in str(caught.value)


def test_initializer_http_rejects_wrong_url_missing_key_and_safe_http_error():
    from io import BytesIO
    from urllib.error import HTTPError
    from scripts.initialize_training_control import (
        FUNCTION_ADMIN_URL,
        _function_request as send,
    )

    with pytest.raises(ValueError, match="URL_NOT_ALLOWED"):
        send(
            url="https://example.test/api/training-control/initialize",
            host_key="secret",
            inspect=True,
            payload=None,
        )
    with pytest.raises(ValueError, match="HOST_KEY_REQUIRED"):
        send(
            url=FUNCTION_ADMIN_URL,
            host_key="",
            inspect=True,
            payload=None,
        )

    secret = "never-include-this"
    calls = []

    class ErrorOpener:
        def open(self, request, timeout):
            calls.append(request)
            raise HTTPError(
                FUNCTION_ADMIN_URL,
                409,
                "Conflict",
                {},
                BytesIO(b'{"code":"TRAINING_CONTROL_CHANGED"}'),
            )

    with pytest.raises(RuntimeError, match="HTTP 409.*TRAINING_CONTROL_CHANGED") as caught:
        send(
            url=FUNCTION_ADMIN_URL,
            host_key=secret,
            inspect=False,
            payload=_initialization_body(),
            opener_factory=lambda _policy: ErrorOpener(),
        )
    assert len(calls) == 1
    assert secret not in str(caught.value)
