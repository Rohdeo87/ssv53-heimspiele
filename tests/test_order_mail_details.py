from __future__ import annotations

import unittest

from order_mail import OrderMailError, send_order_ready_mail


def base_env(**overrides):
    values = {
        "SSV53_ORDER_MAIL_ENABLED": "true",
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
        self.__class__.instances.append(self)

    def ehlo(self):
        return (250, b"ok")

    def starttls(self, context=None):
        self.started_tls = True
        return (220, b"ready")

    def login(self, username, password):
        self.logged_in = (username, password)
        return (235, b"ok")

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


class OrderMailDetailTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    @staticmethod
    def payload():
        return {
            "confirmation": "SSV53-ORDER-ABHOLBEREIT",
            "orderId": "SSV53-260818-123456-AB12",
            "email": "test@example.org",
            "name": "Max Mustermann",
            "items": [
                {
                    "variant": "Kids – Weiß",
                    "groupKey": "kids",
                    "colorKey": "weiss",
                    "size": "116",
                    "qty": 1,
                    "unitPriceCents": 1500,
                },
                {
                    "variant": "Frauen – Blau",
                    "groupKey": "damen",
                    "colorKey": "blau",
                    "size": "M",
                    "qty": 2,
                    "unitPriceCents": 1500,
                },
            ],
            "totalCents": 4500,
        }

    def test_shop_style_mail_contains_order_details_and_ssv_design(self):
        result = send_order_ready_mail(
            self.payload(),
            base_env(),
            smtp_factory=FakeSMTP,
            smtp_ssl_factory=FakeSMTP,
            store=FakeStore(),
        )
        self.assertTrue(result["sent"])
        self.assertEqual(result["itemCount"], 2)
        self.assertEqual(result["totalCents"], 4500)

        message = FakeSMTP.instances[-1].message
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()

        self.assertIn("Kids – Weiß", plain)
        self.assertIn("Frauen – Blau", plain)
        self.assertIn("45,00 €", plain)
        self.assertIn("Größe 116", plain)

        self.assertIn("#285EA7", html)
        self.assertIn("#E0AA3F", html)
        self.assertIn("Icon_Verein.png", html)
        self.assertIn("Kids_wei%C3%9F.jpeg", html)
        self.assertIn("Damen_blau.jpeg", html)
        self.assertIn("AUSGABEBEREIT", html)
        self.assertIn("Gesamtsumme", html)
        self.assertIn("45,00 €", html)
        self.assertIn("Sonstiges → Meine Reservierungen", html)
        self.assertNotIn("Bezahlung", html)
        self.assertIn("Deine Reservierung", plain)

    def test_client_supplied_image_url_is_never_used(self):
        payload = self.payload()
        payload["items"][0]["imageUrl"] = "https://evil.example.invalid/pixel.png"

        send_order_ready_mail(
            payload,
            base_env(),
            smtp_factory=FakeSMTP,
            smtp_ssl_factory=FakeSMTP,
            store=FakeStore(),
        )
        html = (
            FakeSMTP.instances[-1]
            .message.get_body(preferencelist=("html",))
            .get_content()
        )
        self.assertNotIn("evil.example.invalid", html)
        self.assertIn("Kids_wei%C3%9F.jpeg", html)

    def test_total_mismatch_is_rejected_before_smtp(self):
        payload = self.payload()
        payload["totalCents"] = 4400

        with self.assertRaises(OrderMailError) as context:
            send_order_ready_mail(
                payload,
                base_env(),
                smtp_factory=FakeSMTP,
                smtp_ssl_factory=FakeSMTP,
                store=FakeStore(),
            )
        self.assertEqual(context.exception.code, "ORDER_TOTAL_MISMATCH")
        self.assertEqual(FakeSMTP.instances, [])

    def test_invalid_product_key_is_rejected(self):
        payload = self.payload()
        payload["items"][0]["groupKey"] = "extern"

        with self.assertRaises(OrderMailError) as context:
            send_order_ready_mail(
                payload,
                base_env(),
                smtp_factory=FakeSMTP,
                smtp_ssl_factory=FakeSMTP,
                store=FakeStore(),
            )
        self.assertEqual(context.exception.code, "ORDER_DETAILS_INVALID")
        self.assertEqual(FakeSMTP.instances, [])

    def test_legacy_payload_without_items_still_works(self):
        legacy = {
            "confirmation": "SSV53-ORDER-ABHOLBEREIT",
            "orderId": "SSV53-260818-123456-CD34",
            "email": "test@example.org",
            "name": "Max Mustermann",
        }
        result = send_order_ready_mail(
            legacy,
            base_env(),
            smtp_factory=FakeSMTP,
            smtp_ssl_factory=FakeSMTP,
            store=FakeStore(),
        )
        self.assertTrue(result["sent"])
        self.assertEqual(result["itemCount"], 0)


if __name__ == "__main__":
    unittest.main()
