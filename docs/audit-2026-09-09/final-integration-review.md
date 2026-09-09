# Abschließender Integrationsreview – Trainingskalender

Stand: 9. September 2026. Dieser Review ist ein lokaler, read-only Review und
keine Betriebsfreigabe.

Geprüft wurden `scripts/prepare_training_calendar_approval.py`,
`scripts/build_runtime_config_bundle.py`, der manuelle
`azure-runtime-config-rollout.yml`-Pfad sowie der bestehende
`SSV53_Runtime_Config_Auto_Dispatch.yml`-Aufrufer.

- Die lokale Vorbereitung verlangt einen konkreten SHA-256 des Kandidaten,
  einen nichtleeren Freigabebeleg und einen nicht zukünftigen Zeitpunkt. Sie
  validiert die Quellenbindungen erneut, erstellt die Ausgabe exklusiv und
  aktiviert oder veröffentlicht nichts.
- Der Runtime-Bundlebau akzeptiert den Winter-Schalter nur zusammen mit einem
  gültigen gemeinsamen Kalender. Das Envelope wird identisch in Mäher-Config
  und Occupancy-Daten eingebettet; das Manifest bindet deren Bytes per SHA-256.
- Der Rollout liest bei einem Workflow-Dispatch zuerst dessen unveränderlichen
  Commit und lässt ihn weiterhin nur auf dem Migrationsbranch zu. Für den
  sechs-stündigen Dispatcher, der keine Kalender-Inputs sendet, fallen
  Schalter, Inhaltshash, Freigabebeleg und Freigabezeit auf die vier
  persistenten GitHub-Variablen zurück. Fehlt oder ändert sich eine Bindung,
  schlägt die Kalendervorbereitung fehl, statt einen Kalender stillschweigend
  wegzulassen.

Die gezielte Prüfung
`tests/test_runtime_config_rollout_workflow.py`,
`tests/test_manual_training_bundle.py`,
`tests/test_training_calendar_approval.py` und
`tests/test_training_control.py` ergab **52 bestanden**.

## Nachreview der einmaligen Initialisierung

Die ADMIN-Route `training-control/initialize` bleibt standardmäßig durch
`WINTER_TRAINING_INITIALIZATION_ENABLED=false` gesperrt. Ihr GET gibt nur
Zustandsrevision, Initialisierungsmarker und den öffentlichen Trainingszustand
zurück. POST verlangt vollständiges Schema, Bestätigung und erwartete
Zustandsrevision; die serverseitige einmalige CAS-Initialisierung erhält
unverwandte Zustandsfelder.

Die CLI verlangt für GET und POST nun zwingend exakt die festgelegte HTTPS
ADMIN-URL. Der frühere direkte Managed-Identity-Pfad wurde entfernt. Der
Hostschlüssel bleibt im HTTP-Header, erscheint weder in URL noch Payload oder
CLI-Ausgabe, und der Client folgt keinen Redirects und wiederholt keinen POST.
Bei einem Transportfehler sagt er ausschließlich, dass kein zweites Senden
erfolgt ist; die Wirkung des ersten POST bleibt ausdrücklich ungeklärt.

`tests/test_training_control.py` ergab für diese Nachprüfung **44 bestanden**.

Nicht geprüft und nicht ausgeführt wurden GitHub-Variablen, Workflow-Dispatch,
Azure-Publish, Gerätebefehle, Winter-Schalter oder andere Produktionsaktionen.
Die persistente Variablenbelegung und jede Veröffentlichung bleiben eine
separate, ausdrücklich zu genehmigende Live-Aktion.
