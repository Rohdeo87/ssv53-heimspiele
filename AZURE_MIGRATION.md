# Azure-Migration der SSV53-Platzpflege

## Status dieser ersten Stufe

Diese Vorbereitung ist vollständig **inert**:

- kein Azure-Deployment,
- keine Azure-Ressourcen,
- kein Husqvarna-Aufruf,
- kein Hydrawise-Aufruf,
- kein Park-, Start- oder Beregnungsbefehl.

Die bestehende GitHub-Mähsteuerung bleibt unverändert. Die neuen Dateien liegen
ausschließlich auf dem Branch `feature/azure-mower-migration`.

## Enthaltene Grundlage

- Azure Functions Python-v2-Projekt im Repository-Stamm,
- minütlicher Timer über `TIMER_SCHEDULE`,
- Schedule-Monitoring,
- drei Wiederholungsversuche im Abstand von zehn Sekunden,
- strukturierte Heartbeat-Protokolle,
- zentrale Funktion `mower.controller.run_control_cycle`,
- feste Betriebsmodi mit gesperrten Live-Modi,
- automatisierte Sicherheitstests.

## Betriebsmodi

| Wert | Bedeutung |
|---|---|
| `OFF` | Timer protokolliert nur, Automatik deaktiviert |
| `DRY_RUN` | sichere Standardstufe ohne Befehle |
| `PARK_ONLY` | für Phase 1 technisch gesperrt |
| `FULL_MOWER` | für Phase 1 technisch gesperrt |
| `FULL_FAILSAFE` | für Phase 1 technisch gesperrt |

Ein versehentlich gesetzter Live-Modus führt in dieser Stufe zu einem Fehler und
damit zu einer sichtbaren Wiederholung beziehungsweise Alarmierung, aber niemals
zu einem Gerätebefehl.

## Nächste technische Stufe

1. Bestehende Husqvarna-Logik aus `mower-decision.yml` in `mower/husqvarna.py`
   verschieben.
2. Entscheidungslogik in ein unabhängig testbares Modul übernehmen.
3. persistenten Automationszustand definieren.
4. Azure Storage und Key Vault als Infrastrukturcode vorbereiten.
5. nach Freischaltung des Nonprofit-Guthabens den Heartbeat bereitstellen.
