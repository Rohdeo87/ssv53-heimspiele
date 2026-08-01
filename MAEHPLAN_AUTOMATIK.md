# SSV53 – automatische Mähplanberechnung

Diese erste Ausbaustufe berechnet einen **Dry Run**. Sie sendet noch keine Befehle an den Husqvarna Automower.

## Berücksichtigte Sperren

- Heimspiele aus `public/rasen.ics`. Die dortigen Zeiten enthalten bereits 60 Minuten vor und 60 Minuten nach der angenommenen Spielzeit; es wird kein weiterer Spielpuffer addiert.
- Rasen-Trainings aus `mower/config.json` mit 30 Minuten vorher und 30 Minuten danach.
- Nächste relevante Hydrawise-Beregnungen mit zunächst 15 Minuten vorher und 30 Minuten danach.
- Überlappende Sperren werden zusammengeführt.
- Freie Fenster unter 30 Minuten werden verworfen.

## Hydrawise einrichten

Im Hydrawise-Konto unter **Account Details → Account Settings → Generate API Key** einen API-Key erstellen. Anschließend im GitHub-Repository unter **Settings → Secrets and variables → Actions** folgende Secrets anlegen:

- `HYDRAWISE_API_KEY`
- `HYDRAWISE_CONTROLLER_ID` nur dann, wenn das Hydrawise-Konto mehrere Steuergeräte enthält

Die API wird ausschließlich gelesen. Der Workflow startet, stoppt oder verändert keine Beregnung.

In `mower/config.json` gilt zunächst `include_all_zones: true`. Nach dem ersten erfolgreichen Lauf können die im Artefakt sichtbaren Hydrawise-Zonen auf die tatsächlichen Rasenplatz-Zonen begrenzt werden, indem `include_all_zones` auf `false` gesetzt und `relay_ids` oder `zone_name_patterns` gepflegt werden.

## Workflow

`SSV53 Mähplan installieren und berechnen V5` ist zugleich Installer und dauerhafter Dry-Run-Workflow. Er läuft:

- einmal täglich,
- nach einem erfolgreichen Heimspiel-Update,
- manuell über `workflow_dispatch`.

Der Workflow installiert keine weitere Datei unter `.github/workflows/`. Damit wird die GitHub-Sicherheitsbeschränkung vermieden, die das Erzeugen oder Ändern von Workflow-Dateien durch den normalen Actions-Token blockiert.

Bereits vorhandene Mähplan-Dateien werden bei normalen Läufen nicht überschrieben. Das Ergebnis steht in der GitHub-Actions-Zusammenfassung und als Artefakt mit:

- `mowing_plan.md`
- `mowing_plan.json`

## Noch keine Mähersteuerung

Die Husqvarna-API wird erst ergänzt, nachdem mindestens mehrere Dry Runs plausibel waren, die relevanten Hydrawise-Zonen feststehen und die aktiven Rasen-/Winterzeiträume bestätigt sind.
