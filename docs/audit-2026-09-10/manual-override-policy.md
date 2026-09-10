# Manuelle Bedienung und Vorrang vor der Automatik

Stand: 10.09.2026, Untersuchung ab 08:23 Uhr Europe/Berlin.
**Entscheidungsentwurf, noch keine freigegebene neue Betriebsregel.**

## Geprüfter Ausgangspunkt

- Entwicklungsbasis: `356ad2bd3234b288cb4dae9eb719c13ead9c0594`
  auf `feature/azure-mower-migration`; neuer Arbeitsbranch
  `feature/manual-override-policy-20260910`.
- Installierter Paketmanifest-Hash:
  `298313e2008280b522ee8f5d07fdfd3fd2b06155345398f3cc6bc39fbf894458`.
  Der getrennte [Installationsnachweis](calendar-final-source-parity.json)
  beschreibt Quellcode und tatsächlich installierte Dateien.
- Produktionsmodus zuletzt am 10.09.2026 um 08:18 Uhr ausgelesen:
  `OPERATOR_ONLY`, Schutzparken aktiv, automatische Mäherstarts und
  Bewässerungsbefehle aus, gemeinsame Bewässerungsausführung aus.
  Die vollständige Automatik ist damit nicht eingeschaltet.
- Das Bedienjournal in `mower/operator_controls.py` verwaltet Parken und
  Schnitthöhe. Ein dauerhafter, ausdrücklich freigegebener manueller Start
  mit eigener Gültigkeit und Rückkehrregel ist dort bisher nicht vorhanden.
  Die umfangreicheren Startaktionen im separaten `FULL_FAILSAFE`-Pfad ersetzen
  diese fehlende gemeinsame Vorrangregel nicht.
- Der tatsächliche App-Renderpfad verwendet `effectiveMowerActions()` in
  `appack-platzwart-dashboard.html`. Bei gültiger Parkberechtigung bleibt
  Parken auch beim Laden sichtbar. Die Beschränkung eines älteren Fallbacks
  darf nicht mit dem aktuellen Bedienverhalten verwechselt werden.

## Beobachteter manueller Start und Schutzauftrag

Die [bereinigten regulären Produktionsbeobachtungen](manual-park-observation.json)
enthalten keine nachgebildeten Geräteantworten. Sie sind trotzdem keine
kontrollierte Abnahme: Ein Nutzerstart und eine bereits aktive Schutzsteuerung
waren beteiligt. Für diese Untersuchung wurde kein direkter Geräte-Testbefehl
gesendet.

| Zeit am 10.09.2026 | Beobachtung | Belastbare Aussage |
| --- | --- | --- |
| 08:18 Uhr | Schutzsteuerung meldet einen gesendeten Parkauftrag; Trocknungssperre aktiv. | Die bestehende Steuerung behandelt den externen Start nicht als Freigabe der Trocknungssperre. |
| 08:27–08:31 Uhr | `PARKED_IN_CS`, Modus `HOME`, zugleich weiterhin `FORCE_MOW`. | Stationsaufenthalt wird gemeldet; dauerhaftes Halten ist damit noch nicht nachgewiesen. |
| 08:29–08:31 Uhr | Auftrag `UNKNOWN`, Grund `CONFIRMATION_TIMEOUT`; keine Wiederholung. | Die strenge Parkbestätigung wurde innerhalb der Frist nicht erfüllt. |
| 08:31 Uhr | Alle sieben erwarteten Bewässerungszonen aktuell inaktiv; frische Daten. | Zu diesem Zeitpunkt keine laufende Bewässerung laut Hydrawise. |
| 09:40 Uhr | Bestehendes Ende der Trocknungssperre, aus beobachtetem Wasserende 07:10 Uhr. | Keine durch diese Untersuchung vorgenommene Verkürzung der 150 Minuten. |

Die Ursache des fortbestehenden Planner-Felds `FORCE_MOW` ist noch offen.
Die bisher ausbleibende Bestätigung hat jedoch eine reproduzierbare Ursache
in unserem Code: `_fresh_station_confirmation()` und `_station_held()` verlangen
`FORCE_PARK`, aber prüfen nicht den für diesen Befehl dokumentierten Modus.
Die aktuelle [Husqvarna-Spezifikation](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/swagger.yml)
(am 10.09.2026 erneut abgerufen) beschreibt für `ParkUntilFurtherNotice` den
Modus `HOME`. Das separate Planner-Feld `FORCE_PARK` beschreibt dagegen ein
Parken bis zum nächsten Zeitplaneintrag. Es ist kein Dauerparknachweis.

Die Korrektur im Entwicklungsstand verlangt deshalb den gemeldeten
Stationsaufenthalt **und** Modus `HOME` am exakten verbundenen Gerät.
Eine Auftragsbestätigung benötigt zusätzlich einen Statusbericht nach dem
Sendezeitpunkt. `FORCE_PARK` ohne `HOME` darf nicht mehr genügen. Ein weiterhin
vorhandenes Planner-Feld ersetzt den dokumentierten Betriebsmodus nicht.
Die Spezifikation bezeichnet `statusTimestamp` ausdrücklich als Zeitpunkt
eines Backend-Updates. Diese Bestätigung beschreibt daher den von der
Hersteller-Cloud gemeldeten Zustand; sie ist keine lokale unabhängige Messung
und keine Garantie gegen einen späteren externen Start.

Weitere für die Einführung relevante Prüfpunkte:

- Der bisher installierte `input_failure_guard.py` leitet aus `FORCE_PARK` zusammen mit
  `nextStartTimestamp=0` einen unbefristeten Halt ab. Laut derselben
  Spezifikation bedeutet der Zahlenwert `0` einen aktuell fälligen Start.
  Der Entwicklungsstand verwendet stattdessen ausschließlich den gemeldeten
  Modus `HOME`. Ohne diesen Modus bleibt der Halt unbekannt und wird eskaliert;
  Start-, Bewässerungs- und Sendefunktionen wurden dabei nicht erweitert.
- `_park_confirmation_ready()` in `full_failsafe.py` prüft Stationsaktivität
  und gespeicherte Bestätigungen, aber bisher nicht den gemeldeten
  Dauerparkmodus. Vor einem Wasserstart beziehungsweise der Wiederaufnahme
  des geräteeigenen Wasserplans muss auch die Startsperre des Mähers gelten.
  Die Behandlung eines Moduswechsels während bereits laufender Bewässerung
  muss zur noch festzulegenden manuellen Konfliktregel passen. Sie darf nicht
  die notwendige Erkennung des tatsächlichen Wasserendes verhindern.

### Bisheriger Entwicklungsnachweis

Die korrigierte Bedien- und Schutzparkerkennung wurde zunächst mit 82 gezielten Tests
geprüft (`test_operator_controls.py`, `test_operator_park_replay.py`,
`test_platzwart_operator_contract.py`). Alle sind erfolgreich.
Die Wiederholung verwendet die hier abgelegten tatsächlichen Statusmeldungen
mit anonymisierter Gerätekennung, einen neuen nachgebildeten Speicher und
einen nachgebildeten Sender. Das Testnetzwerk ist gesperrt. Genau ein Befehl
wird nachgebildet; die späteren Stationsmeldungen bestätigen ihn ohne
Wiederholung. Dieser Unterschied zur historischen Produktion belegt die
korrigierte Auswertung, keine erneut beobachtete Gerätefahrt.

Nach Integration der Korrektur im Eingabedaten-Ausfallschutz sind die
[gesamte Python-Suite](manual-park-tests-python.txt) mit **1.255 Tests und
449 Subtests** sowie die [Appack-Suite](manual-park-tests-appack.txt) mit
**127 Tests** erfolgreich. Die beiden abgegrenzten Korrekturen wurden von
separaten Agenten bearbeitet, durch die Hauptinstanz geprüft und anschließend
gemeinsam getestet. Der Python-Testlauf sperrt Netzwerkzugriffe standardmäßig.

Diese Änderungen sind noch nicht in Produktion installiert. Auch die
neue manuelle Vorrangregel ist noch nicht umgesetzt. Die folgenden offenen
Entscheidungen sind dafür verbindliche Voraussetzungen.

## Offene Entscheidungen des Betreibers

Die erste Antwort des Betreibers am 10.09.2026 ist übernommen:

- Ein manueller Start darf Training und Spiele **nach ausdrücklicher
  Bestätigung** übergehen. Daraus folgt keine Freigabe verbindlicher
  Platzsperren und keine stillschweigende Freigabe unbekannter Belegung.
- Bei jedem Bewässerungskonflikt soll der Betreiber wählen; es gibt keine
  pauschale dauerhafte Bevorzugung von Mäher oder Bewässerung.
- Ein manueller Start gilt bis zur nächsten Ladefahrt.
- Eingriffe über Husqvarna sollen denselben Vorrang wie App-Eingriffe haben.
  Fehlende Informationen der Geräteschnittstelle dürfen dabei nicht durch
  eine erfundene Bestätigung ersetzt werden.

Nachfragen zu Parkdauer, Trocknung und Bestätigung externer Konfliktstarts
sind noch offen. Die folgende Tabelle dokumentiert diese Entscheidungsgrenzen.

| Entscheidung | Zu klärende Möglichkeiten | Ohne Entscheidung |
| --- | --- | --- |
| Umfang eines bewussten manuellen Starts | Training und Spiele sind ausdrücklich bestätigbar; Behandlung der Trocknungszeit noch offen. | Bestehende Trocknungsregel bleibt unverändert. |
| Vorrang bei notwendiger oder laufender Bewässerung | Entscheidung je Konflikt ist vereinbart. | Keine Freigabe ohne die zum Konflikt gehörende Entscheidung. |
| Dauer eines manuellen Starts | Bis zur nächsten Ladefahrt vereinbart. | Keine Verlängerung über weitere Ladezyklen. |
| Dauer eines manuellen Parkens | Bis zur ausdrücklichen Freigabe oder bis zu einer gewählten Zeit. | Keine automatische Aufhebung einer Stoppsperre. |
| Bedienung über Husqvarna-App oder am Gerät | Gleicher Vorrang vereinbart; zusätzliche ausdrückliche Konfliktbestätigung vorab oder nach Erkennung noch zu entscheiden. | Gerätezustand wird angezeigt, aber keine vermeintlich bekannte Bestätigung oder Person behauptet. |

Eine manuelle Aktion außerhalb unserer App erreicht das Gerät vor der
zentralen Prüfung. Die regelmäßige Abfrage kann deshalb keinen garantiert
gleichzeitigen Bewässerungsstopp vor der ersten Mäherbewegung herstellen.
Dieser Unterschied muss im vereinbarten Bedienablauf berücksichtigt werden.

## Abhängig von den Antworten vorzubereitende Umsetzung

Eine gemeinsame, dauerhaft gespeicherte Entscheidung für Bedienauftrag,
Gültigkeit, Zielgerät, Auftragsgeneration und Rückkehr zur Automatik muss vor
jedem automatischen und manuellen Gerätebefehl geprüft werden. Reservierung,
unbekannter Befehlsausgang, Neustart und konkurrierende Instanzen müssen
dieselbe Entscheidung erhalten. Ein Parkauftrag muss einen noch nicht
gesendeten Start ablösen können; eine ausdrücklich gesetzte Pause darf keine
Bewegung auslösen. Ein abgelaufener Lock beweist nicht, dass ein alter Befehl
nicht mehr wirksam werden kann.

Die App soll das Ergebnis in einfacher Sprache zeigen, etwa „Manuell gestartet
bis 10:00 Uhr“, „Manuell geparkt“ oder „Start wartet: Bewässerung läuft“.
Diese Texte sind Beispiele, keine vorweggenommene Entscheidung zur Gültigkeit.
Angefordert, bestätigt und unbekannt müssen weiterhin unterscheidbar sein.

Gezielt abzusichern sind mindestens: Neustart während eines Auftrags; zwei
Instanzen; verlorene Antwort; verspäteter Start nach Parkauftrag; Ablauf einer
manuellen Freigabe; Pause und Stopp; belegter Platz; aktive, unbekannte und
mehrere Bewässerungszonen; geräteeigener Zeitplan; externer Start ohne bekannte
Urheberschaft. Die Tests verwenden nachgebildete Sender und keine echten
Gerätebefehle.

Vor dem vollständigen Livebetrieb müssen zusätzlich Dauerparken, die
vereinbarte manuelle Vorrangregel und die kontrollierte Zuständigkeit für den
Hydrawise-Zeitplan nachgewiesen sein. Eine Ladung allein darf keine zusätzliche
Bewässerung auslösen. Die vereinbarte Bewässerungsgrenze 03:30–08:00 Uhr und
die vollständige erforderliche Wasserversorgung bleiben erhalten.
