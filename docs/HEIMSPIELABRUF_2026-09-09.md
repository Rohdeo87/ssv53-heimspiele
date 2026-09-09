# Heimspielabruf: Verlegung über Quartalsgrenzen

## Belegter Ausfall

Der [Importlauf 34312723317](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34312723317)
brach am 09.09.2026 um 04:57:03 UTC trotz HTTP 200 ab:
`Verlegung 031MFGE2PG000000VS5489BUVVKNMR0J: Ziel fehlt oder zyklische Verlegung`.
Der letzte veröffentlichte Quellenstand blieb dadurch auf 08.09.2026 04:51:22 UTC.

Der Vereinsspielplan enthält die alte C-Junioren-Paarung vom 12.09.2026 09:00 Uhr
mit einem ausdrücklichen Verweis auf 08.10.2026 18:00 Uhr. Der Abruf verwendet
Quartalsfenster. Die Septemberzeile lag im ersten, die gültige Zielzeile im zweiten
Fenster. Der Parser versuchte die Kette bereits nach dem ersten Fenster aufzulösen
und brach vor dem Abruf der Zielzeile ab.

Die Verlegung betrifft auch den Platz: bisher Kunstrasen in Dallgow (ausgeschlossen),
jetzt Rasen Platz 1 in Schönwalde mit getauschtem Heimrecht. Das ausgefallene Update
verhinderte daher die Aufnahme einer zusätzlichen lokalen Belegung am 08.10.,
**17:00–20:25 Uhr** einschließlich unverändertem Vor-/Nachlauf von jeweils 60 Minuten.

## Änderung und Sicherheitsgrenzen

Der Saisonimport prüft zunächst die Rohzeilen und Vollständigkeit jedes abgerufenen
Zeitfensters. Erst nach Sammlung aller akzeptierten Fenster werden Duplikate und
explizite Verlegungsketten gemeinsam aufgelöst. Ein eigenständiger Einzelantwort-
Parser bleibt weiterhin streng und lehnt ein darin fehlendes Verlegungsziel ab.

Die Auflösung benötigt weiterhin dieselbe Spiel-ID, Spielnummer, beide eindeutigen
Mannschafts-IDs, einen eindeutigen Endknoten und dessen tatsächliche Spielstätte.
Fehlendes Ziel, widersprüchliche Identität und Zyklen bleiben Fehler. Es werden
keine Termine geraten und keine zusätzlichen Quellenrequests benötigt. Festival-
Zuordnung, Platzentscheidung und Prüfsumme entstehen nach der abschließenden
Auflösung. Erkennt der Parser vorhandene Spiel-/Festivallinks bei geänderten
Zeilenklassen, wird dies als Formatfehler behandelt, nicht als leerer Spielplan.

Vorhandene Abrufbudgets, Schutzpuffer und zweistufige Bestätigung von
sicherheitsrelevanten Rücknahmen bleiben unverändert. Änderungen an Azure-,
Mäher-, Bewässerungs-, App- oder Trainingslogik sind nicht Teil dieses Fixes.

## Nachweise

Der unabhängige Auditabruf der unveränderten Quelle reproduzierte den Fehler am
09.09.2026 um 05:52:40 UTC im konfigurierten Q3-Fenster. Die korrigierte Verarbeitung
der vier Saisonfenster um 05:56:01–05:56:12 UTC ergab 113 eindeutige Datensätze,
46 lokale Belegungen (41 Rasen, 5 Kunstrasen), 0 Review. Zum veröffentlichten Stand
gab es genau eine zusätzliche lokale Belegung: das genannte C-Spiel.

Die identischen vier erfassten HTML-Antworten wurden auf diesem separaten Main-Fix
zusätzlich offline mit der unveränderten Main-Konfiguration wiederholt:
113 Datensätze, 46 lokale Belegungen, keine Netzwerkabfrage oder Veröffentlichung.
Die Rohantworten liegen nur im lokalen, ignorierten Auditverzeichnis.

Getestet auf Main-Basis `d67e9d3fef24117f504a192c9ecaee998612ddd7` mit Python 3.12.14:

- Baseline: 112 pytest-Tests und 192 Subtests bestanden.
- Mit Fix: **119 pytest-Tests und 192 Subtests bestanden**.
- Sieben zusätzliche Offline-Regressionen: fensterübergreifende Verlegung,
  fehlendes Ziel, falsche Mannschaft, falsche Spielnummer, fehlende Zielstätte,
  weiter strenger Einzelantwort-Parser und geänderte Quell-Zeilenklassen.
- `python -m compileall -q .` und `git diff --check` erfolgreich.

Der PR-Testworkflow verwendet nun `pytest` mit gepinnten Testabhängigkeiten aus
`requirements-test.txt`. Damit werden sowohl vorhandene unittest-Testklassen als
auch pytest-Testfunktionen ausgeführt; das bisherige `unittest discover` führte
die neuen Funktionsregressionen nicht aus.

## Release-Abnahme

Nach menschlicher Freigabe des Fixes einen vollständigen Import nachweisen und
die C-Belegung in veröffentlichtem JSON und Rasen-ICS prüfen. Anschließend separat
das Runtime-Manifest und die Belegungs-API auf denselben Quellenstand prüfen.
Die hier genannten Counts sind ein zeitgebundener Vergleich; spätere Abweichungen
müssen anhand der Quelle erklärt werden. Ein erfolgreicher Datenimport beweist
keinen Rollout der Geräte-Steuerungssoftware. Dieser Fix wurde im Audit nicht
produktiv veröffentlicht und löste keine Gerätebefehle aus.
