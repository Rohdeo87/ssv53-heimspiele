from __future__ import annotations

import smtplib
import unittest

from order_mail import (
    OrderMailError,
    check_smtp_connection,
    send_order_ready_mail,
)


def base_env(**overrides):
    values = {
        "SSV53_ORDER_MAIL_ENABLED": "false",
        "SSV53_ORDER_MAIL_SMTP_HOST": "smtp.mail.ovh.net",
        "SSV53_ORDER_MAIL_SMTP_PORT": "587",
        "SSV53_ORDER_MAIL_SMTP_USERNAME": "info@ssv53.de",
        "SSV53_ORDER_MAIL_SMTP_PASSWORD": "test-secret",
        "SSV53_ORDER_MAIL_FROM_ADDRESS": "info@ssv53.de",
        "SSV53_ORDER_MAIL_FROM_NAME": "Schönwalder SV 1953 e.V.",
        "SSV53_STORAGE_ACCOUNT_URL": "https://example.table.core.windows.net/",
        "SSV53_STATE_TABLE_NAME": "MowerAutomationState",
        "AzureWebJobsStorage__clientId": "00000000-0000-0000-0000-000000000000",
    }
    values.update(overrides)
    return values


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=20, **kwargs):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None
        self.message = None
        FakeSMTP.instances.append(self)

    def ehlo(self):
        return (250, b"ok")

    def starttls(self, context=None):
        self.started_tls = True
        return (220, b"ready")

    def login(self, username, password):
        self.logged_in = (username, password)
        return (235, b"ok")

    def noop(self):
        return (250, b"ok")

    def send_message(self, message):
        self.message = message
        return {}

    def quit(self):
        return (221, b"bye")


class FakeStore:
    def __init__(self, claim_result="claimed"):
        self.claim_result = claim_result
        self.sent = []
        self.failed = []

    def claim(self, *, order_id, recipient):
        return self.claim_result

    def mark_sent(self, *, order_id, recipient):
        self.sent.append((order_id, recipient))

    def mark_failed(self, *, order_id, recipient, error_code):
        self.failed.append((order_id, recipient, error_code))


class OrderMailTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_check_authenticates_with_starttls_without_sending(self):
        result = check_smtp_connection(
            base_env(),
            smtp_factory=FakeSMTP,
            smtp_ssl_factory=FakeSMTP,
        )
        self.assertTrue(result["ok"])
        smtp = FakeSMTP.instances[-1]
        self.assertTrue(smtp.started_tls)
        self.assertEqual(smtp.logged_in, ("info@ssv53.de", "test-secret"))
        self.assertIsNone(smtp.message)

    def test_send_is_blocked_while_disabled(self):
        with self.assertRaises(OrderMailError) as context:
            send_order_ready_mail(
                {
                    "confirmation": "SSV53-ORDER-ABHOLBEREIT",
                    "orderId": "SSV53-260818-123456-AB12",
                    "email": "test@example.org",
                    "name": "Max Mustermann",
                },
                base_env(),
                smtp_factory=FakeSMTP,
                smtp_ssl_factory=FakeSMTP,
                store=FakeStore(),
            )
        self.assertEqual(context.exception.code, "ORDER_MAIL_DISABLED")
        self.assertEqual(FakeSMTP.instances, [])

    def test_enabled_send_uses_fixed_subject_and_marks_sent(self):
        store = FakeStore()
        result = send_order_ready_mail(
            {
                "confirmation": "SSV53-ORDER-ABHOLBEREIT",
                "orderId": "SSV53-260818-123456-AB12",
                "email": "test@example.org",
                "name": "Max Mustermann",
            },
            base_env(SSV53_ORDER_MAIL_ENABLED="true"),
            smtp_factory=FakeSMTP,
            smtp_ssl_factory=FakeSMTP,
            store=store,
        )
        self.assertTrue(result["sent"])
        smtp = FakeSMTP.instances[-1]
        self.assertEqual(
            smtp.message["Subject"],
            "Deine SSV53-Bestellung ist abholbereit",
        )
        self.assertEqual(smtp.message["To"], "test@example.org")
        self.assertIn(
            ("SSV53-260818-123456-AB12", "test@example.org"),
            store.sent,
        )

    def test_duplicate_does_not_send_again(self):
        store = FakeStore(claim_result="sent")
        result = send_order_ready_mail(
            {
                "confirmation": "SSV53-ORDER-ABHOLBEREIT",
                "orderId": "SSV53-260818-123456-AB12",
                "email": "test@example.org",
                "name": "Max Mustermann",
            },
            base_env(SSV53_ORDER_MAIL_ENABLED="true"),
            smtp_factory=FakeSMTP,
            smtp_ssl_factory=FakeSMTP,
            store=store,
        )
        self.assertTrue(result["alreadySent"])
        self.assertEqual(FakeSMTP.instances, [])

    def test_invalid_order_id_is_rejected(self):
        with self.assertRaises(OrderMailError) as context:
            send_order_ready_mail(
                {
                    "confirmation": "SSV53-ORDER-ABHOLBEREIT",
                    "orderId": "ABC",
                    "email": "test@example.org",
                    "name": "Max",
                },
                base_env(SSV53_ORDER_MAIL_ENABLED="true"),
                smtp_factory=FakeSMTP,
                smtp_ssl_factory=FakeSMTP,
                store=FakeStore(),
            )
        self.assertEqual(context.exception.code, "ORDER_ID_INVALID")

    def test_unresolved_key_vault_reference_is_rejected(self):
        with self.assertRaises(OrderMailError) as context:
            check_smtp_connection(
                base_env(
                    SSV53_ORDER_MAIL_SMTP_PASSWORD=(
                        "@Microsoft.KeyVault(VaultName=x;SecretName=y)"
                    )
                ),
                smtp_factory=FakeSMTP,
                smtp_ssl_factory=FakeSMTP,
            )
        self.assertEqual(context.exception.code, "SMTP_SECRET_NOT_RESOLVED")


if __name__ == "__main__":
    unittest.main()
