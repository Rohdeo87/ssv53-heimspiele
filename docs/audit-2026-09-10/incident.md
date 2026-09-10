# Mäher und Bewässerung am 10. September 2026

## Belegter Vorfall

Die Auswertung von 404 regulären Minutenzyklen zwischen dem 9. September,
23:50 Uhr, und dem 10. September, 06:33 Uhr, zeigt eine reale Schutzlücke.
Alle folgenden Uhrzeiten gelten für Europe/Berlin.

| Zeit | Beobachtung |
|---|---|
| 03:50 | Die lesende Planung fordert bereits hypothetisch Parken an. |
| 04:30–04:52 | Der Mäher meldet Mähen, Hydrawise gleichzeitig eine aktive Zone. |
| 04:52–04:54 | Heimfahrt und anschließend Laden; die Bewässerung läuft weiter. |
| 05:45 | Der Mäher verlässt die Station bei weiterhin aktiver Bewässerung. |
| 05:46–06:28 | Erneut Mähen während laufender Bewässerung. |
| ab 06:28 | Der Mäher meldet PAUSED; der zentrale Dienst hat keinen Befehl gesendet. |

64 Minutenabtastungen enthalten gleichzeitig `MOWING` und einen frischen
Hydrawise-Status mit `active_zone_count=1`. Die Intervalle sind aus Abtastungen
abgeleitet, keine sekundengenaue Messung der Messer- oder Ventilbewegung.
Die unveränderten Rohantworten bleiben im privaten Diagnoseordner; sie
enthalten Gerätekennungen und werden nicht veröffentlicht.

## Ursachen

1. `OPERATOR_ONLY` bediente ausschließlich neue manuelle Park- und
   Schnitthöhenaufträge. Die erkannte Entscheidung `WOULD_PARK` führte zu
   keinem automatischen Parkbefehl. Sämtliche untersuchten Zyklen melden
   `command_sent=false` und ein leeres Operatorjournal.
2. Husqvarna lieferte um 06:32 einen leeren Wochenkalender, aber den weiterhin
   gesetzten Override `FORCE_MOW`. Der manuelle Auftrag konnte nach dem Laden
   fortgesetzt werden. Ein leerer Kalender hebt einen Override nicht auf.
3. Der native Hydrawise-Plan lief unabhängig weiter. Beobachtet wurden sieben
   Zonen von 04:30 bis 07:10 Uhr. Dieses Zeitfenster erfüllt die Vorgabe
   frühestens 03:30 und vollständig beendet bis 08:00; es ersetzt jedoch
   keine gegenseitige Verriegelung der Geräte.
4. Der gemeinsame Trainingskalender stand weiterhin auf `SHADOW`.
   Dadurch war die gemeinsame Quelle nicht verbindlich aktiv und der
   Winterumschalter auf der Platzpflegeseite nicht bedienbar.

Die Freigabe einzelner Bedienfunktionen war keine sichere Koordination des
Betriebs. Dieser Zwischenstand durfte mit einem fortgesetzten manuellen
Mähauftrag und einem unabhängigen Bewässerungsplan nicht unbeaufsichtigt
verbleiben. Tests und die überprüfte Installation hatten diese betriebliche
Lücke nicht geschlossen.

## Sofortige Eingrenzung und Änderungen

Der Nutzer wurde gebeten, den Mäher zu stoppen und geparkt zu lassen. Die
anschließend gelesene Pause ist ein Zustandsnachweis; sie ist kein bestätigter
unbegrenzter Stationsparkauftrag. Kein automatischer Wiederanlauf wird
freigegeben.

App und Steuerung lieferten vor der Kalenderaktivierung unabhängig denselben
gültigen Kalender, Revision 2, ohne Blocker. Der persistente Status war Sommer,
Winter ausgeschaltet, ohne vorgemerkten Wechsel; Absagen und Spielquelle waren
verfügbar. Die verbindliche Aktivierung verändert diesen Inhalt nicht.

Kalender-Hash: `bc9ccae4f33054cd29dcf4eed044d1b8095cac29c9215182f2646fdb3cdf1eec`.
Hüllen-Hash: `804ca739366b1f372947ed661eac9b142567934030091c102cf2707f01b629d5`.

Die Einstellung `SHARED_TRAINING_MODE=ACTIVE` wurde anschließend gesetzt und
zurückgelesen. Die öffentlichen Abfragen um 06:41 Uhr mit Sommer **und** Winter
lieferten denselben wirksamen Sommerplan: 38 unveränderte Termine, davon vier
Spiele und 34 Trainings. Sämtliche bisherigen Terminwerte blieben erhalten;
Trainings liefern zusätzlich die verbindlichen Puffer von je 30 Minuten.
Nur der Auswertungszeitpunkt unterscheidet die beiden Abfragen.
Der [Kalendernachweis](calendar-activation-proof.json) dokumentiert diesen
Abgleich. Es wurde kein Winterwechsel ausgelöst.

Der fehlende Parkschutz wird innerhalb desselben Bedienprozesses ergänzt.
Er erhält eine eigene, standardmäßig ausgeschaltete Freigabe, verwendet das
persistente Befehlsjournal und darf weder Mähen starten noch Wasserbefehle
auslösen. Manuelle Pause und Stopps müssen erhalten bleiben. Laden allein
gilt bei einem fortsetzbaren Mähauftrag nicht als dauerhafte Absicherung.

Die tatsächlichen Test-, Installations- und Aktivierungsergebnisse sind
gesondert nachzuweisen. Dieser Ursachenbericht behauptet deren Abschluss nicht.

## Entwicklung und reproduzierbare Nachweise

- Gesamte Python-Suite: **1.238 Tests und 445 Subtests bestanden**.
- Gesamte Appack-Node-Suite nach der letzten Anzeigenkorrektur: **125 Tests bestanden**.
- Zwei getrennte Reviews bearbeiteten Befehlsjournal/Parkschutz und die App-Anzeige;
  die integrierte Änderung wurde anschließend nochmals geprüft und getestet.
- [Historische Wiederholung](guard-replay.json): fünf echte aufgezeichnete
  Eingangssituationen, jeweils mit unabhängigem nachgebildetem Speicher und
  Sender. Vier Schutzfälle fordern genau einen Parkauftrag an; die manuelle
  Pause fordert keinen Bewegungsbefehl an. Mit ausgeschaltetem Schutz fordert
  keiner dieser Fälle einen automatischen Parkauftrag an.
- Reproduzierbar mit `scripts/replay_operator_guard_incident.py --input <private-logs>
  --output <proof.json>`. Netzwerkzugriff ist im Wiederholungsskript gesperrt.
  Die fünf isolierten Fälle sind keine lückenlose physische Nachsimulation;
  daraus werden weder verhinderte Unfallminuten noch reale Heimfahrten abgeleitet.
- Mobile Sichtprüfung mit dem tatsächlichen Template, synthetischen Daten und
  gesperrtem Netzwerk: [Laden](charging-preview/mobile.png) und
  [laufende Bewässerung mit fahrendem Mäher](water-conflict-preview/mobile.png).
  Der aktuelle Zustand und die akute Parkaufforderung bleiben sichtbar.
  Ohne Startautomatik zeigt die Planung „Start nur manuell“ statt einer
  scheinbar verbindlichen Startzeit. Diese Ansichten sind keine Geräteabnahme.
- Auch fünf reguläre Steuerungszyklen bis 06:59 Uhr bestätigen jetzt
  `training_calendar.active=true`, denselben Kalender-Hash und denselben
  wirksamen Sommerplan wie der öffentliche Kalender.

Der Schutz ist standardmäßig aus und wird nur im bestätigten Bedienmodus mit
freigegebenen Parkbefehlen wirksam. Ein vorhandener unbeantworteter Parkauftrag
wird nicht automatisch wiederholt. Erneute Ausfahrt nach einem eindeutig
abgeschlossenen Auftrag benötigt neue Telemetrie und mindestens 30 Sekunden
Abstand. Ein gerätegebundener frischer Stationsstatus mit `FORCE_PARK` ist der
einzige positive Haltenachweis; eine bloße Befehlsannahme genügt nicht.

Bei Rücknahme zunächst den Schutzschalter deaktivieren und den sicheren
Gerätezustand herstellen. Ältere Software versteht die neuen Schutzaufträge im
Journal nicht. Das Journal darf deshalb nicht gelöscht werden, um einen
Code-Rollback zu erzwingen; ungeklärte Aufträge müssen erhalten und aufgelöst
werden.

## Abnahme und Restrisiken

- Historische Abläufe vor Bewässerungsbeginn, während aktiver Bewässerung und
  beim Laden müssen mit ausschließlich nachgebildeten Sendern Parkschutz
  auslösen. Eine manuelle Pause muss ohne Bewegungsbefehl bestehen bleiben.
- Gleichzeitige Instanzen, Neustart, verlorene Antwort und fehlende Daten
  dürfen keine automatische Startfreigabe oder unkontrollierte Wiederholung
  erzeugen. Ein angenommener Parkauftrag bleibt bis zur Geräterückmeldung offen.
- App und Steuerung müssen nach der Kalenderaktivierung dieselben aktiven
  Kalender- und Saisonrevisionen melden; Spielbelegungen bleiben erhalten.
- Nach Installation sind Paketdateien und echte reguläre Zyklen zu prüfen.
  Eine Gerätewirkung gilt erst nach frischer Rückmeldung als bestätigt.
- Ein Ausfall der Zentrale kann einen unabhängigen Hydrawise-Zeitplan nicht
  ausschalten. Der Parkschutz ist deshalb keine vollständige Abnahme eines
  unbeaufsichtigten Automatikbetriebs. Externe manuelle Starts können weiterhin
  eine Reaktions- und Heimfahrtzeit verursachen.
- Rückfall: automatische Starts gesperrt lassen, Mäher dauerhaft parken und
  vorhandene Herstelleraufträge prüfen. Ein reiner Code-Rollback genügt nicht.

Herstellergrundlage: Die [Automower-API](https://developer.husqvarnagroup.cloud/apis/automower-connect-api?tab=readme)
und ihr [Schema](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/swagger.yml)
unterscheiden Kalender, Override, Befehlsannahme und tatsächlichen Zustand.
