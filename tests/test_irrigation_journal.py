from datetime import datetime, timezone

from mower.irrigation_journal import (
    observation_entity,
    read_irrigation_observations,
    record_irrigation_observation,
)
from mower.runtime import CycleResult


def cycle() -> CycleResult:
    return CycleResult(
        schema_version=2,
        executed_at_utc="2026-08-21T04:15:42+00:00",
        source="test",
        control_mode="FULL_FAILSAFE",
        past_due=False,
        decision_code="IRRIGATION_ZONE_RUNNING",
        command_sent=False,
        message="läuft",
        details={
            "hydrawise": {"safety": {"available": True, "fresh": True, "active_relay_ids": [22]}},
            "automation_state": {
                "irrigation_plan_id": "plan-1",
                "irrigation_phase": "RUNNING",
                "irrigation_completed_relay_ids": [11],
                "park_command_sent_utc": "2026-08-21T04:10:00+00:00",
                "park_confirmed_utc": "2026-08-21T04:12:00+00:00",
                "park_confirmed_observations": 2,
            },
            "weather": {
                "enabled": True,
                "available": True,
                "fresh": True,
                "provider": "OPEN_METEO",
            },
            "adaptive_planning": {
                "plan_id": "adaptive-1",
                "status": "SHADOW_PLAN_READY",
                "water_recommendation": "KEEP_BASELINE",
                "selected": {
                    "irrigation_start_utc": "2026-08-21T04:30:00+00:00",
                    "irrigation_end_utc": "2026-08-21T07:10:00+00:00",
                    "earliest_mow_resume_utc": "2026-08-21T09:40:00+00:00",
                    "lost_dry_mowing_minutes": 310,
                    "expected_rain_mm": 0.0,
                },
            },
        },
    )


def test_observation_entity_is_idempotent_per_minute():
    entity = observation_entity(cycle())
    assert entity["PartitionKey"] == "ssv53-irrigation-20260821"
    assert entity["RowKey"] == "20260821T0415Z"
    assert entity["active_relay_ids"] == "[22]"
    assert entity["completed_relay_ids"] == "[11]"
    assert entity["park_confirmed_observations"] == 2
    assert entity["weather_provider"] == "OPEN_METEO"
    assert entity["adaptive_plan_id"] == "adaptive-1"
    assert entity["adaptive_earliest_mow_resume_utc"] == "2026-08-21T09:40:00+00:00"
    assert entity["schema_version"] == 2


def test_record_replaces_same_minute_without_append_duplicates():
    class Client:
        def __init__(self):
            self.calls = []

        def upsert_entity(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    record_irrigation_observation(cycle(), {}, table_client=client)
    assert len(client.calls) == 1
    assert client.calls[0]["entity"]["RowKey"] == "20260821T0415Z"
    assert client.calls[0]["timeout"] == 5


def test_read_filters_period_and_restores_statistics_shape():
    class Client:
        def query_entities(self, **kwargs):
            return [
                {
                    "observed_utc": "2026-08-21T04:15:42+00:00",
                    "decision_code": "IRRIGATION_ZONE_RUNNING",
                    "active_relay_ids": "[22]",
                    "irrigation_plan_id": "plan-1",
                    "irrigation_phase": "RUNNING",
                    "completed_relay_ids": "[11]",
                }
            ]

    rows = read_irrigation_observations(
        {},
        datetime(2026, 8, 21, 4, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 21, 5, 0, tzinfo=timezone.utc),
        table_client=Client(),
    )
    assert len(rows) == 1
    assert rows[0]["journal_source"] is True
    assert rows[0]["active_relay_ids"] == "[22]"
