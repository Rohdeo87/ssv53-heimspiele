# SSV53 – FUSSBALL.DE-Platzbelegung PoC Version 10

Version 10 behebt die beiden in `dfbnet-diagnose-5.zip` nachgewiesenen Probleme:

1. Der bisherige Saisonabruf lieferte pro Mannschaft exakt zehn Tabellenzeilen und war damit sehr wahrscheinlich gekürzt.
2. Auswärtige Spielstätten wurden unnötig als „zu prüfen“ behandelt, obwohl sie für den Schönwalder Belegungsplan sicher ausgeschlossen werden können.

## Neue Abruflogik

Die Saison wird je Mannschaft in drei nicht überlappende Zeitfenster aufgeteilt:

- 01.07.2026–31.10.2026
- 01.11.2026–28.02.2027
- 01.03.2027–30.06.2027

Bei drei Mannschaften entstehen damit neun streng nacheinander ausgeführte Requests pro Lauf. Das bleibt unter der unveränderlichen Obergrenze von zehn Requests.

Enthält ein einzelnes Zeitfenster zehn oder mehr Spielzeilen, wird der Feed vorsorglich nicht veröffentlicht, weil die Antwort erneut gekürzt sein könnte.

## Platzlogik

- Sportplatz Schönwalde Strandbad, Platz 1 → `Rasen`
- Sportplatz Schönwalde Strandbad, Platz 2 / Kunstrasen / KR → `Kunstrasen`
- andere vollständig benannte Spielstätte → ausgeschlossen
- fehlende Spielstätte → Prüfung
- Schönwalder Strandbad ohne eindeutige Platznummer → Prüfung
- spielfrei → ausgeschlossen

Die formale Heim-/Gastrolle ist nicht entscheidend. Ein formal als Auswärtsspiel geführtes Spiel wird übernommen, wenn es tatsächlich auf Platz 1 oder Platz 2 am Strandbad angesetzt ist.

## Vollständigkeitsprüfung

Es gibt keine fest eingebaute Mindestzahl von 17 Spielen mehr. Stattdessen wird strukturell geprüft:

- alle drei Zeitfenster wurden pro Mannschaft verarbeitet,
- kein Zeitfenster erreicht die vermutete Zehn-Zeilen-Grenze,
- jeder vorhandene Spiel-Link wurde geparst,
- keine Spiel-ID ist innerhalb einer Antwort doppelt,
- alle aufzunehmenden Spiele besitzen Datum, Uhrzeit, Gegner, Spiel-ID und Spielstätte,
- kein lokaler Platz bleibt ungeklärt.

Bei einem Fehler bleibt der bisher veröffentlichte Feed unverändert.

## Schutzmaßnahmen für FUSSBALL.DE

- Abrufe nur zwischen 06:00 und 22:00 Uhr Europe/Berlin
- zufällige Verzögerung von 2–12 Minuten
- mindestens 3 Sekunden Abstand zwischen Requests
- maximal 10 Requests je Lauf
- maximal ein Retry nur bei Timeout oder 502/503/504
- sofortiger Abbruch und dauerhafte Sperre bei 403, 406 oder Challenge-Seite
- Sperre bei 429 gemäß `Retry-After`
- keine parallelen Läufe
- letzter erfolgreicher Feed bleibt bei jedem Fehler erhalten

Bei vier Läufen pro Tag entstehen im Normalfall 36 Requests pro Tag.

## Installation

1. ZIP vollständig entpacken.
2. Den gesamten Inhalt in den lokalen Ordner `ssv53-heimspiele` kopieren.
3. Vorhandene Dateien ersetzen.
4. GitHub Desktop öffnen.
5. Falls **Pull origin** angezeigt wird, zuerst ziehen und bei einem Konflikt in `state/request_state.json` die Version von GitHub behalten.
6. Summary eintragen: `Vollständiger Zeitfensterabruf Version 10`
7. **Commit to main** anklicken.
8. **Push origin** anklicken.
9. Im Browser unter **Actions → SSV53 Heimspiele aktualisieren → Run workflow** einmal manuell starten.

## Ergebnis prüfen

Nach einem grünen Lauf:

1. `public/summary.json` öffnen.
2. `review` muss `0` sein.
3. Unter `team_audits` müssen pro Mannschaft drei Fenster stehen.
4. Kein Fenster darf zehn Spielzeilen enthalten.
5. Die tatsächliche Zahl bei `by_calendar.Rasen` ist das Ergebnis des vollständigen Abrufs und keine vorgegebene Mindestzahl.

Bei einem roten Lauf das neue Artefakt `dfbnet-diagnose-...` herunterladen und unverändert zur Analyse bereitstellen.
