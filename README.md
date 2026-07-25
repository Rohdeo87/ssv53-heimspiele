# SSV53 – FUSSBALL.DE-Platzbelegung PoC Version 11

Version 11 basiert auf Version 10 und behebt den in `dfbnet-diagnose-8.zip` nachgewiesenen Restfehler: Der Abruf war inhaltlich plausibel, aber zwei Herren-Zeitfenster enthielten exakt zehn Tabellenzeilen. Die Qualitätskontrolle stufte diese Antworten deshalb vorsorglich als möglicherweise gekürzt ein und veröffentlichte den Feed nicht.

## Ergebnis der Diagnose 8

- 57 Datensätze verarbeitet
- 21 Spiele sicher auf `Sportplatz Schönwalde Strandbad, Platz 1`
- 0 Spiele zur manuellen Platzprüfung
- 36 Spiele korrekt ausgeschlossen
- keine fehlgeschlagenen Mannschaften
- keine fehlenden oder doppelten Spiel-IDs innerhalb der Antworten
- Veröffentlichung nur wegen zwei Antworten mit jeweils zehn Tabellenzeilen blockiert

## Neue Abruffenster

Die Zeitfenster sind nun abhängig vom Spielaufkommen der Mannschaft:

### Herren Ü50 – 2 Requests

- 01.07.2026–31.12.2026
- 01.01.2027–30.06.2027

### Herren Ü40 – 2 Requests

- 01.07.2026–31.12.2026
- 01.01.2027–30.06.2027

### Herren – 5 Requests

- 01.07.2026–31.08.2026
- 01.09.2026–31.10.2026
- 01.11.2026–28.02.2027
- 01.03.2027–30.04.2027
- 01.05.2027–30.06.2027

Damit werden weiterhin nur neun Requests pro Lauf ausgeführt. Die anhand der Diagnose 8 rekonstruierten Zeilenzahlen liegen in allen neuen Fenstern klar unter der Zehn-Zeilen-Grenze.

## Platzlogik

- Sportplatz Schönwalde Strandbad, Platz 1 → `Rasen`
- Sportplatz Schönwalde Strandbad, Platz 2 / Kunstrasen / KR → `Kunstrasen`
- Sportplatz Perwenitz oder Paaren → ausgeschlossen
- andere eindeutig fremde Spielstätte → ausgeschlossen
- fehlende Spielstätte → Prüfung
- Strandbad ohne eindeutige Platznummer → Prüfung
- spielfrei → ausgeschlossen

Die formale Heim-/Gastrolle ist nicht entscheidend; maßgeblich ist ausschließlich die tatsächliche Spielstätte.

## Vollständigkeitsprüfung

Der Feed wird nur veröffentlicht, wenn:

- alle konfigurierten Zeitfenster verarbeitet wurden,
- kein Zeitfenster zehn oder mehr Tabellenzeilen enthält,
- alle vorhandenen Spiel-Links verarbeitet wurden,
- keine Spiel-ID innerhalb einer Antwort doppelt ist,
- alle aufzunehmenden Spiele vollständig sind,
- kein lokales Spiel ungeklärt bleibt.

Es gibt keine starre Mindestzahl an Spielen.

## Schutzmaßnahmen

- Abrufe nur zwischen 06:00 und 22:00 Uhr Europe/Berlin
- zufällige Verzögerung von 2–12 Minuten
- mindestens 3 Sekunden Abstand zwischen Requests
- maximal 10 Requests je Lauf
- maximal ein Retry nur bei Timeout oder 502/503/504
- sofortiger Abbruch und dauerhafte Sperre bei 403, 406 oder Challenge-Seite
- Sperre bei 429 gemäß `Retry-After`
- keine parallelen Läufe
- letzter erfolgreicher Feed bleibt bei Fehlern erhalten

## Installation

1. ZIP vollständig entpacken.
2. Den Inhalt des Ordners `poc_v11` in das lokale Repository `ssv53-heimspiele` kopieren.
3. Vorhandene Dateien ersetzen.
4. In GitHub Desktop zuerst **Pull origin**, falls angeboten.
5. Bei einem Konflikt in `state/request_state.json` die Version von `main/origin` behalten; bei den Version-11-Dateien die lokale Version von `main` behalten.
6. Commit-Text: `Teambezogene Zeitfenster Version 11`
7. **Commit to main** und danach **Push origin**.
8. Unter **Actions → SSV53 Heimspiele aktualisieren → Run workflow** einmal manuell starten.

## Ergebnis prüfen

Ein erfolgreicher Lauf sollte `publishable: true`, `review: 0` und voraussichtlich 21 Rasen-Spiele ausweisen. Die Zahl 21 ist das Ergebnis der Diagnose 8, keine fest eingebaute Vorgabe.
