# Adaptive Mäher- und Beregnungsplanung

## Sicherheitsstatus

Die adaptive Planung ist zunächst ausschließlich ein Schattenplan. Sie darf
keine Husqvarna- oder Hydrawise-Befehle verändern. `ADAPTIVE_EXECUTION_ENABLED`
wird in diesem Paket aktiv abgelehnt. Gerätebefehle verbleiben vollständig in
der vorhandenen FULL_FAILSAFE-State-Machine.

Unveränderte harte Regeln:

- kein Wasser ohne eigenen Parkbefehl und sicheren Docknachweis;
- zwei getrennte Parkbeobachtungen, frischer Status und `connected=true`;
- keine Beregnung bei Training, Spiel, Sonderbelegung oder unbekannter Belegung;
- keine automatische Aufhebung manueller Stopps oder externer Eingriffe;
- unveränderte Allowlist der sieben Hydrawise-Relais;
- unveränderter 40-Minuten-Ausfallschutz, solange reguläre Planstarts nicht
  vollständig suspendiert und bestätigt wurden;
- 150 Minuten Mindesttrocknung ab dem tatsächlich bestätigten Ende.

## Kostenfreier Wetterpfad

Der einzige zugelassene Provider ist `OPEN_METEO`. Der Adapter besitzt keine
API-Key-, Zahlungs- oder frei konfigurierbare URL. Andere Provider werden ohne
Netzwerkaufruf abgelehnt.

Zusätzlich schützt ein ETag-gesichertes Azure-Table-Budget jeden Abruf:

- höchstens 24 Abrufe pro UTC-Tag;
- höchstens 900 Abrufe pro UTC-Monat;
- mindestens 60 Minuten zwischen zwei reservierten Abrufen;
- fehlgeschlagene Abrufe zählen mit, damit Fehler keine Abrufschleife erzeugen.

Bei ausgeschöpftem Budget, veralteten Daten oder Ausfall des Wetterdienstes
bleibt der bestehende Hydrawise-Basisplan maßgeblich. Wetter darf dann weder
Wasser reduzieren noch eine frühere Mähfreigabe begründen.

## Aktivierung im Schattenbetrieb

Der produktive Schattenbetrieb wurde am 28. August 2026 für den Rasenplatz
mit den Koordinaten `52.594709, 13.130208` aktiviert. Wetter und adaptive
Planung dürfen weiterhin ausschließlich Empfehlungen und Telemetrie erzeugen.

```text
WEATHER_ENABLED=true
WEATHER_SHADOW_ONLY=true
WEATHER_PROVIDER=OPEN_METEO
WEATHER_LATITUDE=52.594709
WEATHER_LONGITUDE=13.130208
ADAPTIVE_PLANNING_ENABLED=true
ADAPTIVE_EXECUTION_ENABLED=false
```

Der Schattenplan wird in jedem Steuerzyklus unter `details.adaptive_planning`
protokolliert und zusammen mit Wetterfrische, Parkzeitpunkten, Empfehlung und
berechneter Freigabe im bestehenden Beregnungsjournal gespeichert.

## Freigabestufen

1. Code und Sicherheitskorrekturen aktivieren. (abgeschlossen)
2. Wetter und adaptive Planung nur im Schattenbetrieb aktivieren. (abgeschlossen)
3. Mehrere Wochen Vorhersage, tatsächlichen Regen, Heimfahrten und
   Beregnungsverläufe vergleichen.
4. Erst nach fachlicher Auswertung eine neue, getrennte Live-Ausführung
   implementieren und mit einem neuen Bestätigungstext absichern.

Der Schritt in Stufe 4 ist durch dieses Deployment weder implementiert noch
freigegeben. `ADAPTIVE_EXECUTION_ENABLED=true` wird vom Code weiterhin als
ungültige Konfiguration abgelehnt.
