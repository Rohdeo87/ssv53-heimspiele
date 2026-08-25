from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import function_app
from order_mail import OrderMailStore, _recipient_hash
from occupancy_notifications import (
    AzureOccupancyNotificationStore,
    DELIVERY_PARTITION,
)
from platzwart_console import ConsoleTableStore
from special_occupancy import (
    AzureTableSpecialOccupancyStore,
    SpecialOccupancyEvent,
)
from training_cancellations import AzureTableCancellationStore


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class FakeTableClient:
    def __init__(self, entities=None):
        self.entities = {
            (str(item["PartitionKey"]), str(item["RowKey"])): item
            for item in (entities or [])
        }
        self.deleted = []
        self.updated = []
        self.delete_options = []
        self.update_options = []

    def query_entities(self, **_kwargs):
        return list(self.entities.values())

    def delete_entity(self, *, partition_key, row_key, **kwargs):
        self.deleted.append((partition_key, row_key))
        self.delete_options.append(kwargs)
        self.entities.pop((partition_key, row_key), None)

    def update_entity(self, *, entity, **kwargs):
        value = dict(entity)
        key = (str(value["PartitionKey"]), str(value["RowKey"]))
        self.entities[key] = value
        self.updated.append(value)
        self.update_options.append(kwargs)

    def create_entity(self, *, entity):
        value = dict(entity)
        key = (str(value["PartitionKey"]), str(value["RowKey"]))
        self.entities[key] = value


class VersionedEntity(dict):
    def __init__(self, value, etag="etag-1"):
        super().__init__(value)
        self.metadata = {"etag": etag}


def special_entity(
    event_id: str,
    start: str,
    end: str,
) -> dict:
    event = SpecialOccupancyEvent.from_command(
        {
            "id": event_id,
            "title": "Training",
            "start": start,
            "end": end,
            "resourceId": "rasen",
            "creator": {
                "id": "profile-17",
                "name": "Trainer Beispiel",
                "role": "Trainer",
            },
        },
        now_utc=NOW,
    )
    return event.to_entity()


class PrivacyRetentionTests(unittest.TestCase):
    def test_retention_environment_may_only_shorten_published_maximum(self):
        self.assertEqual(
            function_app._retention_days(
                "RETENTION", 90, {"RETENTION": "30"}
            ),
            30,
        )
        self.assertEqual(
            function_app._retention_days(
                "RETENTION", 90, {"RETENTION": "91"}
            ),
            90,
        )

    def test_legacy_contact_fields_are_never_returned_publicly(self):
        legacy = special_entity(
            "legacy-public-event",
            "2026-08-26T18:00:00+02:00",
            "2026-08-26T19:30:00+02:00",
        )
        legacy.update(
            {
                "CreatorEmail": "creator@example.invalid",
                "CreatorPhone": "+49 000 111111",
                "CreatorImage": "data:image/jpeg;base64,private",
                "MovedByEmail": "mover@example.invalid",
                "MovedByChatId": "private-chat-id",
            }
        )

        public = SpecialOccupancyEvent.from_entity(legacy).to_public_event()

        self.assertEqual(
            set(public["creator"]),
            {"id", "name", "role", "contactRef"},
        )
        self.assertEqual(
            set(public["movedBy"]),
            {"id", "name", "role", "contactRef"},
        )
        serialized = str(public)
        self.assertNotIn("creator@example.invalid", serialized)
        self.assertNotIn("mover@example.invalid", serialized)
        self.assertNotIn("private-chat-id", serialized)

    def test_special_cleanup_scrubs_current_legacy_contact_but_keeps_block(self):
        current = special_entity(
            "current-event",
            "2026-08-26T18:00:00+02:00",
            "2026-08-26T19:30:00+02:00",
        )
        current.update(
            {
                "CreatorEmail": "trainer@example.invalid",
                "CreatorPhone": "+49 000 000000",
                "CreatorImage": "data:image/jpeg;base64,private",
                "CreatorInfoHtml": "<b>privat</b>",
            }
        )
        current["FutureSchemaField"] = "must-stay"
        client = FakeTableClient([VersionedEntity(current)])

        result = AzureTableSpecialOccupancyStore(client).cleanup_retention(
            now_utc=NOW
        )

        self.assertEqual(result, {"deleted": 0, "scrubbed": 1, "skipped": 0})
        saved = next(iter(client.entities.values()))
        self.assertTrue(saved["Active"])
        self.assertEqual(saved["EventId"], "current-event")
        self.assertEqual(saved["CreatorId"], "profile-17")
        self.assertEqual(saved["CreatorName"], "Trainer Beispiel")
        self.assertEqual(saved["FutureSchemaField"], "must-stay")
        self.assertNotIn("CreatorEmail", saved)
        self.assertNotIn("CreatorPhone", saved)
        self.assertNotIn("CreatorImage", saved)
        self.assertNotIn("CreatorInfoHtml", saved)
        self.assertEqual(client.update_options[0]["etag"], "etag-1")
        self.assertIn("match_condition", client.update_options[0])

    def test_special_cleanup_deletes_only_unambiguously_expired_rows(self):
        expired = special_entity(
            "expired-event",
            "2025-12-01T18:00:00+01:00",
            "2025-12-01T19:00:00+01:00",
        )
        malformed = {
            "PartitionKey": "special-occupancy",
            "RowKey": "event-malformed",
            "Kind": "event",
            "Active": True,
        }
        client = FakeTableClient([VersionedEntity(expired), malformed])

        result = AzureTableSpecialOccupancyStore(client).cleanup_retention(
            now_utc=NOW,
            event_retention_days=90,
        )

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertIn(
            ("special-occupancy", "event-malformed"),
            client.entities,
        )

    def test_special_cleanup_scrubs_pii_even_when_event_is_malformed(self):
        malformed = VersionedEntity(
            {
                "PartitionKey": "special-occupancy",
                "RowKey": "event-malformed-private",
                "Kind": "event",
                "Active": True,
                "CreatorEmail": "private@example.invalid",
                "MovedByChatId": "private-chat",
                "FutureSchemaField": "must-stay",
            }
        )
        client = FakeTableClient([malformed])

        result = AzureTableSpecialOccupancyStore(client).cleanup_retention(
            now_utc=NOW
        )

        self.assertEqual(result, {"deleted": 0, "scrubbed": 1, "skipped": 0})
        saved = next(iter(client.entities.values()))
        self.assertNotIn("CreatorEmail", saved)
        self.assertNotIn("MovedByChatId", saved)
        self.assertEqual(saved["FutureSchemaField"], "must-stay")

    def test_training_cleanup_keeps_future_cancellation_and_drops_old_history(self):
        client = FakeTableClient(
            [
                VersionedEntity({
                    "PartitionKey": "training-cancellations",
                    "RowKey": "current-old",
                    "Kind": "current",
                    "Day": "2026-01-01",
                }),
                {
                    "PartitionKey": "training-cancellations",
                    "RowKey": "current-future",
                    "Kind": "current",
                    "Day": "2026-08-30",
                },
            ]
        )

        result = AzureTableCancellationStore(client).cleanup_retention(
            now_utc=NOW
        )

        self.assertEqual(result["deleted"], 1)
        self.assertIn(
            ("training-cancellations", "current-future"),
            client.entities,
        )

    def test_order_store_never_persists_plain_recipient_and_scrubs_legacy(self):
        client = FakeTableClient()
        store = OrderMailStore(client)
        self.assertEqual(
            store.claim(order_id="SSV53-260825-120000-AB12", recipient="User@Example.de"),
            "claimed",
        )
        created = next(iter(client.entities.values()))
        self.assertNotIn("recipient", created)
        self.assertEqual(created["recipientHash"], _recipient_hash("user@example.de"))

        legacy = {
            "PartitionKey": "ssv53-order-ready-mail-v1",
            "RowKey": "SSV53-260825-120000-CD34",
            "status": "sent",
            "recipient": "legacy@example.de",
            "updatedAtUtc": "2026-08-24T12:00:00+00:00",
        }
        client = FakeTableClient([VersionedEntity(legacy)])
        result = OrderMailStore(client).cleanup_retention(now_utc=NOW)
        saved = next(iter(client.entities.values()))
        self.assertEqual(result["scrubbed"], 1)
        self.assertNotIn("recipient", saved)
        self.assertEqual(saved["recipientHash"], _recipient_hash("legacy@example.de"))

    def test_order_store_scrubs_plain_recipient_without_valid_timestamp(self):
        legacy = VersionedEntity(
            {
                "PartitionKey": "ssv53-order-ready-mail-v1",
                "RowKey": "SSV53-LEGACY-WITHOUT-TIME",
                "status": "failed",
                "recipient": "legacy@example.de",
                "FutureSchemaField": "must-stay",
            }
        )
        client = FakeTableClient([legacy])

        result = OrderMailStore(client).cleanup_retention(now_utc=NOW)

        self.assertEqual(result, {"deleted": 0, "scrubbed": 1, "skipped": 0})
        saved = next(iter(client.entities.values()))
        self.assertNotIn("recipient", saved)
        self.assertEqual(saved["recipientHash"], _recipient_hash("legacy@example.de"))
        self.assertEqual(saved["FutureSchemaField"], "must-stay")

    def test_console_cleanup_never_removes_device_or_activation_records(self):
        client = FakeTableClient(
            [
                VersionedEntity({
                    "PartitionKey": "ssv53-platzwart",
                    "RowKey": "loginfail-old",
                    "failure_utc": "2026-08-01T12:00:00+00:00",
                }),
                {
                    "PartitionKey": "ssv53-platzwart",
                    "RowKey": "device-essential",
                    "created_utc": "2025-01-01T00:00:00+00:00",
                },
                {
                    "PartitionKey": "ssv53-platzwart",
                    "RowKey": "activation-essential",
                    "used_utc": "2025-01-01T00:00:00+00:00",
                },
            ]
        )

        result = ConsoleTableStore(client).cleanup_retention(now_utc=NOW)

        self.assertEqual(result["deleted"], 1)
        self.assertIn(("ssv53-platzwart", "device-essential"), client.entities)
        self.assertIn(("ssv53-platzwart", "activation-essential"), client.entities)

    def test_collision_delivery_cleanup_keeps_current_and_skips_unclear_claims(self):
        client = FakeTableClient(
            [
                VersionedEntity(
                    {
                        "PartitionKey": DELIVERY_PARTITION,
                        "RowKey": "expired",
                        "Status": "sent",
                        "UpdatedAtUtc": "2026-01-01T12:00:00+00:00",
                    }
                ),
                {
                    "PartitionKey": DELIVERY_PARTITION,
                    "RowKey": "current",
                    "Status": "sent",
                    "UpdatedAtUtc": "2026-08-24T12:00:00+00:00",
                },
                {
                    "PartitionKey": DELIVERY_PARTITION,
                    "RowKey": "unclear",
                    "Status": "failed",
                },
            ]
        )

        result = AzureOccupancyNotificationStore(client).cleanup_retention(
            now_utc=NOW,
            retention_days=180,
        )

        self.assertEqual(result, {"deleted": 1, "skipped": 1})
        self.assertIn((DELIVERY_PARTITION, "current"), client.entities)
        self.assertIn((DELIVERY_PARTITION, "unclear"), client.entities)
        self.assertEqual(client.delete_options[0]["etag"], "etag-1")
        self.assertIn("match_condition", client.delete_options[0])

    def test_all_retention_mutations_fail_closed_without_etag(self):
        special = special_entity(
            "expired-without-etag",
            "2025-12-01T18:00:00+01:00",
            "2025-12-01T19:00:00+01:00",
        )
        special_client = FakeTableClient([special])
        special_result = AzureTableSpecialOccupancyStore(
            special_client
        ).cleanup_retention(now_utc=NOW)
        self.assertEqual(special_result["deleted"], 0)
        self.assertEqual(special_result["skipped"], 1)

        cancellation_client = FakeTableClient(
            [{
                "PartitionKey": "training-cancellations",
                "RowKey": "old-without-etag",
                "Kind": "current",
                "Day": "2026-01-01",
            }]
        )
        cancellation_result = AzureTableCancellationStore(
            cancellation_client
        ).cleanup_retention(now_utc=NOW)
        self.assertEqual(cancellation_result["deleted"], 0)
        self.assertEqual(cancellation_result["skipped"], 1)

        console_client = FakeTableClient(
            [{
                "PartitionKey": "ssv53-platzwart",
                "RowKey": "loginfail-without-etag",
                "failure_utc": "2026-01-01T12:00:00+00:00",
            }]
        )
        console_result = ConsoleTableStore(console_client).cleanup_retention(
            now_utc=NOW
        )
        self.assertEqual(console_result["deleted"], 0)
        self.assertEqual(console_result["skipped"], 1)

        order_client = FakeTableClient(
            [{
                "PartitionKey": "ssv53-order-ready-mail-v1",
                "RowKey": "order-without-etag",
                "status": "sent",
                "recipient": "private@example.invalid",
                "updatedAtUtc": "2026-01-01T12:00:00+00:00",
            }]
        )
        order_result = OrderMailStore(order_client).cleanup_retention(now_utc=NOW)
        self.assertEqual(order_result["deleted"], 0)
        self.assertEqual(order_result["scrubbed"], 0)
        self.assertEqual(order_result["skipped"], 1)

        collision_client = FakeTableClient(
            [{
                "PartitionKey": DELIVERY_PARTITION,
                "RowKey": "collision-without-etag",
                "Status": "sent",
                "UpdatedAtUtc": "2026-01-01T12:00:00+00:00",
            }]
        )
        collision_result = AzureOccupancyNotificationStore(
            collision_client
        ).cleanup_retention(now_utc=NOW)
        self.assertEqual(collision_result["deleted"], 0)
        self.assertEqual(collision_result["skipped"], 1)

    def test_retention_runner_isolates_one_unavailable_store(self):
        class Store:
            def __init__(self, result=None, error=None):
                self.result = result or {"deleted": 0}
                self.error = error

            def cleanup_retention(self, **_kwargs):
                if self.error:
                    raise self.error
                return self.result

        healthy = Store({"deleted": 2})
        failing = Store(error=RuntimeError("unavailable"))
        environment = {
            "SSV53_STORAGE_ACCOUNT_URL": "https://example.invalid",
            "SSV53_STATE_TABLE_NAME": "state",
            "AzureWebJobsStorage__clientId": "client",
        }
        with patch.object(
            function_app.AzureTableSpecialOccupancyStore,
            "from_environment",
            return_value=healthy,
        ), patch.object(
            function_app.AzureTableCancellationStore,
            "from_environment",
            return_value=failing,
        ), patch.object(
            function_app.ConsoleTableStore,
            "from_environment",
            return_value=healthy,
        ), patch.object(
            function_app.AzureOccupancyNotificationStore,
            "from_environment",
            return_value=healthy,
        ), patch.object(
            function_app.OrderMailStore,
            "from_environment",
            return_value=healthy,
        ):
            result = function_app.run_privacy_retention(NOW, environment)

        self.assertTrue(result["specialOccupancy"]["available"])
        self.assertFalse(result["trainingCancellations"]["available"])
        self.assertTrue(result["platzwartConsole"]["available"])
        self.assertTrue(result["occupancyNotifications"]["available"])
        self.assertTrue(result["orderMail"]["available"])
        self.assertEqual(
            result["trainingCancellations"]["errorType"],
            "RuntimeError",
        )

    def test_retention_timer_reports_one_warning_without_retry_or_exception(self):
        partial = {
            "specialOccupancy": {"available": True, "deleted": 0},
            "trainingCancellations": {
                "available": False,
                "errorType": "RuntimeError",
            },
        }
        with patch.object(
            function_app,
            "run_privacy_retention",
            return_value=partial,
        ), patch.object(function_app.LOGGER, "warning") as warning, patch.object(
            function_app.LOGGER,
            "info",
        ) as info:
            function_app.ssv53_privacy_retention_timer(None)

        warning.assert_called_once_with(
            "SSV53_PRIVACY_RETENTION_PARTIAL unavailable=%s",
            "trainingCancellations",
        )
        info.assert_not_called()

    def test_retention_timer_runs_outside_the_irrigation_window(self):
        with open(function_app.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertIn('schedule="0 23 11 * * *"', source)
        retention_section = source.split("def ssv53_privacy_retention_timer", 1)[0]
        retention_decorators = retention_section.rsplit("@app.timer_trigger", 1)[-1]
        self.assertNotIn("@app.retry", retention_decorators)


if __name__ == "__main__":
    unittest.main()
