from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import function_app
from mower.runtime import CycleResult
from occupancy_notifications import (
    collision_recipients,
    find_collisions,
    process_collision_notifications,
    send_collision_test_mail,
)


NOW = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


def message_part(message, content_type: str) -> str:
    return next(
        part.get_content()
        for part in message.walk()
        if part.get_content_type() == content_type
    )


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "SSV53_OCCUPANCY_COLLISION_NOTIFICATIONS_ENABLED": "true",
        "SSV53_OCCUPANCY_COLLISION_RECIPIENTS": "info@ssv53.de,Thomas.rohde@ssv53.de",
        "SSV53_ORDER_MAIL_ENABLED": "true",
        "SSV53_ORDER_MAIL_SMTP_HOST": "smtp.example.test",
        "SSV53_ORDER_MAIL_SMTP_PORT": "587",
        "SSV53_ORDER_MAIL_SMTP_USERNAME": "info@ssv53.de",
        "SSV53_ORDER_MAIL_SMTP_PASSWORD": "secret",
        "SSV53_ORDER_MAIL_FROM_ADDRESS": "info@ssv53.de",
        "SSV53_ORDER_MAIL_FROM_NAME": "Schönwalder SV 1953 e.V.",
    }
    values.update(overrides)
    return values


class MemoryStore:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.delivery_status: dict[str, str] = {}

    def claim_delivery(self, fingerprint, now_utc) -> bool:
        if fingerprint in self.claimed:
            return False
        self.claimed.add(fingerprint)
        return True

    def mark_delivery(self, fingerprint, status, now_utc) -> None:
        self.delivery_status[fingerprint] = status


def collision_payload(*, creator_email: str = "trainer@example.de") -> dict:
    return {"events": [
        {
            "id": "match:c", "source": "match", "resourceId": "rasen",
            "title": "SSV C - Gast C", "kickoff": "2026-08-20T18:30:00+02:00",
            "start": "2026-08-20T18:30:00+02:00", "end": "2026-08-20T20:15:00+02:00",
            "occupancyStart": "2026-08-20T17:30:00+02:00",
            "occupancyEnd": "2026-08-20T21:15:00+02:00",
        },
        {
            "id": "one-off:trainer", "source": "special", "resourceId": "rasen",
            "title": "Zusatztraining", "start": "2026-08-20T17:45:00+02:00",
            "end": "2026-08-20T19:00:00+02:00",
            "creator": {"name": "Trainer Privat", "email": creator_email,
                        "phone": "+49 170 1234567"},
        },
    ]}


class OccupancyNotificationTests(unittest.TestCase):
    def test_recipients_are_server_configured_validated_and_deduplicated(self) -> None:
        self.assertEqual(
            collision_recipients(environment(
                SSV53_OCCUPANCY_COLLISION_RECIPIENTS=(
                    " info@ssv53.de ; Thomas.Rohde@ssv53.de,INFO@ssv53.de "
                )
            )),
            ("info@ssv53.de", "thomas.rohde@ssv53.de"),
        )
        for raw in ("", "keine-mail", ",".join(f"x{i}@example.de" for i in range(6))):
            with self.assertRaises(RuntimeError):
                collision_recipients(environment(SSV53_OCCUPANCY_COLLISION_RECIPIENTS=raw))

    def test_collision_uses_match_occupancy_and_ignores_cancelled_training(self) -> None:
        match, booking = collision_payload()["events"]
        self.assertEqual(find_collisions([match, booking]), [(match, booking)])
        self.assertEqual(find_collisions([match, {**booking, "cancelled": True}]), [])
        self.assertEqual(find_collisions([match, {**booking, "resourceId": "kunstrasen"}]), [])

    def test_both_central_addresses_receive_one_mail_without_trainer_data(self) -> None:
        store = MemoryStore()
        sent = []
        first = process_collision_notifications(
            NOW, environment(), payload=collision_payload(), store=store,
            mail_sender=lambda settings, message: sent.append(message),
        )
        second = process_collision_notifications(
            NOW, environment(), payload=collision_payload(), store=store,
            mail_sender=lambda settings, message: sent.append(message),
        )
        self.assertEqual(first, {"collisions": 1, "sent": 2})
        self.assertEqual(second, {"collisions": 1, "sent": 0})
        self.assertEqual([message["To"] for message in sent], [
            "info@ssv53.de", "thomas.rohde@ssv53.de",
        ])
        combined = "\n".join(message.as_string() for message in sent).lower()
        self.assertNotIn("trainer@example.de", combined)
        self.assertNotIn("trainer privat", combined)
        self.assertNotIn("+49 170", combined)
        plain = message_part(sent[0], "text/plain")
        branded_html = message_part(sent[0], "text/html")
        self.assertIn("18:30", plain)
        self.assertIn("Überschneidung erkannt", branded_html)
        self.assertIn("#285EA7", branded_html)
        self.assertIn("Icon_Verein.png", branded_html)
        self.assertNotIn("personenbezogen", sent[0].as_string().lower())
        self.assertNotIn("kontaktdaten", sent[0].as_string().lower())

    def test_changed_relevant_time_creates_new_notification(self) -> None:
        store = MemoryStore()
        sent = []
        process_collision_notifications(
            NOW, environment(), payload=collision_payload(), store=store,
            mail_sender=lambda settings, message: sent.append(message),
        )
        changed = collision_payload()
        changed["events"][1]["end"] = "2026-08-20T19:15:00+02:00"
        process_collision_notifications(
            NOW, environment(), payload=changed, store=store,
            mail_sender=lambda settings, message: sent.append(message),
        )
        self.assertEqual(len(sent), 4)

    def test_disabled_feature_does_not_parse_recipients_or_send(self) -> None:
        result = process_collision_notifications(
            NOW,
            environment(
                SSV53_OCCUPANCY_COLLISION_NOTIFICATIONS_ENABLED="false",
                SSV53_OCCUPANCY_COLLISION_RECIPIENTS="",
            ),
            payload=collision_payload(),
            store=MemoryStore(),
            mail_sender=lambda settings, message: self.fail("mail sent"),
        )
        self.assertEqual(result, {"collisions": 0, "sent": 0})

    def test_notification_failure_cannot_fail_completed_mower_cycle(self) -> None:
        result = CycleResult(
            schema_version=2, executed_at_utc=NOW.isoformat(), source="test",
            control_mode="FULL_FAILSAFE", past_due=False, decision_code="HOLD",
            command_sent=False, message="safe", details={},
        )
        timer = Mock(past_due=False)
        context = Mock(invocation_id="invocation", retry_context=None)
        with patch.object(function_app, "run_control_cycle", return_value=result), patch.object(
            function_app, "process_collision_notifications",
            side_effect=RuntimeError("mail unavailable"),
        ) as notification:
            self.assertIsNone(function_app.ssv53_mower_timer(timer, context))
            notification.assert_not_called()
            self.assertIsNone(function_app.ssv53_occupancy_notification_timer(timer))

    def test_contact_registration_routes_are_removed(self) -> None:
        self.assertFalse(hasattr(function_app, "ssv53_occupancy_contact_register"))
        self.assertFalse(hasattr(function_app, "ssv53_occupancy_contact_verify"))

    def test_test_mail_only_uses_server_configured_central_recipients(self) -> None:
        sent = []
        result = send_collision_test_mail(
            environment(),
            mail_sender=lambda settings, message: sent.append(message),
        )
        self.assertEqual(result, {"ok": True, "sent": 2})
        self.assertEqual(
            [message["To"] for message in sent],
            ["info@ssv53.de", "thomas.rohde@ssv53.de"],
        )
        self.assertTrue(all("[TEST]" in message["Subject"] for message in sent))
        self.assertTrue(all(message.is_multipart() for message in sent))
        self.assertTrue(all("Test erfolgreich" in message_part(message, "text/html") for message in sent))
        self.assertTrue(all("keine weitere Aktion" in message_part(message, "text/plain") for message in sent))


if __name__ == "__main__":
    unittest.main()
