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
