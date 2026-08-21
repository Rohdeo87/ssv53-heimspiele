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
            },
        },
    )


def test_observation_entity_is_idempotent_per_minute():
    entity = observation_entity(cycle())
    assert entity["PartitionKey"] == "ssv53-irrigation-20260821"
    assert entity["RowKey"] == "20260821T0415Z"
    assert entity["active_relay_ids"] == "[22]"
    assert entity["completed_relay_ids"] == "[11]"


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
