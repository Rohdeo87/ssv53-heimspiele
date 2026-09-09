from datetime import datetime, timezone

from mower.irrigation_operating_window import validate_fresh_start, validate_zone_sequence


UTC = timezone.utc
def u(hour, minute=0, day=9):
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def test_160_minutes_at_0330_and_0430_fit_without_pause_change():
    first = validate_zone_sequence([u(1, 30)], [160 * 60])
    second = validate_zone_sequence([u(2, 30)], [160 * 60])
    assert first.code == "OK" and first.projected_end_utc == u(4, 10)
    assert second.code == "OK" and second.projected_end_utc == u(5, 10)


def test_latest_start_is_0520_for_160_minutes():
    result = validate_zone_sequence([u(3, 20)], [160 * 60])
    assert result.code == "OK"
    assert result.latest_start_utc == u(3, 20)


def test_pause_and_duration_are_preserved_and_late_sequence_rejected():
    starts = [u(1, 30), u(2, 30), u(3, 30), u(4, 30), u(5, 30), u(6, 30), u(7, 30)]
    result = validate_zone_sequence(starts, [40 * 60] * 7, [20 * 60] * 6)
    assert result.code == "TOO_LATE"
    assert "08:00" in result.reason
    shortened = validate_zone_sequence([u(1, 30), u(2, 0)], [40 * 60, 40 * 60], [20 * 60])
    assert shortened.code == "INVALID"


def test_actual_absolute_gaps_drive_latest_start_and_deadline():
    starts = [u(1, 30), u(2, 30), u(3, 30), u(4, 30), u(5, 30), u(6, 30), u(7, 30)]
    result = validate_zone_sequence(starts, [40 * 60] + [20 * 60] * 6, [20 * 60] * 6)
    assert result.code == "TOO_LATE"
    # Omitting minimum pauses does not erase the absolute gaps in the plan.
    result = validate_zone_sequence(starts, [20 * 60] * 7)
    assert result.latest_start_utc == u(23, 40, day=8)


def test_early_late_overnight_and_malformed_values_fail_closed():
    assert validate_zone_sequence([u(1, 29)], [60 * 60]).code == "TOO_EARLY"
    assert validate_zone_sequence([u(6, 1)], [120 * 60]).code == "TOO_LATE"
    assert validate_zone_sequence([u(21, 30), u(22, 30)], [60, 60], [0]).code == "INVALID"
    assert validate_zone_sequence([u(1, 30)], [True]).code == "INVALID"
    assert validate_zone_sequence([u(1, 30).replace(tzinfo=None)], [60]).code == "INVALID"


def test_fresh_start_and_dst_transition_dates_use_berlin_boundaries():
    assert validate_fresh_start(u(1, 30), duration_seconds=160 * 60).code == "OK"
    assert validate_fresh_start(u(1, 29), duration_seconds=160 * 60).code == "TOO_EARLY"
    # Both dates exercise ZoneInfo's CET/CEST conversion without changing the rule.
    dst_start = datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    dst_end = datetime(2026, 10, 25, 2, 30, tzinfo=UTC)
    dst_start_2027 = datetime(2027, 3, 28, 1, 30, tzinfo=UTC)
    dst_end_2027 = datetime(2027, 10, 31, 2, 30, tzinfo=UTC)
    assert validate_fresh_start(dst_start, duration_seconds=60 * 60).code == "OK"
    assert validate_fresh_start(dst_end, duration_seconds=60 * 60).code == "OK"
    assert validate_fresh_start(dst_start_2027, duration_seconds=60 * 60).code == "OK"
    assert validate_fresh_start(dst_end_2027, duration_seconds=60 * 60).code == "OK"


def test_malformed_sequence_container_fails_closed():
    assert validate_zone_sequence(None, [60]).code == "INVALID"
    assert validate_zone_sequence([u(1, 30)], None).code == "INVALID"
    assert validate_zone_sequence([u(1, 30)], [60], None).code == "INVALID"


def test_overflowing_duration_margin_and_datetime_fail_closed():
    huge = 10**100
    assert validate_zone_sequence([u(1, 30)], [huge]).code == "INVALID"
    assert validate_zone_sequence([u(1, 30)], [60], validation_margin_seconds=huge).code == "INVALID"
    assert validate_zone_sequence([datetime.max.replace(tzinfo=UTC)], [60]).code == "INVALID"
    assert validate_fresh_start(datetime.max.replace(tzinfo=UTC), duration_seconds=huge).code == "INVALID"
    assert validate_zone_sequence({"start": u(1, 30)}, {"duration": 60}).code == "INVALID"
