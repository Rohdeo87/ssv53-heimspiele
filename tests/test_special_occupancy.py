from __future__ import annotations

import unittest
from datetime import datetime, timezone

from mower.full_failsafe import PARK_GUARD_BLOCK_SOURCES, SAFE_PARK_SOURCES
from mower.full_mower import SAFE_AUTOSTART_PARK_SOURCES
from mower.planner import Block
from special_occupancy import (
    COMMAND_MARKER,
    InMemorySpecialOccupancyStore,
    SpecialOccupancyError,
    encode_command,
    event_to_mower_block,
    merge_public_special_events,
    parse_admin_request,
    relocated_training_occurrence_keys,
)


class SpecialOccupancyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 18, 17, 30, tzinfo=timezone.utc)
        self.store = InMemorySpecialOccupancyStore()

    def _command(
        self,
        *,
        command_id: str = "chatgpt-test-0001",
        event_id: str = "test-sperre",
        resource_id: str = "kunstrasen",
        start: str = "2026-12-18T00:00:00+01:00",
        end: str = "2026-12-19T00:00:00+01:00",
    ) -> dict:
        return {
            "commandId": command_id,
            "action": "upsert",
            "event": {
                "id": event_id,
                "title": "Test – Platz gesperrt",
                "start": start,
                "end": end,
                "resourceId": resource_id,
                "area": "vorne & hinten",
                "description": "Test",
                "suppressTraining": True,
            },
        }

    def test_idempotent_upsert_and_conflicting_command_id(self) -> None:
        command = self._command()
        first = self.store.apply(command, now_utc=self.now)
        second = self.store.apply(command, now_utc=self.now)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        changed = self._command()
        changed["event"]["title"] = "Anderer Inhalt"
        with self.assertRaisesRegex(SpecialOccupancyError, "anderem Inhalt"):
            self.store.apply(changed, now_utc=self.now)

    def test_mail_transport_decodes_command_and_rejects_wrong_sender(self) -> None:
        command = self._command()
        encoded = encode_command(command)
        body = {
            "mailSubject": "[SSV53-BELEGUNG] Test",
            "mailFrom": "thomas_rohde@outlook.de",
            "mailBody": COMMAND_MARKER + encoded,
        }
        decoded = parse_admin_request(
            body,
            allowed_sender="thomas_rohde@outlook.de",
        )
        self.assertEqual(decoded, command)

        body["mailFrom"] = "fremd@example.org"
        with self.assertRaisesRegex(SpecialOccupancyError, "nicht freigegeben"):
            parse_admin_request(
                body,
                allowed_sender="thomas_rohde@outlook.de",
            )

    def test_dynamic_special_replaces_static_and_suppresses_training(self) -> None:
        command = self._command()
        self.store.apply(command, now_utc=self.now)
        special = self.store.events["test-sperre"]
        payload = {
            "events": [
                {
                    "id": "one-off:test-sperre",
                    "title": "Alte statische Sperre",
                    "start": "2026-12-18T00:00:00+01:00",
                    "end": "2026-12-19T00:00:00+01:00",
                    "resourceId": "kunstrasen",
                    "source": "special",
                    "area": "vorne & hinten",
                },
                {
                    "id": "training:winter:e2:2026-12-18",
                    "title": "E2",
                    "start": "2026-12-18T15:30:00+01:00",
                    "end": "2026-12-18T17:00:00+01:00",
                    "resourceId": "kunstrasen",
                    "source": "training",
                    "area": "vorne & hinten",
                },
                {
                    "id": "training:winter:e1:2026-12-18",
                    "title": "E1",
                    "start": "2026-12-18T15:30:00+01:00",
                    "end": "2026-12-18T17:00:00+01:00",
                    "resourceId": "rasen",
                    "source": "training",
                    "area": "vorne & hinten",
                },
            ]
        }
        merged = merge_public_special_events(payload, [special])
        ids = [item["id"] for item in merged["events"]]
        self.assertEqual(ids.count("one-off:test-sperre"), 1)
        self.assertNotIn("training:winter:e2:2026-12-18", ids)
        self.assertIn("training:winter:e1:2026-12-18", ids)

    def test_rasen_special_becomes_mower_block_with_buffer(self) -> None:
        command = self._command(
            event_id="rasen-event",
            resource_id="rasen",
            start="2026-09-01T18:00:00+02:00",
            end="2026-09-01T20:00:00+02:00",
        )
        self.store.apply(command, now_utc=self.now)
        event = self.store.events["rasen-event"]
        block = event_to_mower_block(event, Block)
        self.assertIsNotNone(block)
        self.assertEqual(block.source, "special")
        self.assertEqual(block.start.isoformat(), "2026-09-01T17:30:00+02:00")
        self.assertEqual(block.end.isoformat(), "2026-09-01T20:30:00+02:00")

    def test_kunstrasen_special_never_creates_mower_block(self) -> None:
        command = self._command()
        self.store.apply(command, now_utc=self.now)
        self.assertIsNone(
            event_to_mower_block(self.store.events["test-sperre"], Block)
        )

    def test_relocation_suppresses_only_linked_training_across_places(self) -> None:
        command = self._command(
            event_id="trainer-move-c",
            resource_id="kunstrasen",
            start="2026-08-19T17:30:00+02:00",
            end="2026-08-19T19:00:00+02:00",
        )
        command["event"]["replacesTrainingEventId"] = (
            "training:sommer:som-ras-c-mi:2026-08-19"
        )
        command["event"]["team"] = "C-Junioren"
        command["event"]["movedBy"] = {
            "id": "trainer-17",
            "name": "Trainer Beispiel",
            "email": "trainer@example.de",
        }
        self.store.apply(command, now_utc=self.now)
        special = self.store.events["trainer-move-c"]
        payload = {"events": [
            {
                "id": "training:sommer:som-ras-c-mi:2026-08-19",
                "source": "training",
                "resourceId": "rasen",
                "start": "2026-08-19T17:30:00+02:00",
                "end": "2026-08-19T19:00:00+02:00",
            },
            {
                "id": "training:sommer:som-ras-c-fr:2026-08-21",
                "source": "training",
                "resourceId": "rasen",
                "start": "2026-08-21T17:00:00+02:00",
                "end": "2026-08-21T18:30:00+02:00",
            },
        ]}
        merged = merge_public_special_events(payload, [special])
        ids = {item["id"] for item in merged["events"]}
        self.assertNotIn("training:sommer:som-ras-c-mi:2026-08-19", ids)
        self.assertIn("training:sommer:som-ras-c-fr:2026-08-21", ids)
        self.assertIn("one-off:trainer-move-c", ids)
        public_event = special.to_public_event()
        self.assertEqual(public_event["team"], "C-Junioren")
        self.assertEqual(public_event["movedBy"]["name"], "Trainer Beispiel")
        restored = type(special).from_entity(special.to_entity())
        self.assertEqual(restored.team, "C-Junioren")
        self.assertEqual(restored.moved_by_email, "trainer@example.de")
        self.assertEqual(
            relocated_training_occurrence_keys([special]),
            {("som-ras-c-mi", "2026-08-19")},
        )

    def test_special_is_a_safe_park_and_restart_source(self) -> None:
        self.assertIn("special", SAFE_PARK_SOURCES)
        self.assertIn("special", PARK_GUARD_BLOCK_SOURCES)
        self.assertIn("special", SAFE_AUTOSTART_PARK_SOURCES)


if __name__ == "__main__":
    unittest.main()
