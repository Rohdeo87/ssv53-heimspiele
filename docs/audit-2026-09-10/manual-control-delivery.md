# Manuelle Bedienung: Umsetzung und Prüfnachweise

Stand: 10. September 2026. Dieser Bericht beschreibt die neue Entwicklung.
Produktionsbetrieb und lokale Prüfungen bleiben getrennt.

## Verbindliche Regeln

Die [Nutzerentscheidungen](manual-override-policy.md) sind umgesetzt: manuelles
Parken bis zur ausdrücklichen Freigabe; manueller Start bis zur beobachteten
nächsten Rückfahrt zur Station; Training und Spiele nur nach Bestätigung des
tatsächlich freien Platzes; eine Ausnahme von der Trocknungszeit nur nach
Prüfung vor Ort. Für jeden Wasserkonflikt ist eine eigene Wahl erforderlich.
Ein neuer Wasserlauf oder eine geänderte Belegung übernimmt keine alte Ausnahme.

Husqvarna- und Geräteaktionen werden vorher auf der Platzpflegeseite bestätigt.
Eine Zustandsänderung allein beweist nicht, wer sie ausgelöst hat. Insbesondere
ist ein erneuter gleicher Parkbefehl im bestehenden Parkzustand nicht aus der
Statusänderung erkennbar. Nicht bestätigte externe Starts lösen eine gesetzte
manuelle Parksperre nicht auf.

## Tatsächliche Änderungen

| Bereich | Änderung | Nachweis |
| --- | --- | --- |
| Bedienauftrag | Dauerhafte Sitzung, Zielgerät und fortlaufende Generation; Zustimmung an vollständige aktuelle Belegung und konkreten Wasserlauf gebunden. | `mower/manual_session.py`, `mower/manual_control_api.py` |
| App und Backend | Authentifizierter Vertrag 3 mit getrennten Start-, Park-, Freigabe- und Konfliktaktionen; alte App-Versionen können weiter schützend parken. | `platzwart_console.py`, `function_app.py`, Appack-Template |
| Gerätebefehle | Speicherung vor Versand; erneute Prüfung nach Anmeldung; ungeklärte Ergebnisse werden nicht wiederholt. | `mower/device_send_guard.py`, `mower/start_dispatch_guard.py` |
| Wasserwahl | Schutzparken, Stopp der eindeutigen laufenden Zone, einzeln bestätigtes Aussetzen des ursprünglichen Plans; Wasserende und Trocknung bleiben getrennte Nachweise. | `mower/manual_water_conflict.py`, `mower/full_failsafe.py` |
| Schnitthöhe im Vollbetrieb | Eine angenommene Anfrage gilt erst nach späterem passenden Gerätewert als ausgeführt. Alte oder ungeklärte Aufträge verhindern widersprüchliche neue Höhenänderungen. | `mower/full_height_control.py` |
| Anzeige | Manuelle Freigabe, Parkanforderung und Stationsmeldung getrennt; keine falsche Belegungswarnung für genau die bestätigte Ausnahme. Parken bleibt erreichbar. | [Mobile Vorschau](manual-control-preview/appack-preview-mobile.html) |

`ENABLE_MANUAL_SESSIONS` ist standardmäßig **aus**. Das Hinzufügen des Codes
aktiviert weder den Vollbetrieb noch reale Gerätebefehle.

## Durchgeführte Prüfungen

- Vollständiger Python-Lauf: **1.337 Tests und 449 Untertests bestanden**.
  [Protokoll](manual-session-tests-python.txt), [JUnit](manual-session-tests.xml).
- Appack: **135 Tests bestanden**. [Protokoll](manual-session-tests-appack.txt).
- Tatsächliche Browserdarstellung: 9 Kombinationen aus drei Zuständen und
  320/390/1120 Pixeln, keine JavaScript-Fehler und kein horizontaler Überlauf.
  [Browserprotokoll](manual-control-preview/browser-check.json).
- [Bestätigungsdialog auf Mobilgeräten](manual-control-preview/confirmation-390.png),
  [manueller Mähbetrieb](manual-control-preview/moving-390.png),
  [Parkzustand](manual-control-preview/park-390.png).

Die Geräteantworten dieser Tests sind simuliert. Netzwerkzugriffe sind in den
Python-Tests gesperrt; die Vorschau sperrt Netzwerkzugriffe und Geräteaktionen.
Die Browserbilder sind keine Abnahme der installierten Vereins-App.

Die Tests decken insbesondere API-Auftrag bis zum Steuerungszyklus,
Start → gemeldete Ausfahrt → Rückfahrt, dauerhaftes Parken, Freigabe,
unbestätigten externen Start während manueller Parksperre, Änderungen während
der Anmeldung, verlorene Antworten, Neustart mit gespeichertem Auftrag,
Wasserstopp und ursprünglichen Bewässerungsplan, neue Konflikte sowie
Schnitthöhe 26 mm mit späterer Rückmeldung ab.

## Unabhängiges Abschlussreview

Das Abschlussreview fand eine Bestätigungslücke: Ein vor dem Versand
gecachter Hydrawise-Zustand konnte als spätere Stopp-/Aussetzbestätigung
zählen. Die Korrektur verlangt jetzt eine Herstellerbeobachtung strikt nach
dem Versand, den vollständigen erwarteten Zonensatz und den passenden
bestätigten Journaleintrag. Ein eigener Fehlerfall hält bei alten und exakt
gleichzeitigen Meldungen an; der vollständige Ablauf verwendet tatsächlich
fortschreitende Herstellerzeiten.

Ein zweiter Verdacht betraf den Übergang einer laufenden manuellen Sitzung
in den Zehn-Minuten-Vorlauf einer späteren Bewässerung. Der gezielte
Übergangstest belegt das bereits vorhandene Schutzparken über die erfasste
Bewässerungsplanung. Beide Punkte wurden nach der Korrektur unabhängig
nachgeprüft; in diesen zwei untersuchten Pfaden blieb kein offener Blocker.
Das ist kein umfassender physischer Sicherheitsnachweis.

## Restrisiken und Freigabe

### Belegter Aktualitätskonflikt im Stationsbetrieb

Die [bereinigte Betriebsstichprobe](manual-idle-telemetry-sample.json) enthält
179 Steuerungszyklen vom 10.09.2026, 07:13–10:11 Uhr Ortszeit. In 124 Zyklen
war der letzte Hersteller-Zeitstempel älter als 180 Sekunden. Es gab 26
verschiedene Geräte-Zeitstempel; mehrere aufeinanderfolgende Aktualisierungen
im ruhenden Zustand lagen genau 930 Sekunden auseinander. Das größte
beobachtete Meldungsalter betrug 928,3 Sekunden. Die Stichprobe enthält
Stations-, Rückfahrt- und `NOT_APPLICABLE`-Meldungen, keine produktive Mähzeit.

Damit ist ein Widerspruch zwischen der bisherigen Drei-Minuten-Grenze und
den tatsächlich gelieferten ruhenden Gerätemeldungen belegt. Dies erklärt
einen Teil der fehlenden Statusfreigaben; es beweist keinen Geräteausfall.
Die [Herstellerbeschreibung von `metadata.statusTimestamp`](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/swagger.yml)
bezeichnet den Zeitpunkt des letzten Statusupdates, gibt aber keine
verbindliche maximale Verzögerung physischer Zustandsänderungen an.

Die Grenze wurde deshalb nicht stillschweigend verlängert. Vor dem Pilot ist
die Aktualität bei Ausfahrt, Rückfahrt und Kommunikationsverlust zu prüfen.
Bis dahin bleibt dies eine ausdrückliche betriebliche Prüflücke: Ein alter
Stationswert kann einen manuellen Start vorübergehend blockieren, auch wenn
die Verbindung als vorhanden gemeldet wird. Schutzparken bleibt erreichbar.

| Risiko | Erkennung und Begrenzung | Noch erforderlicher Nachweis |
| --- | --- | --- |
| Ein externer Start erreicht den Mäher vor der zentralen Abfrage. | App-Vorbereitung ist verbindlich; Schutzparken bei tatsächlichem Konflikt; keine zentrale Freigabe bei unbekanntem Wasser. | Beaufsichtigter Geräteablauf mit dokumentierter Reaktionszeit. |
| Geräte- oder Cloudmeldung bleibt alt. | Unbekannt bleibt unbekannt; kein längeres Vertrauen allein wegen eines erfolgreichen GET. Parken bleibt bedienbar. | Aktualität im realen Stations-, Lade- und Mähbetrieb über den gesamten Ablauf messen. |
| Hydrawise bestätigt eine Anfrage, aber nicht den physischen Ventilzustand. | Spätere Zonenbeobachtung, kein Start während ungeklärtem Ergebnis, Eskalation statt Wiederholung. | Reale Übereinstimmung der sieben Zonen mit der Anlage; Sichtprüfung während Pilot. |
| Eine Anfrage kommt verspätet an. | Persistente Versandreservierung, Prüfung direkt vor HTTP, keine automatische Wiederholung ungeklärter gefährlicher Aktionen. | Restrisiko der nicht vollständig nachweisbaren Geräte-/Cloudwarteschlange bleibt; kein Code-Lock kann bereits versendete Aktionen zurückrufen. |
| Bewässerung wird zugunsten des Mähers unterbrochen. | Ursprünglicher Plan bleibt gespeichert; unterbrochener Lauf wird nicht als erfüllter Wasserbedarf gezählt. | Nachholbedarf im zulässigen Zeitfenster fachlich prüfen; keine automatische Zusatzbewässerung nur wegen Ladens. |

Die vollständige Automatik wurde in dieser Entwicklung noch **nicht live
aktiviert**. Der getrennte letzte Produktions-Read um 09:39 Uhr meldete
`OPERATOR_ONLY`, keinen gesendeten Befehl, Stationsstatus `HOME`, Akku 100 %
und keine aktive Bewässerungszone. Das ist eine Gerätemeldung, keine neue
Vor-Ort-Abnahme. Die zuletzt installierten Bytes stehen weiterhin im
[Installationsnachweis](calendar-final-source-parity.json).

Vor dem Livepilot müssen das konkrete neue Paket und seine Installation
nachgewiesen, alte/geräteeigene Zeitpläne abgeglichen und aktuelle Geräte- sowie
Platzverhältnisse bestätigt sein. Erst der anschließend beobachtete Ablauf
liefert einen Live-Nachweis. Ein Rückfall muss Geräteaufträge und geräteeigene
Zeitpläne einschließen; das Zurücksetzen von Code allein genügt nicht.

## Konkretes Quellpaket

Die [Paketprüfung](manual-control-package-verification.json) bestätigt alle 67
Quelldateien bytegenau gegen Commit `6d32a2c66a89ad0ca012b92fed7af8417d4f5175`.
Das Paket enthält zusätzlich sein Manifest; alle 15 Azure-Funktionen wurden
ohne Netzwerkzugriff importiert. [Paketpfad und Hash](manual-control-package.json).
Diese Prüfung belegt keinen Azure-Remote-Build und keine Installation.
