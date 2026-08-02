# Offene Entscheidungen vor dem ersten Azure-Deployment

Vor `what-if`, Deployment oder lesenden Live-Abfragen werden diese Punkte
ausdrücklich bestätigt:

1. Ist die Mannschaftsbezeichnung `E1` in `mower/config.json` korrekt,
   oder muss sie `E2` lauten?
2. Sind die effektiven Vorläufe gewollt?
   - Training: 30 Minuten Puffer plus 15 Minuten Park-Lookahead
   - Beregnung: 30 Minuten Puffer plus 15 Minuten Park-Lookahead
   - Heimspiele: 60 Minuten ICS-Puffer plus 15 Minuten Park-Lookahead
3. Wie erhält Azure nach einer Spielplanänderung die aktuelle
   `public/rasen.ics` und `mower/config.json`?
   Bevorzugt wird ein versionierter Blob-Abruf mit Managed Identity,
   ETag, letzter gültiger Kopie und maximal zulässigem Datenalter.
4. Welche E-Mail-Adressen erhalten Heartbeat-, Fehler- und Kostenalarme?
5. Welche minimale Azure-Rolle wird für den reinen What-if-Lauf
   freigegeben?
6. Sind die vier Key-Vault-Geheimnisse vorhanden und korrekt benannt?
7. Bleiben `CONTROL_MODE=DRY_RUN` und `ENABLE_LIVE_READS=false` beim
   ersten Deployment unverändert?

Ohne dokumentierte Bestätigung dieser Punkte erfolgt keine Aktivierung.
