from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import azure.functions as func

import function_app
from training_cancellations import InMemoryCancellationStore


FIXED_NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def request(
    method: str,
    *,
    params=None,
    body=None,
    content_type: str = "application/json",
) -> func.HttpRequest:
    return func.HttpRequest(
        method=method,
        url="https://example.test/api/training-cancellations",
        headers={"Content-Type": content_type},
        params=params or {},
        body=(json.dumps(body).encode("utf-8") if body is not None else b""),
    )


class TrainingCancellationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryCancellationStore()
        self.store_patch = patch.object(
            function_app.AzureTableCancellationStore,
            "from_environment",
            return_value=self.store,
        )
        self.clock_patch = patch.object(function_app, "datetime", wraps=datetime)
        self.store_patch.start()
        clock = self.clock_patch.start()
        clock.now.return_value = FIXED_NOW
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.clock_patch.stop)

    def test_get_lists_only_training_occurrences(self) -> None:
        response = function_app.ssv53_training_cancellations(
            request("GET", params={"start": "2026-08-13", "end": "2026-08-20"})
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.get_body())
        self.assertTrue(payload["items"])
        self.assertTrue(all(item["source"] == "training" for item in payload["items"]))

    def test_cancel_then_restore_exact_occurrence(self) -> None:
        listing = function_app.ssv53_training_cancellations(
            request("GET", params={"start": "2026-08-13", "end": "2026-08-20"})
        )
        event_id = json.loads(listing.get_body())["items"][0]["id"]
        cancelled = function_app.ssv53_training_cancellations(
            request(
                "POST",
                body={
                    "action": "cancel",
                    "eventId": event_id,
                    "confirmation": "TRAINING_FAELLT_AUS",
                },
            )
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(
            json.loads(cancelled.get_body())["mowerReleaseNotBeforeUtc"],
            "2026-08-13T10:30:00+00:00",
        )
        restored = function_app.ssv53_training_cancellations(
            request(
                "POST",
                body={
                    "action": "restore",
                    "eventId": event_id,
                    "confirmation": "TRAINING_WIEDER_AKTIV",
                },
            )
        )
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(json.loads(restored.get_body())["restored"])

    def test_match_or_forged_id_cannot_be_cancelled(self) -> None:
        response = function_app.ssv53_training_cancellations(
            request(
                "POST",
                body={
                    "action": "cancel",
                    "eventId": "match:123",
                    "confirmation": "TRAINING_FAELLT_AUS",
                },
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.items, {})

    def test_wrong_confirmation_cannot_change_anything(self) -> None:
        listing = function_app.ssv53_training_cancellations(request("GET"))
        event_id = json.loads(listing.get_body())["items"][0]["id"]
        response = function_app.ssv53_training_cancellations(
            request(
                "POST",
                body={"action": "cancel", "eventId": event_id, "confirmation": "ja"},
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.items, {})

    def test_text_plain_simple_request_is_parsed_without_cors_preflight(self) -> None:
        listing = function_app.ssv53_training_cancellations(request("GET"))
        event_id = json.loads(listing.get_body())["items"][0]["id"]
        response = function_app.ssv53_training_cancellations(
            request(
                "POST",
                content_type="text/plain;charset=UTF-8",
                body={
                    "action": "cancel",
                    "eventId": event_id,
                    "confirmation": "TRAINING_FAELLT_AUS",
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.get_body())["cancelled"])


if __name__ == "__main__":
    unittest.main()
