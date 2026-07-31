# Spielabrufe vollständig vom Handy weiterentwickeln

## Entwicklungsablauf

1. Änderungen werden in einem eigenen Branch und Pull Request vorbereitet.
2. Der Workflow **SSV53 Code prüfen** testet den Code automatisch, ohne FUSSBALL.DE aufzurufen.
3. Für einen echten Abruf wird in GitHub Actions der Workflow **SSV53 Heimspiele aktualisieren** auf dem Test-Branch manuell gestartet.
4. Auf einem Test-Branch ist der Lauf standardmäßig ein reiner Test. Er schreibt keine erzeugten Daten zurück.
5. Die Registerkarte **Summary** zeigt neue, geänderte und entfernte Spiele direkt lesbar an.
6. Erst nach Prüfung wird der Pull Request nach `main` übernommen.

## Manueller Testlauf auf dem Handy

1. Repository `ssv53-heimspiele` öffnen.
2. **Actions** öffnen.
3. **SSV53 Heimspiele aktualisieren** auswählen.
4. **Run workflow** antippen.
5. Den gewünschten Test-Branch auswählen.
6. `skip_start_delay` aktiviert lassen.
7. `allow_destructive_change` deaktiviert lassen.
8. `persist_test_results` deaktiviert lassen.
9. Lauf starten und anschließend **Summary** öffnen.

## Bedeutung des Änderungsberichts

- **Neu:** Spiel-ID war im letzten erfolgreichen Feed noch nicht vorhanden.
- **Geändert:** Dasselbe Spiel hat beispielsweise einen anderen Anstoß, Platz, Status oder Gegner.
- **Entfernt:** Spiel-ID ist im neuen vollständigen Saisonabruf nicht mehr vorhanden.

Eine reine Änderung des technischen Checksums wird nicht als fachliche Änderung angezeigt.

## Zusätzlicher Schutz

Die Veröffentlichung wird automatisch blockiert, wenn:

- der neue Feed leer ist, obwohl vorher Spiele vorhanden waren, oder
- bei mindestens zehn bisherigen Spielen mehr als 60 Prozent und mindestens fünf Spiele auf einmal verschwinden.

Der letzte erfolgreiche Stand bleibt dann unverändert. Eine solche Änderung darf nur nach manueller Prüfung mit `allow_destructive_change` freigegeben werden.

## Veröffentlichung

- Zeitgesteuerte Läufe auf `main` veröffentlichen weiterhin automatisch.
- Manuelle Läufe auf `main` veröffentlichen ebenfalls automatisch.
- Läufe auf Test-Branches veröffentlichen standardmäßig nicht.
