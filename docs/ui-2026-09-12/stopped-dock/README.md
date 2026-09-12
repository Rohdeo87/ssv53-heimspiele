# Manueller Stopp in der Station und Bewässerung

Stand: 12. September 2026. Umfang: Meldung fehlender Bewässerungsbuttons bei physischem STOP, Stationsnachweis und Abschluss des vorherigen Bewässerungslaufs.

## Ergebnis und tatsächlicher Stand

Ein physischer Stopp ist kein sachlicher Grund, eine Bewässerung auszuschließen, **wenn der Aufenthalt in der Station hinreichend bestätigt ist**. Der Stopp muss dafür bestehen bleiben dürfen. STOP allein beweist den Standort nicht. Bei einem Start über die zentrale Steuerung muss weiterhin unmittelbar vor dem Gerätebefehl Wasserfreiheit geprüft werden.

Die drei aktuellen Azure-Protokolle in [recent-cycles.json](recent-cycles.json) wurden nur gelesen. Neuester Zyklus: 12.09.2026, 13:12:02 Uhr Berlin:

- Mäher: `STOPPED`, Aktivität `NOT_APPLICABLE`, Modus `HOME`, verbunden, kein Fehler.
- Stationsnachweis: `INVALID`, Grund `STATION_OR_CONTROL_CHANGED`; keine erhaltenen Stationsbestätigungen.
- Hydrawise: alle sieben erwarteten Relays vollständig gelesen, keine laufende oder unmittelbar bevorstehende Zone; Trockenfreigabe erteilt.
- Alter Ablauf: `COMPLETE_HOLD`, beendet um 07:32:59 Uhr Berlin. Keine aktuelle Zone und keine Startreservierung.
- Der beobachtete Zyklus sendete keinen Befehl. Manifest der laufenden Software: `cc36ebb779787ee97d0c499da61410cf9810505fb384400e1379c3262760a89c`.

Das ist ein Nachweis der gemeldeten Zustände und laufenden Softwarekennung. Es ist **kein unabhängiger Nachweis des physischen Standorts**. Die Aussage des Nutzers, dass der Mäher in der Station steht, ist davon getrennt festgehalten; daraus wurde kein Gerätebefehl erzeugt.

## Ursachen

1. `AutomationState.record_cycle` verwirft beim Übergang nach `STOPPED/NOT_APPLICABLE` die Parkbestätigung. `irrigation_park_hold` akzeptiert diese Kombination ebenfalls nicht. `HOME` beschreibt den Parkmodus und ersetzt keine Stationsposition.
2. Die Oberfläche schließt sämtliche Wasserstarts bei STOP aus. Ihre bisherige Aufforderung, den Mäher zuerst freizugeben, war daher zu pauschal und wird in der Entwicklung entfernt.
3. `COMPLETE_HOLD` blockiert einen weiteren manuellen Bewässerungsauftrag. Dieser Abschlusszustand wird bislang unter anderem beim späteren Mähstart oder beim bestätigten nächsten automatischen Lauf entfernt. Ein physischer STOP kann den Mähstart verhindern und dadurch die unnötige Kopplung sichtbar machen.
4. Die zentrale Startprüfung ist nicht mit einem Start direkt am Gerät gleichzusetzen. Letzterer läuft nicht durch unseren Startendpunkt.

## In dieser Änderung umgesetzt

- Optionaler Abschluss des alten Bewässerungsablaufs, unabhängig von einem Mähstart: `IRRIGATION_TERMINAL_CLEANUP_ENABLED`, **standardmäßig aus**.
- Nur `COMPLETE_HOLD` ist geeignet, mit bestätigtem Ende, aktuellem vollständig gelesenem und freiem Relay-Satz. PENDING, Geräte-Startreservierung, noch laufende Zone, unbestätigte Gerätebefehle, Wartungsmodus und ein gespeicherter Bewässerungsplan-Override verhindern den Abschluss.
- Die Projektion wird zusammen mit der normalen Entscheidung über deren bestehende CAS-Speicherung übernommen. Sicherheits- und Parkentscheidungen laufen im selben Zyklus weiter.
- Trockenbeginn, Trockenfrist, manuelle Sitzungen, Park-/Stoppsperren und das Befehlsjournal bleiben erhalten. Fehlte bisher eine explizite Herkunft der Trockenfrist, wird die vorhandene Herkunft `IRRIGATION_END` ausdrücklich bewahrt; sonst könnte eine bestehende 150-Minuten-Frist auf die zweiminütige Datenbestätigung zurückfallen.
- Die Erklärung für fehlende Wasserstartbuttons lautet in der vorbereiteten Oberfläche: „Die Station ist noch nicht bestätigt. Der Mäher darf gestoppt bleiben.“ Bei nachweislich laufender Bewässerung wird diese Start-Erklärung nicht angezeigt.
- Reproduzierbares Leseskript: `scripts/read_stopped_dock_evidence.py`; es fragt ausschließlich vorhandene Azure-Protokolle ab.

**Nicht umgesetzt oder aktiviert:** neue Freigabe aus STOP ohne Stationsnachweis, Vor-Ort-Bestätigung, automatische Wasserabschaltung bei externer Abfahrt. Keine Änderungen an Produktionskonfiguration, Gerätezeitplänen oder Geräten. Die Wasserstartbuttons werden durch diese Änderung allein noch nicht freigegeben. Die vorbereitete Oberfläche ist nicht veröffentlicht.

## Zielverhalten und noch erforderliche Entscheidung

| Situation | Bewässerung | Mäherstart |
|---|---|---|
| STOP und belastbar bestätigte Station | Soll möglich sein; STOP bleibt bestehen | Physischer STOP bleibt verbindlich |
| STOP, Standort unbekannt | Keine automatische Standortannahme | Keine automatische Wiederaufnahme |
| Mäher auf dem Platz oder auf dem Weg | Kein konfliktträchtiger Wasserstart | Wasserprüfung bleibt erforderlich |
| Wasser läuft | Kein zusätzlicher Durchlauf; Beenden bleibt erreichbar | Zentrale Steuerung wartet auf bestätigtes Ende; Konfliktentscheidung bleibt erhalten |
| Wasserstatus unbekannt | Keine automatische Freigabe aus fehlenden Daten | Zentrale Steuerung startet nicht |

Dem Nutzer wurde die gezielte Wahl vorgelegt, ob bei unklarer Stationsmeldung eine App-Bestätigung „Ich sehe den Mäher in der Station“ angeboten werden soll. Die Antwort steht aus. Diese zusätzliche Freigabe wurde nicht durch Zeitablauf angenommen.

Bei Zustimmung muss die Bestätigung serverseitig berechtigt, protokolliert, kurzlebig und an genau Mäher, Zustandsmeldung und Bewässerungsauftrag gebunden werden. Sie darf die STOP-Sperre nicht löschen und keine allgemeine Mähfreigabe erzeugen. Abfahrt, Startauftrag, widersprüchliche/fehlende Rückmeldungen und ungeklärte Sendebelege widerrufen sie. Ein Neustart darf keine zweite Bewässerung auslösen. Keine alleinige Frontend-Freigabe.

Die alternative Fortführung eines bereits vorhandenen automatischen Stationsnachweises braucht einen lückenlos geprüften Übergang. Der aktuell bereits verworfene Nachweis darf nicht nachträglich aus `HOME` rekonstruiert werden.

## Start direkt am Gerät oder über Husqvarna

Der [Husqvarna-Support](https://www.husqvarna.com/uk/support/husqvarna-self-service/automower-won-t-start-or-keeps-stopping-ka-01503/) beschreibt den physischen STOP als vor Ort aufzuheben; Automower Connect kann diesen STOP nicht selbst aufheben (abgerufen am 12.09.2026). Die lokale Hersteller-API-Referenz nennt `HOME` als Parkmodus und `NOT_APPLICABLE` ohne Stationsaussage.

`mower/start_dispatch_guard.py` prüft zentrale Starts einschließlich Wasserzustand nochmals an der Sendegrenze. Einen Start am Gerät oder unmittelbar über eine Herstelleranbindung können wir nicht vorab abfangen. Im untersuchten Code ist keine allgemeine sofortige Wasserabschaltung allein bei beobachteter externer Abfahrt vorhanden; eine laufende Zone kann andernfalls ihre Dauer beenden, während spätere Zonenstarts gesperrt werden. Das ist ein offener Punkt, keine bereits geschlossene Absicherung.

Vor einer erweiterten Bewässerungsfreigabe sind daher nötig: vorhandene Startwege eindeutig abgrenzen, bei beobachteter Abfahrt laufende Relays über das persistente Befehlsjournal stoppen, tatsächliches Ende bestätigen, weitere Zonen sperren, Trockenereignis erhalten und Fehler eskalieren. Mit Cloud-Abfrageverzögerungen bleibt dies eine nachträgliche Reaktion, keine garantierte Verriegelung vor jeder physischen Abfahrt. Bis diese Grenze entschieden und erprobt ist, muss vor dem Start am Gerät die Bewässerung beendet sein.

## Tests und Review

- `pytest -p no:cacheprovider --tb=short tests/test_irrigation_terminal_cleanup.py tests/test_full_failsafe.py tests/test_irrigation_park_hold.py tests/test_manual_control_api.py tests/test_start_dispatch_guard.py tests/test_device_send_guard.py -q`: **259 Tests und 35 Subtests bestanden**.
- Neue Tests: Abschluss trotz STOP, standardmäßig ausgeschalteter Schalter, identische 150-Minuten-Trockenfreigabe über Neustart, keine erneute Bereinigung, widersprüchliche/fehlerhafte Relay-Daten, ungelöste Befehle, Erhalt abgeschlossener Sendebelege sowie Schutz-Park im selben Zyklus trotz Bereinigung.
- Appack-Gesamtsuite: **218 Tests bestanden**; anschließend drei gezielte Tests nach der Anpassung der Meldungspriorität erneut bestanden.
- Keine echten Gerätebefehle in diesen Prüfungen. Ein Schutz-Park wird mit einem Testsender geprüft.
- Unabhängiges Review durch `stopped_dock_review`: zwei Befunde (vorzeitiges Zyklusende und nicht streng genug geprüfte Relay-Daten) wurden korrigiert und nachgeprüft. Kein verbleibender sicherheitsrelevanter Blocker im geprüften Cleanup-Diff gemeldet. Das Review umfasst keine unentwickelte Stationsfreigabe.

## Einführung und Rückfall

1. Entscheidung zur Vor-Ort-Bestätigung und externen Startgrenze klären; danach Stationsfreigabe und Abfahrtsreaktion implementieren und gezielt prüfen.
2. Gemeinsames Installationsartefakt und Vergleich der aktuellen Produktionskonfiguration erstellen. Die neue Cleanup-Option bleibt bis dahin aus.
3. Nach Freigabe kontrolliert beobachten: abgeschlossener Lauf wird einmalig entfernt, STOP bleibt bestehen, Start bei laufendem Wasser bleibt gesperrt, kein doppelter Bewässerungslauf. Physischer Stationsnachweis und Wasserende müssen im überwachten Versuch belegt werden.
4. Rückfall: neue Optionen deaktivieren und vorheriges Artefakt bereitstellen. Bereits bestätigte Abschlussbereinigungen nicht blind zurückschreiben. Laufende/angenommene Gerätebefehle, native Zeitpläne und Trockenzeiten vor jedem Rückfall separat abgleichen; ein Code-Rollback hebt sie nicht auf.

Status: analysiert und Teilkorrektur implementiert/getestet; **kein neuer Parallelbetrieb und kein Live-Nachweis dieser Änderung**.
