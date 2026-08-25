from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "appack-datenschutzerklaerung-2026.html"
TXT_PATH = ROOT / "appack-datenschutzerklaerung-2026.txt"


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

    def test_purpose_groups_and_seven_column_tables(self) -> None:
        groups = re.findall(r"<summary>(\d)\. ([^<]+)</summary>", self.html)
        self.assertEqual(["1", "2", "3", "4", "5"], [number for number, _ in groups])
        tables = re.findall(r"<table>(.*?)</table>", self.html, re.DOTALL)
        self.assertEqual(5, len(tables))
        for index, table in enumerate(tables, 1):
            headers = re.findall(r"<th>(.*?)</th>", table, re.DOTALL)
            self.assertEqual(7, len(headers), f"Tabelle {index} hat nicht sieben Spalten")
            rows = re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL)[1:]
            self.assertGreater(len(rows), 0)
            for row in rows:
                self.assertEqual(7, len(re.findall(r"<td\b", row)))

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
