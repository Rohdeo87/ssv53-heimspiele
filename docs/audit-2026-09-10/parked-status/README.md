# Stationsmeldung und verständlicher Zeitplan

## Befund und Umfang

Die Nutzerbilder vom 10.09.2026 zeigen um **19:28 Uhr** eine letzte Meldung um
**19:17 Uhr**, und um **20:31 Uhr** eine letzte Meldung um **20:19 Uhr**. Beide
zeigen den letzten Stationszustand, 100 % Akku und einen unbekannten nächsten
Start. Die Warnung verdrängt die tatsächlich bekannte Trainingssperre.

[180 gelesene Betriebszyklen](observations.json), 17:34–20:33 Uhr Berliner Zeit:
Alle melden Station, verbunden und 100 % Akku. Bei **142 von 180 Abfragen** ist
der Herstellerzeitstempel älter als 180 Sekunden; das Maximum beträgt
**923,092 Sekunden**. Die regulären Abstände neuer Herstellerzeitstempel liegen
bei etwa **930 Sekunden**. Kürzere Abstände um den bereits dokumentierten
Parkbefehl um 18:56 Uhr sind ebenfalls enthalten. Das ist kein nachgewiesener
Verbindungsabbruch. Wegen gleichzeitig bestehender Platzbelegungen sind diese
142 Abfragen auch kein Nachweis verlorener produktiver Mähminuten.

Die am 10.09.2026 erneut abgerufene offizielle
[Husqvarna-Ereignisdokumentation](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/WebSocket.md)
beschreibt einen Energiespar-Timeout und Ereignisabstände von 15 Minuten.
Das [API-Schema](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/swagger.yml)
bezeichnet `statusTimestamp` als Zeitpunkt der letzten Statusaktualisierung im
Hersteller-Backend. Die aktuelle GET-Abfrage oder `connected=true` erzeugt
daraus keinen neuen physischen Stationsnachweis. Der beobachtete Rhythmus passt
zur Herstellerbeschreibung; deren genaue geräteinterne Ursache ist nicht
unabhängig gemessen.

SHA256 der frisch gelesenen Quellen:

- WebSocket.md: `7a9e21804e5c92323a9e3e2cecc092ecfe9495195e0a34c4addba6f4c8064e34`
- swagger.yml: `e391b5e9513a61ef421b99da86e62f7dd6ae6b36c24902438d27ae12b4509060`
- README.md: `bf1f94e7aa329ac33782910fe4077af7403874cb4e2e40813b09cc8689436744`

## Korrektur

Nur die Appack-Platzpflegeanzeige wird verändert. Die letzte Gerätemeldung
bleibt als **Zuletzt: In der Station** mit ihrer Uhrzeit gekennzeichnet.
Während einer verbindlichen Trainings- oder Spielbelegung darf der Zeitplan
auch zwischen den regulären Stationsmeldungen einen **bedingten zukünftigen
Start** zeigen, zum Beispiel **Heute, 22:00 Uhr** mit dem Zusatz
**Geplant · Neue Mähermeldung erforderlich.** Oben steht dann **Platz ist belegt**
mit dem konkreten Sperrende. Wiederholtes Aktualisieren wird nicht als Lösung
für den normalen Meldungsrhythmus vorgeschlagen.

Diese eng begrenzte Darstellung setzt voraus:

- bestätigten Automatikbetrieb, letzten Zustand `PARKED_IN_CS`/`HOME`, aktuell
  verbunden gemeldetes Gerät, explizit Fehlercode 0 und keine manuelle Sitzung;
- Datenalter über 180 und höchstens 1.200 Sekunden; Quellzeitstempel und das
  vom Backend berechnete Alter müssen übereinstimmen;
- aktuelle Wasserdaten mit explizit null aktiven Zonen und kein aktives Programm;
- eine derzeit gültige Belegung und einen passenden `OCCUPANCY`-Sperrgrund mit
  gleichem Ende; ein ausreichend langes zukünftiges Mähfenster;
- keine sonstige unbekannte Sperre, ungeklärte oder laufende Start-/Parkanfrage.

**1.200 Sekunden sind ausschließlich eine Anzeigegrenze.** Die bisherige
180-Sekunden-Sicherheitsprüfung, Gerätebefehle, manuelle Startfreigaben,
Bewässerung und Backendsoftware bleiben unverändert. Die Darstellung setzt
`telemetryFresh` niemals auf wahr. Nach dem Ende der Belegung entsteht aus der
alten Meldung keine Startzusage. Bei längerer Stille bleibt die Warnung bestehen.
Aktuell gemeldetes Wasser und eine zuletzt gemeldete Mähfahrt werden auch bei
älterer Mähermeldung als Konflikt mit klarer Parkaufforderung angezeigt.

## Prüfung und verbleibendes Risiko

- [143 Appack-Tests bestanden](node-tests.txt), einschließlich sechs neuer
  Testgruppen mit Grenzen 180/181/1.200/1.201 Sekunden, Nullwerten, widersprüchlichen
  Sperrzeiten, weiteren Sperrgründen, manuellen Eingriffen, Befehlsunsicherheit,
  Wasser, Mitternacht und beiden Zeitumstellungen.
- [Vier Browserprüfungen](../../ui-2026-09-10/parked-status/browser-check.json)
  auf 320/390 Pixeln: konkrete Uhrzeit und Vorbehalt, unverändert gesperrter
  manueller Start, verfügbares Parken, kein Bewässerungsstart, keine Überbreite
  oder JavaScript-Fehler. [Nachgestelltes Foto](../../ui-2026-09-10/parked-status/training-390.png),
  [ausbleibende Meldung](../../ui-2026-09-10/parked-status/missing-report-390.png).
  Die isolierten Beispiele nutzen gerundete Fotozeiten und ergänzte
  Vertragsfelder; sie sind keine echten Handy-Statusantworten. Untere Karten
  enthalten teilweise keine Beispieldaten. Kein Netz- oder Gerätezugriff.
- Luna prüfte die Abgrenzung zwischen Anzeige und Freigabe. Der unabhängige
  Sol-Review fand zusätzlich einen fehlenden Abgleich mit dem Belegungssperrgrund
  und die Umdeutung von Nullwerten zu 0. Beide Befunde wurden korrigiert und als
  Regressionen ergänzt.

Ein geplanter Zeitpunkt bleibt von einer rechtzeitigen neuen Mähermeldung und
erneuter Sicherheitsprüfung abhängig. Die Anzeige darf keine physische
Stationsbestätigung vortäuschen. Die tatsächliche Wiederaufnahme kann bei
ausbleibenden Meldungen weiter warten; diese technische Verfügbarkeitsgrenze
wird durch die UI-Korrektur nicht beseitigt. Weder ein Mähzeitgewinn noch ein
vollständiger neuer Gerätezyklus wird behauptet. Eine Lockerung der Sicherheits-
grenze allein aufgrund des Meldungsrhythmus ist nicht Bestandteil dieser Änderung.

## Veröffentlichung und Rückfall

Vor dem Speichern wird die aktuell veröffentlichte `Platzpflege.tpl` gegen den
Vorgänger-Hash `07ec7d68eecc1807e095612cc04ea7cd7f742d04110de57f3e0a54db5d934b4a`
verglichen. Nach dem Speichern müssen vollständiges Neuladen und erneutes Lesen
des richtigen Editors die Identität mit dem getesteten Template bestätigen.
Die Kalenderdatei und die bereits installierte Azure-Steuerung werden nicht
erneut veröffentlicht. Die konkrete CMS-Bestätigung wird nach dem Speichern
ergänzt; Entwicklung und Browsernachstellung allein sind kein Rolloutnachweis.

Bei Darstellungsfehlern kann genau die zuvor gesicherte CMS-Vorlage zurückgespielt
werden. Es entstehen durch diese Veröffentlichung keine neuen geplanten oder
gesendeten Geräteaktionen; kein Eingriff in Steuerungszustand oder Gerätekalender
ist erforderlich. Nach der Veröffentlichung bleibt die Nachkontrolle im echten
Appgerät gesondert offen.
