# SSV53 Azure-Migration

## Phase 1 – abgeschlossen

- Azure Functions Timer-Grundgerüst
- sichere Betriebsmodi
- Standard `CONTROL_MODE=DRY_RUN`
- keinerlei externe API-Aufrufe
- keinerlei Steuerbefehle

## Phase 2 – Read-only Live Dry Run

Phase 2 verschiebt die reine Mähentscheidung aus dem GitHub-Workflow in
wiederverwendbare Python-Module:

- `mower/husqvarna.py`: ausschließlich Anmeldung und lesender Mäherabruf
- `mower/decision.py`: reine, testbare Entscheidungslogik
- `mower/dry_run.py`: Zusammenführung von Training, Heimspielen, Hydrawise
  und Husqvarna
- `mower/controller.py`: stabiler Einstiegspunkt für Azure

### Sicherheitsgrenzen

- `PARK_ONLY`, `FULL_MOWER` und `FULL_FAILSAFE` bleiben technisch gesperrt.
- Das Husqvarna-Modul enthält keinen `/actions`-Endpunkt.
- Jede Entscheidung setzt `command_sent=false`.
- Live-Abfragen sind zusätzlich durch `ENABLE_LIVE_READS=false` deaktiviert.
- Zugangsdaten gehören später in Azure Key Vault, niemals in GitHub.

### Aktivierung nach Azure-Bereitstellung

Zunächst ausschließlich:

```text
CONTROL_MODE=DRY_RUN
ENABLE_LIVE_READS=false
```

Nach erfolgreichem Azure-Heartbeat und eingerichteten Key-Vault-Verweisen darf
nur die lesende Diagnose aktiviert werden:

```text
CONTROL_MODE=DRY_RUN
ENABLE_LIVE_READS=true
```

Auch dann werden keine Mäher- oder Beregnungsbefehle gesendet.

## Phase 3 – Sicherheitszustand und Doppelausführungsschutz

Phase 3 bereitet die dauerhafte Zustandsverwaltung vor, ohne sie bereits mit
echten Gerätebefehlen zu verbinden:

- `mower/state.py`: versionierter Automationszustand mit Revisionsnummer
- `mower/state_store.py`: testbarer Speichervertrag, In-Memory- und lokaler JSON-Store
- `mower/safety.py`: Befehlsfenster, Wartungsmodus, Duplikatschutz und Startschutz
- `tests/test_mower_state.py`: Tests für Paralleländerungen, Doppelausführungen und Eigentum an Parkierungen

### Weiterhin geltende Sicherheitsgrenzen

- Keine neuen externen API-Aufrufe
- Keine Husqvarna- oder Hydrawise-Schreibbefehle
- Azure Table Storage wird erst nach Bereitstellung der Azure-Ressourcen angebunden
- `PARK_ONLY`, `FULL_MOWER` und `FULL_FAILSAFE` bleiben technisch gesperrt
- Ein späterer automatischer Start bleibt verboten, wenn die Parkierung nicht eindeutig von der SSV53-Automatik stammt

## Phase 4 – Azure-Infrastruktur als Code

Phase 4 bereitet die Azure-Ressourcen vollständig als Bicep vor, stellt aber
noch nichts in Azure bereit:

- Flex-Consumption Function App mit Python 3.12
- benutzerseitig zugewiesene Managed Identity
- Storage ohne Shared-Key-Zugriff
- privater Deployment-Container und Azure Table für den Sicherheitszustand
- Key Vault mit RBAC und ausschließlich versionlosen Geheimnisreferenzen
- Log Analytics und Application Insights
- sicherer Startzustand `CONTROL_MODE=DRY_RUN` und `ENABLE_LIVE_READS=false`
- separater Validierungsworkflow ohne Azure-Anmeldung und ohne Deploymentrecht

### Weiterhin geltende Sicherheitsgrenzen

- Keine Azure-Ressourcen werden durch Phase 4 erstellt.
- Keine Geheimniswerte werden im Repository gespeichert.
- Der Validierungsworkflow enthält weder `azure/login` noch einen Deploymentbefehl.
- Gerätebefehle und Live-Abfragen bleiben deaktiviert.
