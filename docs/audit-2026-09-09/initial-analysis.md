# Ausgangsbewertung vor Umsetzung – 9. September 2026

Diese Analyse begründet die Entwicklungsarbeiten. Sie ist keine Livefreigabe.
Ausgangsbranch: `feature/azure-mower-migration`, SHA
`9c2d0fc9c010b366cd49b58b8086b27f9d59a0dd`. Separater Worktree und Branch
`audit/platzpflege-20260909`; bestehende Nutzeränderungen im ursprünglichen
Checkout (Alarmierung/Infra) bleiben erhalten. `sources/` bleibt unverändert.

## Nachgewiesener Stand

- GitHub-main: `d67e9d3fef24117f504a192c9ecaee998612ddd7`. Der lokale
  Fetch-Refspec aktualisierte zunächst nur den Migrationsbranch; main wurde
  ausdrücklich neu geladen und mit der GitHub-API abgeglichen.
- `ssv53-app` main `2d90d062d8c84d600eecd0dc592fe944963d08e5`: Expo-Prototyp,
  synthetische Inhalte, keine implementierte Cloud-Umgebung. Kein nachgewiesener
  Steuerungsteil der produktiven Kette. Appack-Templates im Heimspielrepo
  referenzieren den unten genannten Azure-Host.
- Azure Resource Group und Function App aus dem Auftrag existieren. Flex
  Consumption, Python 3.12, eine Mäh-Timerfunktion pro Minute, FULL_FAILSAFE,
  Liveabfragen und Geräte-Schreibflags aktiv. Adaptive Planung aktiv, adaptive
  Ausführung aus; Wetter nur Shadow. Sieben Hydrawise-Zonen konfiguriert.
- Liveparameter: Trocknung 150 Min., Heimfahrtvorlauf 4 Min., zwei
  Dockbestätigungen, Telemetriegrenze 180 s, Akku Fortsetzung/Neustart 60/90 %.
  06–22 Uhr gehört zum FUSSBALL.DE-Abruffenster, nicht zum 24h-Mähplan.
- Deployment-ID `cbac58e0-76b7-4fb2-b789-c9332d885b57`, aktiv, Status 4,
  Ende 06.09.2026 05:36:37 UTC. Das beweist noch keinen vollständigen
  Bytevergleich mit Git: Blobdatenzugriff verweigert, Shared Key deaktiviert,
  SCM-Dateiabfrage nicht verfügbar. Keine Zugriffsrechte verändert.
- Geräte-Telemetrie 09.09.2026 05:48 UTC: Modell 580 EPOS, ERROR, Code 93,
  95 % Akku, kein bestätigtes Dock; Bewässerung wartet. Dies ist ein
  API-/Lognachweis, keine physische Vor-Ort-Prüfung.

## Priorisierung und erste Entscheidungen

| Rang | Beleg / Ursache | Sichere Entwicklungsmaßnahme | Wirkung / Grenze |
|---|---|---|---|
| P0 | Hydrawise-Datenfehler löscht kontinuierlichen Freigabebeginn; physische Wartezeit und Datenbestätigung sind vermischt | Persistente physische Trocknungsbasis separat, kurze/lange Lücke injizieren, konservative Migration | Verhindert erneute volle Wartezeit durch Einzelabfrage und vorzeitige Freigabe nach externer Bewässerung; keine Verkürzung der 150 Min. |
| P0 | Traineränderungen ANONYMOUS, Rollen/Ersteller teils vom Client; Azure Auth deaktiviert | Serverauthentifizierung vor jedem Schreibpfad, negative Berechtigungstests | Keine Freigabe allein durch Approlle; Einführungsabhängigkeit: berechtigter Anmeldeweg |
| P1 | Aktueller Import scheitert bei Verlegung September → Oktober | Auflösung über vollständig gelesene Abruffenster, Identitäts-/Vollständigkeitsprüfung erhalten | Wiederherstellung der gesamten Feedkette; keine leere Freigabe |
| P1 | Migration enthält ältere Importhärtung als main | Gezielt geprüfte Schutzänderungen übernehmen | Verhindert unsichere Rückschritte bei späterem Merge |
| P1 | UI nennt 120 statt produktiv 150 Min.; schätzt Laden mit unbelegtem 1 %/Min. | Serverfristen und Datenalter anzeigen, unbelegte Ladeprognose entfernen, Mehrfachsperren sichtbar machen | Korrekte Erwartung ohne Änderung der Geräteentscheidung |
| P1 | Gerätestörung blockiert Heimfahrt und Bewässerung | Ursachen in Betriebsdaten getrennt ausweisen, Eskalation/Abbruch dokumentieren | Keine Softwarefreigabe bei fehlendem Dock; Vor-Ort-Prüfung erforderlich |
| P2 | Planungsziel misst Fensterverlust, nicht produktive Schnittzeit; Laden fehlt | Isolierte Vergleichssimulation mit gleichem Bedarf, Journal, Neustart- und Dublettentests | Nur modellierter Gewinn; Ausführung bleibt deaktiviert |

Vertiefungen: [Import](import-review.md), [Steuerung](control-review.md),
[Planung](planning-review.md). Die Abschlussdokumente ergänzen Betriebsdaten,
Abnahmekriterien, Risikoregister, Testmatrix und Rückfallverfahren.

## Bereits belegte Betriebskette eines Fehlers

1. [Importlauf 34312723317](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34312723317),
   09.09. 04:57:03 UTC: `Verlegung 031MFGE2PG000000VS5489BUVVKNMR0J: Ziel fehlt oder zyklische Verlegung`.
   Genau derselbe Fehler wurde per aktuellem read-only GET reproduziert.
2. Letzter guter Feed: 08.09. 04:51:22 UTC. 113 Spiele, 45 Platzspiele
   (40 Rasen, 5 Kunstrasen). Erzeugungszeit und erfolgreiche Nacht-Workflows
   dürfen nicht verwechselt werden: Außerhalb des Abruffensters läuft kein neuer Import.
3. [Bundlelauf 34283743125](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34283743125),
   08.09. 22:02 UTC: Quelldaten älter als 720 Min.; kein neues Bundle veröffentlicht.
4. [Vorheriger Bundlelauf 34225267114](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34225267114)
   am 08.09. 12:17 UTC erfolgreich. Ein Bundle-Publish ist kein Deployment der Steuerungssoftware.

## Grenzen

Keine Gerätebefehle, produktiven Konfigurationsänderungen, Deployments, Merges,
Rollenvergaben oder kostenpflichtigen Infrastrukturänderungen im Audit.
Alte ioBroker-Laufzeit, native Geräteschedules, Appack-Installation und
physische Stations-/Wegefreigabe sind noch nicht live nachgewiesen.
Die historischen 150 Minuten werden ohne belastbare Boden-/Wasserdaten nicht
verkürzt; ein Ladeereignis erzeugt keinen zusätzlichen Wasserbedarf.
