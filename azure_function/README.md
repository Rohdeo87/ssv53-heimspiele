# Azure Functions – SSV53 Platzpflege

Die Azure Function ist in Phase 1 ausschließlich ein sicherer Heartbeat.

## Lokale Konfiguration

`local.settings.example.json` nach `local.settings.json` kopieren. Die
Beispieldatei enthält keine Zugangsdaten.

## Sicherheitsgrenze

Nur `CONTROL_MODE=OFF` und `CONTROL_MODE=DRY_RUN` sind zulässig. Alle späteren
Live-Modi sind im Code ausdrücklich blockiert.

## Zeitplan

Azure NCRONTAB enthält ein zusätzliches Sekundenfeld. Der Standard

```text
0 * * * * *
```

bedeutet: einmal pro Minute bei Sekunde 0.
