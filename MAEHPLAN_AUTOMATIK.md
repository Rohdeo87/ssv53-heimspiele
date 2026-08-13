# SSV53 – Mähplan-Automatik

## Aktueller Stand

Die vollständige Sicherheitslogik ist als separat verriegelte `FULL_FAILSAFE`-
Stufe implementiert. Der Quellstand kann Mäher und Hydrawise steuern; jede
Schreibart besitzt ein eigenes Gate und eine zusätzliche exakte
Bestätigungsphrase. Infrastruktur-Deployments setzen alle Gates weiterhin
standardmäßig auf `false` und die Bestätigungen auf `LOCKED`.

## Datenquellen

- `public/rasen.ics`: Heimspiele auf dem Rasenplatz; die Termine enthalten bereits 60 Minuten Vorlauf und 60 Minuten Nachlauf.
- `mower/config.json`: wiederkehrende Trainingszeiten mit 30 Minuten Vorlauf und 30 Minuten Nachlauf.
- Hydrawise API: exakt sieben aktuelle/nächste Zonen. Ein planmäßiger Lauf wird
  30 Minuten vorher gesperrt; nach sieben einzeln bestätigten Zonenenden muss
  Hydrawise 90 Minuten fortlaufend frei bleiben.
- Husqvarna Authentication API und Automower Connect API: aktueller Status von „Schaf“, Akku, Aktivität, Fehlerzustand, Planner-Override und EPOS-Arbeitsbereich.

## Dauerhafte Workflows

### `mower-plan.yml`

Berechnet den maximalen Mähplan:

- täglich,
- nach einem erfolgreichen Heimspielabruf,
- manuell für ein frei wählbares Startdatum.

Deutsche Datumsformate wie `24.8.26` und `24.08.2026` werden akzeptiert.

### `mower-decision.yml`

Prüft alle 15 Minuten die aktuelle Situation und gibt im Dry Run eine Empfehlung aus:

- Mähen möglich,
- bereits korrekt am Mähen,
- Parken wäre erforderlich,
- bereits sicher geparkt,
- manuellen Stopp respektieren,
- Fehler oder Wartungszustand manuell prüfen.

Freie Mähfenster werden über Mitternacht hinweg verbunden.

## Verbindliche Sperrregeln

- Heimspiele: Platzsperre 60 Minuten vor und 60 Minuten nach dem Spiel; der
  Parkbefehl folgt wegen des zusätzlichen Lookaheads 70 Minuten vor Anstoß.
- Training: Platzsperre 30 Minuten vor und 30 Minuten nach dem Training; der
  Parkbefehl folgt 40 Minuten vor Trainingsbeginn.
- Hydrawise: 30 Minuten vor dem Planstart beginnt die Platzsperre, der
  Parkbefehl folgt 40 Minuten vorher. Nach bestätigter Parkposition werden die
  sieben späteren Planstarts suspendiert und dieselben sieben Zonen mit ihren
  jeweiligen Planlaufzeiten nacheinander gestartet. Erst nach jedem
  bestätigten Zonenstart und Zonenende folgt die nächste Zone.
- Nach der siebten Zone bleibt der Mäher mindestens 90 Minuten geparkt. Jede
  fehlende, alte oder widersprüchliche Antwort unterbricht die Freigabekette.
- Überlappende Sperren werden zusammengeführt.
- Freie Fenster unter 30 Minuten werden nicht für einen neuen Mähstart verwendet.

## Manuelle Bedienung

Ein manueller Stopp oder eine erkennbare manuelle Übersteuerung darf später niemals automatisch aufgehoben werden. Fehler- und Wartungszustände verhindern jeden automatischen Start.

## GitHub-Secrets

- `HYDRAWISE_API_KEY`
- optional `HYDRAWISE_CONTROLLER_ID`
- `HUSQVARNA_CLIENT_ID`
- `HUSQVARNA_CLIENT_SECRET`
- `SSV53_AUTOMATION_TOKEN` nur für ausdrücklich bestätigte Repository-Wartungsaktionen

## Verriegelte Gesamtsteuerung

Die nächste Steuerungsstufe ist im Code vorhanden, bleibt aber standardmäßig
vollständig verriegelt:

- `PARK` wird vor Training, Spielen und als Beregnungs-Failsafe unterstützt.
- Eine automatische Startberechtigung wird ausschließlich durch persistent
  gespeicherte SSV53-Park- oder Mähaufträge erzeugt. Fremde und manuelle
  Übersteuerungen werden niemals aufgehoben.
- Hydrawise muss live und frisch genau sieben eindeutige Zonen liefern. Vor
  einem vorgezogenen Lauf werden alle sieben ursprünglichen Planstarts bis
  hinter ihr ursprüngliches Ende suspendiert; so kann kein zweiter Lauf
  entstehen.
- Doppelte Funktionsaufrufe können keinen doppelten Zonenstart auslösen: jeder
  Start wird vor dem API-Aufruf persistent reserviert. Ein unbestätigter Start,
  eine unerwartete aktive Zone oder parallele Zonen führen in einen
  fail-closed Fehler-Hold.
- Ein Beregnungsblock setzt die Bestätigung auch dann zurück, wenn ein
  einzelner API-Wert widersprüchlich sein sollte.
- Der Mäher muss seine Ladestation mindestens eine Minute bestätigt haben und
  fehlerfrei sein. Neue Mähaufträge starten ab 90 Prozent Akku; ein bereits
  übernommener Dauer-Mähauftrag darf nach abgeschlossenem Arbeitsbereich ab
  60 Prozent erneut beginnen. Unterhalb dieser Schwelle lädt er weiter.
- Der Start erfolgt ausschließlich in der eindeutig erkannten `Rasenfläche`,
  zeitlich bis spätestens fünf Minuten vor dem nächsten Sperrfenster. Solange
  kein Spiel, Training, Beregnung oder zu niedriger Akkustand entgegensteht,
  wird ein beendeter Arbeitsbereich erneut gestartet.

Für die Gesamtsteuerung sind gleichzeitig `CONTROL_MODE=FULL_FAILSAFE`,
`ENABLE_PARK_COMMANDS=true`, `ENABLE_START_COMMANDS=true`,
`ENABLE_IRRIGATION_COMMANDS=true` sowie beide exakten Bestätigungsphrasen
erforderlich. Ein fehlendes Gate lässt die zugehörige Aktion gesperrt.

## Alarmierung und kontrollierter Fehler-Reset

Zusätzlich zu Timer- und Exception-Alarmen meldet Azure semantische
Sicherheitszustände mit hoher Priorität. Dazu gehören insbesondere ein
gespeicherter Beregnungsfehler, eine veränderte oder gelöschte Planfolge,
ein abgelaufener Suspendierungsnachweis, eine falsche Relay-Liste und eine
fehlgeschlagene Zustandspersistierung.

Ein gespeicherter Beregnungszustand `FAILED` darf nur über den mit einem
Function-Key geschützten `POST /api/irrigation/recover-failed` zurückgesetzt
werden. Der Request muss die aktuelle Zustandsrevision und die exakte Phrase
`SSV53-RESET-FAILED-IRRIGATION` enthalten. Vor dem Reset werden Mäherstatus,
Dockposition, Fehlerfreiheit, Automationsbesitz, Hydrawise-Frische sowie die
exakte Freigabe aller sieben Relay-IDs erneut live geprüft. Ein Konflikt oder
eine aktive beziehungsweise bevorstehende Zone lehnt den Reset ab.

Der Reset selbst sendet weder einen Husqvarna- noch einen Hydrawise-Befehl.
Der Mäher bleibt im Dock und die fortlaufende 90-Minuten-Freigabekette beginnt
neu. Die erwartete Revision muss unmittelbar vor dem Request aus dem aktuellen
Sicherheitsbericht oder der Telemetrie entnommen werden; bei HTTP 409 darf kein
zweiter Request mit geratenen Werten erfolgen, bevor die Ursache geprüft wurde.
