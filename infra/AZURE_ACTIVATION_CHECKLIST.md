# Aktivierungs-Checkliste nach Genehmigung des Azure-Guthabens

Noch keine Schritte dieser Liste ausführen, solange das Sponsorship-Abonnement
nicht aktiviert ist.

1. Azure-Sponsorship im Microsoft Nonprofit Hub aktivieren.
2. Prüfen, dass das richtige SSV53-Verzeichnis und Abonnement ausgewählt sind.
3. Ressourcengruppe `rg-ssv53-platzpflege-prod` in `Germany West Central` anlegen.
4. Bicep zunächst ausschließlich mit `what-if` prüfen.
5. Erst nach Prüfung die Infrastruktur bereitstellen.
6. Im erzeugten Key Vault diese vier Geheimnisse anlegen:
   - `husqvarna-client-id`
   - `husqvarna-client-secret`
   - `hydrawise-api-key`
   - `hydrawise-controller-id`
7. `CONTROL_MODE=DRY_RUN` und `ENABLE_LIVE_READS=false` unverändert lassen.
8. Zunächst nur den Azure-Heartbeat bereitstellen und mindestens 48 Stunden beobachten.
9. Erst danach lesende Live-Abfragen aktivieren.
10. Gerätebefehle bleiben bis zu einer späteren, gesonderten Freigabe gesperrt.

Die spätere GitHub-Anmeldung bei Azure wird mit OpenID Connect vorbereitet.
Es wird kein dauerhaftes Azure-Passwort in GitHub gespeichert.
