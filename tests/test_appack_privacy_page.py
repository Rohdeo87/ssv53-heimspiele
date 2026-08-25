from __future__ import annotations

import hashlib
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "appack-datenschutzerklaerung-2026.html"
TXT_PATH = ROOT / "appack-datenschutzerklaerung-2026.txt"
FIELD_ORDER = (
    "Daten",
    "Zweck",
    "Rechtsgrundlage",
    "Empfänger",
    "Speicherdauer",
    "Herkunft",
    "Pflicht / freiwillig",
)
CONTENT_FINGERPRINT = "6cbd082b9edd8311303e18c5c1d078978b05f0dfa6e1e3bdad93337fc57c5760"


class FragmentTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)


def normalized_markup_text(fragment: str) -> str:
    parser = FragmentTextParser()
    parser.feed(fragment)
    parser.close()
    return " ".join(parser.parts)


class StrictPageParser(HTMLParser):
    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.ids: list[str] = []
        self.scripts = 0
        self.external_links_without_noopener: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "script":
            self.scripts += 1
        href = str(values.get("href") or "")
        if href.startswith(("http://", "https://")):
            rel = set(str(values.get("rel") or "").split())
            if "noopener" not in rel:
                self.external_links_without_noopener.append(href)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            self.errors.append(f"Void-Element </{tag}> darf nicht geschlossen werden")
            return
        if not self.stack:
            self.errors.append(f"Unerwartetes </{tag}>")
            return
        actual = self.stack.pop()
        if actual != tag:
            self.errors.append(f"Erwartet </{actual}>, gefunden </{tag}>")


class PrivacyPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html_bytes = HTML_PATH.read_bytes()
        cls.txt_bytes = TXT_PATH.read_bytes()
        cls.html = cls.html_bytes.decode("utf-8")

    def test_html_and_copy_are_byte_identical(self) -> None:
        self.assertEqual(self.html_bytes, self.txt_bytes)

    def test_html_is_structurally_balanced_and_static(self) -> None:
        parser = StrictPageParser()
        parser.feed(self.html)
        parser.close()
        self.assertEqual([], parser.errors)
        self.assertEqual([], parser.stack)
        self.assertEqual(len(parser.ids), len(set(parser.ids)), "Doppelte HTML-IDs")
        self.assertEqual(0, parser.scripts, "Datenschutzseite soll statisch bleiben")
        self.assertEqual([], parser.external_links_without_noopener)
        self.assertEqual(1, len(re.findall(r"<body(?:\s|>)", self.html)))
        self.assertEqual(1, self.html.count("</body>"))
        self.assertEqual(1, self.html.count("</html>"))

    def test_required_article_13_information_is_present(self) -> None:
        required = (
            "Schönwalder SV 1953 e.V.",
            "Kurmärkische Straße 2",
            "14621 Schönwalde-Glien",
            "info@ssv53.de",
            "Art. 6 Abs. 1",
            "Speicherdauer",
            "Herkunft",
            "Pflicht / freiwillig",
            "Drittlandübermittlungen",
            "Deine Datenschutzrechte",
            "Landesbeauftragte für den Datenschutz",
            "Stahnsdorfer Damm 77",
            "25. August 2026",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, self.html)

    def test_purpose_groups_use_progressive_disclosure_cards(self) -> None:
        self.assertEqual(5, self.html.count('<details class="purpose-group">'))
        self.assertEqual(20, self.html.count('<details class="processing-card">'))
        group_parts = self.html.split('<details class="purpose-group">')[1:]
        self.assertEqual([5, 5, 3, 2, 5], [part.count('<details class="processing-card">') for part in group_parts])
        self.assertEqual(5, self.html.count('class="purpose-index" aria-hidden="true"'))
        self.assertNotIn('role="listitem"', self.html)
        self.assertNotIn("<table", self.html)
        self.assertNotIn("table-wrap", self.html)
        self.assertNotIn("min-width:1180px", self.html)

    def test_all_140_processing_values_are_unchanged(self) -> None:
        groups: list[list[list[str]]] = []
        group_parts = self.html.split('<details class="purpose-group">')[1:]
        value_pattern = re.compile(
            r'<(span|dd)\b[^>]*data-privacy-field="([^"]+)"[^>]*>(.*?)</\1>',
            re.DOTALL,
        )
        for group_part in group_parts:
            cards = re.findall(
                r'<details class="processing-card">(.*?)</details>',
                group_part,
                re.DOTALL,
            )
            rows: list[list[str]] = []
            for card in cards:
                values: dict[str, str] = {}
                for _, label, fragment in value_pattern.findall(card):
                    self.assertNotIn(label, values, f"Doppeltes Datenschutzfeld {label}")
                    values[label] = normalized_markup_text(fragment)
                self.assertEqual(set(FIELD_ORDER), set(values))
                rows.append([values[label] for label in FIELD_ORDER])
            groups.append(rows)
        payload = json.dumps(groups, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(CONTENT_FINGERPRINT, hashlib.sha256(payload).hexdigest())

    def test_layout_is_mobile_and_large_text_safe(self) -> None:
        for rule in (
            "max-width:720px",
            "overflow-wrap:anywhere",
            ".processing-fields{grid-template-columns:1fr}",
            "details.processing-card>.processing-body{display:block!important}",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, self.html)

    def test_confirmed_retention_values_and_transparency_are_present(self) -> None:
        for text in (
            "abgelaufene aktive Termine bis 90 Tage nach Terminende",
            "gelöschte Termine bis 90 Tage nach Lösch-/Änderungszeitpunkt",
            "Änderungs-/Metadatenprotokoll bis 180 Tage",
            "fehlgeschlagene Anmeldungen 7 Tage",
            "Sitzung 30 Minuten",
            "Bedien- und Sicherheitsaudit 180 Tage",
            "derzeit nicht auf eine bestimmte Rolle begrenzt",
            "reduzierte Identitätsangabe",
        ):
            with self.subTest(text=text):
                self.assertIn(text, self.html)
        self.assertNotIn("höchstens 24 Monate", self.html)
        self.assertNotIn("Fehlversuche grundsätzlich höchstens 30 Tage", self.html)

    def test_source_comment_is_neutral_and_contains_no_internal_checklist(self) -> None:
        self.assertIn("SSV53 Datenschutzerklärung · Version 2.0", self.html)
        self.assertNotIn("FREIGABECHECK", self.html)
        self.assertNotIn("Risikoregister", self.html)
        self.assertNotIn("Unterauftragsverarbeiter", self.html)


if __name__ == "__main__":
    unittest.main()
