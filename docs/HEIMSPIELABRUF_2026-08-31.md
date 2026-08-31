# Heimspielabruf: Verlegungen und Kinderfestivals

## Ursache und Korrektur

Run 33400186560 vom 31.08.2026 brach bei Spiel 031DB8NS8C000000VS5489BUVUR5FS5A ab.
Die alte Zeile (11.09., 19:30) verwies in `column-score/info-text` ausdrücklich auf
15.09., 19:30. Die neue Zeile enthielt vertauschtes Heimrecht, dieselbe Spielnummer
und dieselben beiden Mannschafts-IDs. Ein altes Auswärtsspiel blockierte dadurch
auch die Veröffentlichung der neuen Festivals.

Explizite Verlegungsketten werden jetzt anhand dieser strukturierten Informationen
aufgelöst. Nur ein vorhandener eindeutiger Endknoten mit Spielstätte ist gültig.
Zyklen, fehlende Ziele, mehrere Ziele und abweichende Identitäten führen weiter zum
Abbruch. Desktop-/Mobilduplikate des Endknotens müssen weiterhin widerspruchsfrei
sein. Ein bloßer Heimrechttausch ohne Verlegungshinweis wird NICHT freigegeben.
Es werden keine zusätzlichen Abrufe für diese Auflösung benötigt.

Festivalteilnehmer werden anhand des tatsächlichen Mannschaftslinks identifiziert.
Eine inaktive Gastgeberbeschriftung ist keine Mannschaft. Die Zuordnung darf nur
den eigenen Verein betreffen. Festivalgruppenlinks bleiben im Feed erhalten und
werden in der Vollständigkeitsprüfung mitgezählt. Verschiedene Jahrgänge bleiben
verschiedene Teams. Die bereits verwendeten offiziellen Spielnummern bleiben IDs.

## Freigegebene Festivaldauer

Vereinsvorgabe vom 31.08.2026: E-Festivals 90 Minuten; bei unklarer Festivaldauer
ebenfalls 90 Minuten. Diese Zeit umfasst die Veranstaltung einschließlich Pausen.
Sie wird als Vereinsvorgabe protokolliert, nicht als vom Verband bestätigte Endzeit.
Die bisherige G-Regel mit nur 49 Minuten reiner Spielzeit ist ersetzt.
Verifizierte abweichende Regeln können vor dem generischen Fallback ergänzt werden;
für E gilt die ausdrückliche 90-Minuten-Vorgabe.

Bestehende Vor-/Nachlaufpuffer bleiben jeweils 60 Minuten:

| Veranstaltung | Nominelles Zeitfenster | Platzsperre |
| --- | --- | --- |
| 19.09.2026 F, Jahrgang 2019 | 14:00–15:30 | 13:00–16:30 |
| 27.09.2026 E, Jahrgang 2016 | 13:30–15:00 | 12:30–16:00 |
| 27.09.2026 E, Jahrgang 2017 | 15:30–17:00 | 14:30–18:00 |

## Veröffentlichung und Grenzen

`main` muss dieselben Versionen von `poc_scraper.py`, `create_feed.py`,
`occupancy/match_model.py` und der Zeitkonfiguration wie der Azure-Branch verwenden.
`create_feed` prüft vor der Veröffentlichung nominale Zeiten, Sperrzeiten, Dauer,
Zeitzonen, Format und Regel-ID gegen das gemeinsame Zeitmodell. Die Azure-Bundle-
Tests prüfen zusätzlich die echte Fixture durch Parser und Runtime-Aufbereitung.
Quellzeitstempel werden nicht auf die Uploadzeit umgeschrieben.

Der Quellenabruf erfolgt weiter über den bestehenden Workflow inklusive Zeitfenster,
Frischeprüfung, Requestbudget, Retry-/429-/CAPTCHA-Sperren, Parallelitätsschutz und
Verlustkontrolle. Keine direkte Veröffentlichung aus der archivierten Testantwort.
Der anschließende Runtime-Config-Rollout nutzt die vorhandenen Azure-Prüfungen.
Keine Änderungen an Startfreigaben, PARK_ONLY, Mäher-/Beregnungslogik oder Appack-HTML.

Tests: Originalausschnitt des fehlgeschlagenen Abrufs, vor-/zurückverlegte Termine,
mehrfache Verlegungen, widersprüchliche Identitäten, fehlendes Ziel, Zyklen,
Desktop-/Mobilduplikate, fehlende Festivalgruppen, Jahrgangszuordnung, Fremdvereine,
Rasen/Kunstrasen, 90-Minuten-Regeln, getrennte Puffer und vollständige Azure-Aufbereitung.
