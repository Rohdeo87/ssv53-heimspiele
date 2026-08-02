# Azure-Infrastruktur der SSV53-Platzpflege

Dieses Verzeichnis enthält ausschließlich die vorbereitete Infrastruktur als Code.
Es wird durch Phase 4 noch **nichts** in Azure bereitgestellt.

## Geplante Ressourcen

- Azure Function App auf Flex Consumption mit Python 3.12
- Benutzerseitig zugewiesene Managed Identity
- Storage Account ohne Shared-Key-Zugriff
- privater Blob-Container für Funktionspakete
- Azure Table `MowerAutomationState` für den späteren Sicherheitszustand
- Key Vault ohne im Repository gespeicherte Geheimnisse
- Log Analytics und Application Insights
- notwendige RBAC-Rollen für Storage, Monitoring und Key Vault

## Sichere Erstkonfiguration

- `CONTROL_MODE=DRY_RUN`
- `ENABLE_LIVE_READS=false`
- Timer: jede Minute
- keine dauerhaft bereiten Instanzen
- keine Gerätebefehle
- keine Geheimniswerte in Bicep oder Parameterdateien

## Lokale beziehungsweise GitHub-Validierung

```bash
az bicep install
az bicep build --file infra/main.bicep
python -m unittest discover -s tests -p "test_*.py" -v
```

## Spätere Bereitstellung

Die Bereitstellung erfolgt erst nach Aktivierung des Azure-Nonprofit-Guthabens,
einem Azure-`what-if` und einer ausdrücklichen Freigabe. Die vier Gerätezugänge
werden anschließend direkt im Key Vault angelegt und niemals in GitHub
gespeichert.
