# SSV53 – Mähplan-Automatik

## Aktueller Stand

Die Automatik befindet sich noch im sicheren Dry-Run-Betrieb. Sie liest alle benötigten Daten und berechnet Entscheidungen, sendet aber noch keine Start-, Park-, Pause- oder Kalenderbefehle an den Husqvarna Automower.

## Datenquellen

- `public/rasen.ics`: Heimspiele auf dem Rasenplatz; die Termine enthalten bereits 60 Minuten Vorlauf und 60 Minuten Nachlauf.
- `mower/config.json`: wiederkehrende Trainingszeiten mit 30 Minuten Vorlauf und 30 Minuten Nachlauf.
- Hydrawise API: aktuelle und nächste Beregnungen; 30 Minuten Vorlauf und nach dem gemeldeten Ende 10 Minuten fortlaufend bestätigte Freigabe.
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

- Heimspiele: 60 Minuten vor und 60 Minuten nach dem Spiel; bereits in `public/rasen.ics` enthalten.
- Training: 30 Minuten vor und 30 Minuten nach dem Training.
- Hydrawise: 30 Minuten vor der Beregnung; danach bleibt der Platz gesperrt, bis Hydrawise 10 Minuten fortlaufend frei gemeldet und diese Kette persistent gespeichert hat.
- Vor Training, Spielen und Beregnung gilt zusätzlich ein Park-Lookahead von 10 Minuten.
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

## Verriegelte Park-/Startstufe

Die nächste Steuerungsstufe ist im Code vorhanden, bleibt aber standardmäßig
vollständig verriegelt:

- `PARK` wird vor Training, Spielen und als Beregnungs-Failsafe unterstützt.
- Eine automatische Startberechtigung wird ausschließlich für eine von der
  SSV53-Automatik ausgelöste Trainings- oder Spielparkierung gespeichert.
- Eine Parkierung wegen Beregnung, gemischter Sperre, fehlendem Hydrawise-
  Status oder unbekannter Ursache darf niemals automatisch gestartet werden.
- Hydrawise muss live, frisch und mindestens zehn Minuten durchgehend frei
  melden. Eine aktive oder unmittelbar anstehende Zone setzt diese
  Bestätigung sofort zurück.
- Ein Beregnungsblock setzt die Bestätigung auch dann zurück, wenn ein
  einzelner API-Wert widersprüchlich sein sollte.
- Der Mäher muss seine Ladestation mindestens eine Minute bestätigt haben,
  fehlerfrei sein und mindestens 90 Prozent Akku besitzen.
- Der Start erfolgt nur in der eindeutig erkannten `Rasenfläche`, zeitlich
  begrenzt und mindestens fünf Minuten vor dem nächsten Sperrfenster endend.
- Hydrawise bleibt technisch read-only; das Paket enthält keine Beregnungs-
  Start-, Stopp- oder Suspendierungsfunktion.

Für echte Befehle sind gleichzeitig `CONTROL_MODE=FULL_MOWER`,
`ENABLE_PARK_COMMANDS=true`, `ENABLE_START_COMMANDS=true` und die exakte
Bestätigungsphrase erforderlich. Der verriegelte Deployment-Workflow erzwingt
dagegen `DRY_RUN`, beide Schreib-Gates auf `false` und eine ungültige
Bestätigungsphrase.
