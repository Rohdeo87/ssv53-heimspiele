"""Both customer notifications use one layout and the same approved contacts."""
import unittest
from email import policy
from email.parser import BytesParser

from order_mail import (OrderMailSettings, _build_ready_message, _first_name,
                        _validate_ready_request)
from reservation_notifications import build_message
from tests import test_order_mail_details as fixtures


class CustomerMailLayoutTests(unittest.TestCase):
    def messages(self, name='Mustermann, Max'):
        payload = dict(fixtures.OrderMailDetailTests.payload(), name=name, email='buyer@example.test')
        order_id, recipient, name, items, total = _validate_ready_request(payload)
        settings = OrderMailSettings.from_mapping(fixtures.base_env())
        ready = _build_ready_message(settings=settings, order_id=order_id, recipient=recipient,
            name=name, items=items, total_cents=total)
        reservation = build_message(settings, {'orderId': order_id, 'email': recipient,
            'name': name, 'items': items, 'totalCents': total}, 'customer', 'office@example.test')
        return ready, reservation

    def test_first_name_formats_and_missing_name(self):
        for full, first in [('Mustermann, Max', 'Max'), ('Max Mustermann', 'Max'),
                            ('Musterfrau, Anna-Lena', 'Anna-Lena'),
                            ('Musterfrau, Anna Maria', 'Anna Maria'),
                            ('Dr. Max Mustermann', 'Max'), ('', ''),
                            ('Mustermann,', ''), ('SSV53-Mitglied', '')]:
            with self.subTest(full=full):
                self.assertEqual(_first_name(full), first)

    def test_both_customer_mails_use_first_name_and_existing_pickup_layout(self):
        for message in self.messages():
            with self.subTest(subject=str(message['Subject'])):
                plain = message.get_body(preferencelist=('plain',)).get_content()
                html = message.get_body(preferencelist=('html',)).get_content()
                self.assertIn('Hallo Max,', plain)
                self.assertIn('Hallo <strong>Max</strong>,', html)
                self.assertNotIn('Mustermann', plain + html)
                for marker in ('Icon_Verein.png', '#E0AA3F', '#285EA7',
                               'Kids_wei%C3%9F.jpeg', 'Damen_blau.jpeg',
                               'Gesamtsumme', '45,00 €', 'Reservierungsnummer'):
                    self.assertIn(marker, html)
                self.assertEqual(str(message['To']), 'buyer@example.test')
                self.assertIsNone(message['Cc'])
                self.assertIsNone(message['Bcc'])

    def test_confirmation_does_not_claim_immediate_pickup(self):
        ready, reservation = self.messages()
        ready_html = ready.get_body(preferencelist=('html',)).get_content()
        reservation_html = reservation.get_body(preferencelist=('html',)).get_content()
        self.assertIn('AUSGABEBEREIT', ready_html)
        self.assertIn('ab sofort zur Ausgabe bereit', ready_html)
        self.assertIn('VORGEMERKT', reservation_html)
        self.assertIn('unverbindliche', reservation_html)
        self.assertIn('noch keine Abholbenachrichtigung', reservation_html)
        self.assertNotIn('ab sofort zur Ausgabe bereit', reservation_html)

    def test_contacts_work_without_loading_remote_icons_or_app_navigation(self):
        for message in self.messages():
            # Roundtrip the complete MIME message as an email client receives it.
            received = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
            html = received.get_body(preferencelist=('html',)).get_content()
            plain = received.get_body(preferencelist=('plain',)).get_content()
            self.assertIn('Abholung nach Terminvereinbarung', plain)
            self.assertIn('Termin', html)
            for url in ('mailto:info@ssv53.de', 'https://wa.me/4915567740811', 'tel:+4915567740811'):
                self.assertIn('href="' + url + '"', html)
            self.assertIn('info@ssv53.de', plain)
            self.assertIn('015567 740811', plain)
            self.assertIn('https://wa.me/4915567740811', plain)
            self.assertNotIn('ext://', html)
            icons = [part for part in received.walk() if part.get_content_type() == 'image/png']
            self.assertEqual(len(icons), 3)
            for icon in icons:
                self.assertEqual(icon.get_content_disposition(), 'inline')
                cid = str(icon['Content-ID']).strip('<>')
                self.assertIn('src="cid:' + cid + '"', html)
                self.assertTrue(icon.get_payload(decode=True).startswith(b'\x89PNG\r\n\x1a\n'))

    def test_first_name_is_escaped_in_both_mails(self):
        for message in self.messages('Musterfrau, <script>bad</script>'):
            html = message.get_body(preferencelist=('html',)).get_content()
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)

    def test_internal_office_notification_keeps_contact_name(self):
        payload = dict(fixtures.OrderMailDetailTests.payload(), name='Mustermann, Max')
        order_id, recipient, name, items, total = _validate_ready_request(payload)
        message = build_message(OrderMailSettings.from_mapping(fixtures.base_env()),
            {'orderId': order_id, 'email': recipient, 'name': name, 'items': items,
             'totalCents': total}, 'office', 'office@example.test')
        self.assertIn('Mustermann, Max', message.get_body(preferencelist=('plain',)).get_content())
