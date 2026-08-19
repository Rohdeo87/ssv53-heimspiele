from __future__ import annotations

import json
import hashlib
import re
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import azure.functions as func
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

import function_app
from mower.runtime import CycleResult
from occupancy_notifications import (
    AzureOccupancyNotificationStore,
    VerifiedContact,
    event_team_keys,
    find_collisions,
    process_collision_notifications,
    process_contact_verifications,
    register_contacts,
    verify_contact,
)


NOW = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "SSV53_OCCUPANCY_COLLISION_NOTIFICATIONS_ENABLED": "true",
        "SSV53_ORDER_MAIL_ENABLED": "true",
        "SSV53_ORDER_MAIL_SMTP_HOST": "smtp.example.test",
        "SSV53_ORDER_MAIL_SMTP_PORT": "587",
        "SSV53_ORDER_MAIL_SMTP_USERNAME": "info@ssv53.de",
        "SSV53_ORDER_MAIL_SMTP_PASSWORD": "secret",
        "SSV53_ORDER_MAIL_FROM_ADDRESS": "info@ssv53.de",
        "SSV53_ORDER_MAIL_FROM_NAME": "Schönwalder SV 1953 e.V.",
        "WEBSITE_HOSTNAME": "func.example.test",
    }
    values.update(overrides)
    return values


class MemoryStore:
    def __init__(self, contacts: list[VerifiedContact] | None = None) -> None:
        self.contacts = contacts or []
        self.pending: dict[str, tuple[str, str, frozenset[str], str]] = {}
        self.claimed: set[str] = set()
        self.delivery_status: dict[str, str] = {}
        self.register_calls = 0

    def register_candidate(self, *, name, email, team_keys, token, token_hash, now_utc) -> bool:
        self.register_calls += 1
        if email in self.pending:
            return False
        self.pending[email] = (token_hash, name, team_keys, token)
        return True

    def mark_verification_sent(self, email, token_hash, now_utc) -> None:
        assert self.pending[email][0] == token_hash

    def pending_verifications(self, now_utc, *, limit=5):
        from occupancy_notifications import PendingVerification
        return [
            PendingVerification(name, email, keys, token, token_hash)
            for email, (token_hash, name, keys, token) in list(self.pending.items())[:limit]
        ]

    def verify(self, token_hash, now_utc) -> bool:
        for email, (saved_hash, name, keys, token) in list(self.pending.items()):
            if saved_hash == token_hash:
                self.contacts.append(VerifiedContact(name, email, keys))
                del self.pending[email]
                return True
        return False

    def verified_contacts(self) -> list[VerifiedContact]:
        return list(self.contacts)

    def claim_delivery(self, fingerprint, now_utc) -> bool:
        if fingerprint in self.claimed:
            return False
        self.claimed.add(fingerprint)
        return True

    def mark_delivery(self, fingerprint, status, now_utc) -> None:
        self.delivery_status[fingerprint] = status


class FakeTableClient:
    def __init__(self) -> None:
        self.entities = {}

    def get_entity(self, partition_key, row_key):
        try:
            return dict(self.entities[(partition_key, row_key)])
        except KeyError as exc:
            raise ResourceNotFoundError("missing") from exc

    def upsert_entity(self, entity, mode=None):
        self.entities[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)

    def update_entity(self, entity, mode=None):
        key = (entity["PartitionKey"], entity["RowKey"])
        if key not in self.entities:
            raise ResourceNotFoundError("missing")
        self.entities[key].update(entity)

    def create_entity(self, entity):
        key = (entity["PartitionKey"], entity["RowKey"])
        if key in self.entities:
            raise ResourceExistsError("exists")
        self.entities[key] = dict(entity)

    def query_entities(self, query_filter, parameters):
        rows = [
            dict(value)
            for (partition, row), value in self.entities.items()
            if partition == parameters["partition"]
        ]
        if "token" in parameters:
            rows = [row for row in rows if row.get("PendingTokenHash") == parameters["token"]]
        if "Verified eq true" in query_filter:
            rows = [row for row in rows if row.get("Verified") is True]
        return rows


class OccupancyNotificationTests(unittest.TestCase):
    def test_verified_mapping_cannot_be_overwritten_without_new_mail_confirmation(self) -> None:
        table = FakeTableClient()
        store = AzureOccupancyNotificationStore(table)
        first_token = "first-token"
        first_hash = hashlib.sha256(first_token.encode()).hexdigest()
        self.assertTrue(store.register_candidate(
            name="Juliane", email="juliane@example.de", team_keys=frozenset({"team:c"}),
            token=first_token, token_hash=first_hash, now_utc=NOW,
        ))
        self.assertTrue(store.verify(first_hash, NOW))
        self.assertEqual(store.verified_contacts()[0].team_keys, frozenset({"team:c"}))

        malicious_token = "malicious-token"
        malicious_hash = hashlib.sha256(malicious_token.encode()).hexdigest()
        self.assertTrue(store.register_candidate(
            name="Falsche Zuordnung", email="juliane@example.de",
            team_keys=frozenset({"team:herren"}), token=malicious_token,
            token_hash=malicious_hash, now_utc=NOW,
        ))
        # Bis zum Klick im betroffenen Postfach bleibt die bisherige C-Zuordnung aktiv.
        current = store.verified_contacts()[0]
        self.assertEqual(current.name, "Juliane")
        self.assertEqual(current.team_keys, frozenset({"team:c"}))

    def test_registration_is_write_only_and_requires_mailbox_verification(self) -> None:
        store = MemoryStore()
        result = register_contacts(
            [{
                "name": "Juliane Beispiel",
                "email": "juliane@example.de",
                "teamKeys": ["team:c", "year:2012"],
            }],
            environment(),
            now_utc=NOW,
            store=store,
        )
        self.assertEqual(result, {"accepted": 1, "pending": 1})
        self.assertNotIn("juliane", json.dumps(result).lower())
        messages = []
        self.assertEqual(
            process_contact_verifications(
                NOW,
                environment(),
                store=store,
                mail_sender=lambda settings, message: messages.append(message),
            ),
            1,
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["To"], "juliane@example.de")
        body = messages[0].get_content()
        link = re.search(r"https://[^\s]+token=([A-Za-z0-9_-]+)", body)
        self.assertIsNotNone(link)
        self.assertNotIn("juliane%40", link.group(0).lower())
        self.assertTrue(
            verify_contact(link.group(1), environment(), now_utc=NOW, store=store)
        )
        self.assertFalse(
            verify_contact(link.group(1), environment(), now_utc=NOW, store=store)
        )
        self.assertEqual(store.contacts[0].email, "juliane@example.de")

    def test_invalid_and_excess_contacts_are_not_accepted(self) -> None:
        store = MemoryStore()
        result = register_contacts(
            [
                {"name": "Ohne Mail", "email": "", "teamKeys": ["team:c"]},
                {"name": "Falscher Schlüssel", "email": "x@example.de", "teamKeys": ["other:x"]},
            ],
            environment(),
            now_utc=NOW,
            store=store,
        )
        self.assertEqual(result, {"accepted": 0, "pending": 0})
        with self.assertRaisesRegex(ValueError, "Zu viele"):
            register_contacts(
                [{}] * 101,
                environment(),
                now_utc=NOW,
                store=store,
            )

    def test_team_key_mapping_covers_youth_and_senior_teams(self) -> None:
        self.assertEqual(event_team_keys({"team": "C-Junioren"}), frozenset({"team:c"}))
        self.assertEqual(event_team_keys({"team": "D2-Junioren"}), frozenset({"team:d2"}))
        self.assertEqual(event_team_keys({"team": "Ü40 / Freizeit"}), frozenset({"team:ue40"}))
        self.assertEqual(event_team_keys({"team": "Herren"}), frozenset({"team:herren"}))

    def test_collision_uses_match_occupancy_and_ignores_cancelled_training(self) -> None:
        match = {
            "id": "match:1", "source": "match", "resourceId": "rasen",
            "start": "2026-08-20T19:00:00+02:00", "end": "2026-08-20T20:45:00+02:00",
            "occupancyStart": "2026-08-20T18:00:00+02:00",
            "occupancyEnd": "2026-08-20T21:45:00+02:00",
        }
        training = {
            "id": "training:1", "source": "training", "resourceId": "rasen",
            "start": "2026-08-20T17:30:00+02:00", "end": "2026-08-20T18:30:00+02:00",
        }
        self.assertEqual(find_collisions([match, training]), [(match, training)])
        self.assertEqual(find_collisions([match, {**training, "cancelled": True}]), [])
        self.assertEqual(find_collisions([match, {**training, "resourceId": "kunstrasen"}]), [])

    def test_verified_trainer_receives_one_deduplicated_mail_without_other_contact_data(self) -> None:
        store = MemoryStore([
            VerifiedContact("Juliane Beispiel", "juliane@example.de", frozenset({"team:c"}))
        ])
        sent = []
        payload = {"events": [
            {
                "id": "match:c", "source": "match", "resourceId": "rasen",
                "title": "SSV C - Gast C", "start": "2026-08-20T18:30:00+02:00",
                "end": "2026-08-20T20:15:00+02:00",
                "occupancyStart": "2026-08-20T17:30:00+02:00",
                "occupancyEnd": "2026-08-20T21:15:00+02:00",
            },
            {
                "id": "training:c", "source": "training", "resourceId": "rasen",
                "title": "C", "team": "C", "start": "2026-08-20T17:30:00+02:00",
                "end": "2026-08-20T19:00:00+02:00",
            },
        ]}
        first = process_collision_notifications(
            NOW, environment(), payload=payload, store=store,
            mail_sender=lambda settings, message: sent.append(message),
        )
        second = process_collision_notifications(
            NOW, environment(), payload=payload, store=store,
            mail_sender=lambda settings, message: sent.append(message),
        )
        self.assertEqual(first, {"collisions": 1, "sent": 1})
        self.assertEqual(second, {"collisions": 1, "sent": 0})
        self.assertEqual(sent[0]["To"], "juliane@example.de")
        self.assertNotIn("@", sent[0].get_content())

    def test_unverified_manual_creator_address_is_not_used(self) -> None:
        store = MemoryStore()
        sent = []
        payload = {"events": [
            {
                "id": "match:1", "source": "match", "resourceId": "rasen",
                "title": "Heimspiel", "start": "2026-08-20T19:00:00+02:00",
                "end": "2026-08-20T20:45:00+02:00",
                "occupancyStart": "2026-08-20T18:00:00+02:00",
                "occupancyEnd": "2026-08-20T21:45:00+02:00",
            },
            {
                "id": "one-off:trainer-x", "source": "special", "resourceId": "rasen",
                "title": "Event", "start": "2026-08-20T18:00:00+02:00",
                "end": "2026-08-20T19:30:00+02:00",
                "creator": {"name": "Privat", "email": "unverified@example.de"},
            },
        ]}
        process_collision_notifications(
            NOW, environment(), payload=payload, store=store,
            mail_sender=lambda settings, message: sent.append(message),
        )
        self.assertEqual(sent[0]["To"], "info@ssv53.de")
        self.assertNotIn("unverified@example.de", sent[0].as_string())

    def test_public_registration_response_never_contains_contact_data(self) -> None:
        request = func.HttpRequest(
            method="POST",
            url="https://example.test/api/occupancy-contact-register",
            headers={"Content-Type": "text/plain;charset=UTF-8"},
            params={},
            body=json.dumps({
                "confirmation": "APPACK_KONTAKTE_VERIFIZIEREN",
                "contacts": [{"name": "Privat", "email": "private@example.de", "teamKeys": ["team:c"]}],
            }).encode("utf-8"),
        )
        with patch.object(
            function_app,
            "register_contacts",
            return_value={"accepted": 1, "pending": 1},
        ):
            response = function_app.ssv53_occupancy_contact_register(request)
        body = response.get_body().decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertNotIn("private@example.de", body)
        self.assertNotIn("Privat", body)

    def test_notification_failure_cannot_fail_completed_mower_cycle(self) -> None:
        result = CycleResult(
            schema_version=2,
            executed_at_utc=NOW.isoformat(),
            source="test",
            control_mode="FULL_FAILSAFE",
            past_due=False,
            decision_code="HOLD",
            command_sent=False,
            message="safe",
            details={},
        )
        timer = Mock(past_due=False)
        context = Mock(invocation_id="invocation", retry_context=None)
        with patch.object(function_app, "run_control_cycle", return_value=result), patch.object(
            function_app,
            "process_collision_notifications",
            side_effect=RuntimeError("mail unavailable"),
        ) as notification:
            self.assertIsNone(function_app.ssv53_mower_timer(timer, context))
            notification.assert_not_called()
            self.assertIsNone(function_app.ssv53_occupancy_notification_timer(timer))


if __name__ == "__main__":
    unittest.main()
