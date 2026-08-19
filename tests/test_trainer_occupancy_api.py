from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import azure.functions as func

import function_app
from mower.planner import Block
from special_occupancy import (
    InMemorySpecialOccupancyStore,
    event_to_mower_block,
    merge_public_special_events,
)


FIXED_NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def request(body: dict) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="https://example.test/api/trainer-occupancies",
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        params={},
        body=json.dumps(body).encode("utf-8"),
    )


class TrainerOccupancyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemorySpecialOccupancyStore()
        self.store_patch = patch.object(
            function_app.AzureTableSpecialOccupancyStore,
            "from_environment",
            return_value=self.store,
        )
        self.clock_patch = patch.object(function_app, "datetime", wraps=datetime)
        self.env_patch = patch.dict(
            function_app.os.environ,
            {"SSV53_SPECIAL_OCCUPANCY_ENABLED": "true"},
        )
        self.store_patch.start()
        clock = self.clock_patch.start()
        clock.now.return_value = FIXED_NOW
        self.env_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.env_patch.stop)

    def payload(self, resource_id: str = "rasen") -> dict:
        return {
            "commandId": "trainer-create:test-001",
            "eventId": "trainer-test-001",
            "title": "Zusatztraining C-Junioren",
            "start": "2026-08-20T02:00:00+02:00",
            "end": "2026-08-20T03:30:00+02:00",
            "resourceId": resource_id,
            "area": "vorne & hinten",
            "description": "Trainerbelegung",
            "creator": {"id": "appack-42", "name": "Juliane Beispiel", "email": "juliane@example.de"},
            "confirmation": "TRAINER_BELEGUNG_SPEICHERN",
        }

    def test_rasen_is_saved_directly_and_gets_fixed_mower_buffers(self) -> None:
        payload = self.payload()
        payload["mowerBufferBeforeMinutes"] = 0
        payload["mowerBufferAfterMinutes"] = 0
        response = function_app.ssv53_trainer_occupancies(request(payload))
        self.assertEqual(response.status_code, 200)
        event = self.store.events["trainer-test-001"]
        block = event_to_mower_block(event, Block)
        self.assertEqual(block.start.isoformat(), "2026-08-20T01:30:00+02:00")
        self.assertEqual(block.end.isoformat(), "2026-08-20T04:00:00+02:00")
        self.assertFalse(event.suppress_training)
        self.assertEqual(event.creator_name, "Juliane Beispiel")

    def test_kunstrasen_is_visible_in_both_seasons_without_mower_block(self) -> None:
        response = function_app.ssv53_trainer_occupancies(
            request(self.payload("kunstrasen"))
        )
        self.assertEqual(response.status_code, 200)
        event = self.store.events["trainer-test-001"]
        for season in ("Sommer", "Winter"):
            merged = merge_public_special_events(
                {"season": season, "events": []},
                [event],
            )
            self.assertEqual(merged["events"][0]["resourceId"], "kunstrasen")
        self.assertIsNone(event_to_mower_block(event, Block))

    def test_wrong_confirmation_and_past_start_are_rejected(self) -> None:
        wrong = self.payload()
        wrong["confirmation"] = "JA"
        self.assertEqual(
            function_app.ssv53_trainer_occupancies(request(wrong)).status_code,
            400,
        )
        past = self.payload()
        past["start"] = "2026-08-18T13:00:00+02:00"
        past["end"] = "2026-08-18T14:00:00+02:00"
        self.assertEqual(
            function_app.ssv53_trainer_occupancies(request(past)).status_code,
            400,
        )
        self.assertEqual(self.store.events, {})

    def test_overlap_requires_explicit_second_confirmation(self) -> None:
        payload = self.payload("rasen")
        payload["start"] = "2026-08-20T17:30:00+02:00"
        payload["end"] = "2026-08-20T18:30:00+02:00"
        response = function_app.ssv53_trainer_occupancies(request(payload))
        body = json.loads(response.get_body())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["code"], "OCCUPANCY_CONFLICT")
        self.assertTrue(body["conflicts"])
        signatures = {
            (item["title"], item["start"], item["end"], item["source"])
            for item in body["conflicts"]
        }
        self.assertEqual(len(signatures), len(body["conflicts"]))
        self.assertEqual(self.store.events, {})

        payload["overlapConfirmation"] = "UEBERSCHNEIDUNG_TROTZDEM_SPEICHERN"
        response = function_app.ssv53_trainer_occupancies(request(payload))
        self.assertEqual(response.status_code, 200)
        self.assertIn("trainer-test-001", self.store.events)

    def test_creator_or_app_admin_can_delete_manual_entry(self) -> None:
        self.assertEqual(
            function_app.ssv53_trainer_occupancies(request(self.payload())).status_code,
            200,
        )
        delete = {
            "action": "delete",
            "commandId": "trainer-delete:test-001",
            "eventId": "one-off:trainer-test-001",
            "requesterId": "wrong-user",
            "confirmation": "TRAINER_BELEGUNG_LOESCHEN",
        }
        self.assertEqual(
            function_app.ssv53_trainer_occupancies(request(delete)).status_code,
            403,
        )
        delete["requesterId"] = "appack-42"
        self.assertEqual(
            function_app.ssv53_trainer_occupancies(request(delete)).status_code,
            200,
        )
        self.assertNotIn("trainer-test-001", self.store.events)

    def test_recurring_training_moves_atomically_to_other_pitch(self) -> None:
        payload = {
            "action": "move",
            "commandId": "trainer-move:test-a-thursday",
            "eventId": "training:sommer:som-ras-a-do:2026-08-20",
            "targetResourceId": "kunstrasen",
            "requesterId": "trainer-17",
            "creator": {"id": "trainer-17", "name": "Trainer Beispiel"},
            "confirmation": "TRAINER_BELEGUNG_VERSCHIEBEN",
        }
        response = function_app.ssv53_trainer_occupancies(request(payload))
        self.assertEqual(response.status_code, 200, response.get_body())
        event = next(iter(self.store.events.values()))
        self.assertEqual(event.resource_id, "kunstrasen")
        self.assertEqual(
            event.replaced_training_event_id,
            "training:sommer:som-ras-a-do:2026-08-20",
        )
        self.assertTrue(event.suppress_training)
        self.assertEqual(event.team, event.title)
        self.assertEqual(event.creator_name, "")
        self.assertEqual(event.moved_by_name, "Trainer Beispiel")
        public_event = event.to_public_event()
        self.assertEqual(public_event["team"], event.title)
        self.assertEqual(public_event["movedBy"]["name"], "Trainer Beispiel")
        self.assertIsNone(event_to_mower_block(event, Block))

        # Ein anderer Trainer darf einen verlegten regulären Termin wieder
        # auf den Rasen zurücklegen; dort entsteht sofort der Sicherheitsblock.
        payload.update({
            "commandId": "trainer-move:test-a-thursday-back",
            "eventId": "one-off:" + event.event_id,
            "targetResourceId": "rasen",
            "requesterId": "trainer-99",
            "creator": {"id": "trainer-99", "name": "Andere Trainerin"},
        })
        response = function_app.ssv53_trainer_occupancies(request(payload))
        self.assertEqual(response.status_code, 200, response.get_body())
        moved_back = self.store.events[event.event_id]
        self.assertEqual(moved_back.resource_id, "rasen")
        self.assertEqual(moved_back.team, event.team)
        self.assertEqual(moved_back.creator_name, "")
        self.assertEqual(moved_back.moved_by_name, "Andere Trainerin")
        self.assertIsNotNone(event_to_mower_block(moved_back, Block))

    def test_manual_entry_keeps_creator_and_records_mover_separately(self) -> None:
        self.assertEqual(
            function_app.ssv53_trainer_occupancies(request(self.payload())).status_code,
            200,
        )
        move = {
            "action": "move",
            "commandId": "trainer-move:manual-entry",
            "eventId": "one-off:trainer-test-001",
            "targetResourceId": "kunstrasen",
            "requesterId": "admin-7",
            "isAppAdministrator": True,
            "creator": {
                "id": "admin-7",
                "name": "App Administrator",
                "mobile": "+49 170 1234567",
            },
            "confirmation": "TRAINER_BELEGUNG_VERSCHIEBEN",
        }
        response = function_app.ssv53_trainer_occupancies(request(move))
        self.assertEqual(response.status_code, 200, response.get_body())
        moved = self.store.events["trainer-test-001"]
        self.assertEqual(moved.creator_name, "Juliane Beispiel")
        self.assertEqual(moved.creator_email, "juliane@example.de")
        self.assertEqual(moved.moved_by_name, "App Administrator")
        self.assertEqual(moved.moved_by_mobile, "+49 170 1234567")
        public_event = moved.to_public_event()
        self.assertEqual(public_event["creator"]["name"], "Juliane Beispiel")
        self.assertEqual(public_event["movedBy"]["name"], "App Administrator")

    def test_move_to_occupied_pitch_requires_same_second_confirmation(self) -> None:
        payload = {
            "action": "move",
            "commandId": "trainer-move:test-c-wednesday",
            "eventId": "training:sommer:som-ras-c-mi:2026-08-19",
            "targetResourceId": "kunstrasen",
            "requesterId": "trainer-17",
            "creator": {"id": "trainer-17", "name": "Trainer Beispiel"},
            "confirmation": "TRAINER_BELEGUNG_VERSCHIEBEN",
        }
        response = function_app.ssv53_trainer_occupancies(request(payload))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.get_body())["code"], "OCCUPANCY_CONFLICT")
        self.assertEqual(self.store.events, {})
        payload["overlapConfirmation"] = "UEBERSCHNEIDUNG_TROTZDEM_SPEICHERN"
        response = function_app.ssv53_trainer_occupancies(request(payload))
        self.assertEqual(response.status_code, 200, response.get_body())
        self.assertEqual(next(iter(self.store.events.values())).resource_id, "kunstrasen")


    def test_oversized_optional_profile_image_never_blocks_creation(self) -> None:
        payload = self.payload()
        payload["creator"]["image"] = "data:image/jpeg;base64," + ("A" * 12000)
        response = function_app.ssv53_trainer_occupancies(request(payload))
        self.assertEqual(response.status_code, 200)
        event = self.store.events["trainer-test-001"]
        self.assertTrue(event.creator_image.startswith("data:image/jpeg;base64,"))
        self.assertEqual(event.creator_name, "Juliane Beispiel")
        self.assertEqual(event.creator_email, "juliane@example.de")

if __name__ == "__main__":
    unittest.main()
