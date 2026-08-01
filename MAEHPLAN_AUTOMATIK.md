# SSV53 – Mähplan-Automatik

## Aktueller Stand

Die Automatik befindet sich noch im sicheren Dry-Run-Betrieb. Sie liest alle benötigten Daten und berechnet Entscheidungen, sendet aber noch keine Start-, Park-, Pause- oder Kalenderbefehle an den Husqvarna Automower.

## Datenquellen

- `public/rasen.ics`: Heimspiele auf dem Rasenplatz; die Termine enthalten bereits 60 Minuten Vorlauf und 60 Minuten Nachlauf.
- `mower/config.json`: wiederkehrende Trainingszeiten mit 30 Minuten Vorlauf und 30 Minuten Nachlauf.
- Hydrawise API: aktuelle und nächste Beregnungen; derzeit 15 Minuten Vorlauf und 30 Minuten Nachlauf.
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
- Hydrawise: 15 Minuten vor und 30 Minuten nach der Beregnung.
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

## Nächste Ausbaustufe

Als erster echter Steuerbefehl wird ausschließlich das sichere Parken vor einer aktiven Sperrzeit umgesetzt. Der automatische Start folgt erst nach weiterer Validierung.
