# Gesamtbewertung der SSV53-Platzpflegeplattform

**Begonnene Einführung:** Die [Startprüfung ab 17:41 Uhr](live-introduction-check.md)
berücksichtigt die neue Bestätigung zu Station und Zufahrt. Sie dokumentiert
die noch fehlende Zustandssicherung und die weiterhin ausgefallene alte
Steuerung. Es wurde bisher kein Deployment und kein Gerätebefehl ausgeführt.

**Aktueller Abschlussstand:** [Entwicklungsprüfung und Produktionsbefunde](final-preflight.md),
[verbindliche neue Betriebsregeln](user-operating-rules.md) und
[konkreter Freigabeablauf](final-activation-runbook.md). Der Liefernachweis steht
in [final-preflight-delivery.json](final-preflight-delivery.json). Der neue
[Winter-Schalter](../ui-2026-09-09/final-preflight/winter-switch-390.png) ist lokal
mit Beispieldaten geprüft; es erfolgte keine Veröffentlichung.

Die folgenden Abschnitte sind der **zeitgebundene Erstaudit**. Seine Testzahlen,
Pakete und damaligen Restarbeiten sind historisch. Der neue Bewässerungsbeginn
ab 03:30 ersetzt insbesondere den früheren 03:05-Modellstart und dessen Gewinn;
der neu gerechnete [Tagesvergleich](coordination-simulation.md) ist maßgeblich.
Die Feiertags- und Winterregel wurde inzwischen durch den Nutzer festgelegt.


**Neueste Erweiterung:** [Koordinierte Bewässerung während des Ladens](coordination-execution-update.md)
verbindet die Planung mit dem persistenten Ausführungsablauf. Sie enthält die
zusätzlichen Fehlernachweise, einfache App-Anzeigen, isolierte Ablaufprüfungen
und die weiterhin offenen Voraussetzungen für den Livebetrieb.

**Aktueller Folgestand:** Die bedingte Livefreigabe und die zusätzlichen
Entwicklungsänderungen am Import, Trainingsmodell, Statuscache und lesenden
Planungsvergleich sind in [release-readiness.md](../ui-2026-09-09/release-readiness.md)
zusammengefasst. Dort sind auch die noch offenen Integrationen benannt. Die
älteren Testzahlen und Paketnachweise dieses Erstberichts bleiben zeitgebundene
Nachweise; die Bedingung für den vollständigen Livegang ist noch nicht erfüllt.

**UI-Folgeänderung:** Die anschließend gewünschte einfache Anzeige mit klaren
Uhrzeiten liegt unter [docs/ui-2026-09-09](../ui-2026-09-09/README.md).
Die dortigen Aufnahmen, Tests und Paketnachweise ergänzen diesen Auditstand.
Die älteren UI-Aufnahmen und Paket-Hashes hier bleiben als früherer Stand erhalten.

Die geprüfte Plattform enthält behebbare Softwarefehler und zusätzlich offene
Geräte-/Betriebsrisiken. Der aktuelle Importausfall ist ursächlich erklärt und
als kleiner separater Main-PR vorbereitet. Die größere Entwicklungsänderung
behebt Freigabe-, Persistenz-, Berechtigungs-, Diagnose- und Messfehler. Eine
Ausweitung des unbeaufsichtigten Automatikbetriebs ist auf dieser Beweislage
noch nicht abnahmefähig.

Die Änderungen sind in zwei prüfbaren Entwürfen abgelegt:
[PR #46: Importfix gegen main](https://github.com/Rohdeo87/ssv53-heimspiele/pull/46)
und [PR #47: Steuerung, App und Audit gegen den Migrationsbranch](https://github.com/Rohdeo87/ssv53-heimspiele/pull/47).
Beide sind offen; es wurde nichts zusammengeführt oder produktiv veröffentlicht.

Besonders wichtig: Im vorhandenen Betrieb wurde Mäherbewegung während einer
Trocknungssperre protokolliert. Der verursachende Sender ist nicht nachgewiesen.
Eine erfolgreiche Cloudantwort oder ein grüner Deploymentworkflow kann diesen
fehlenden Gerätenachweis nicht ersetzen.

## Belastbare Ergebnisse

1. **Import live geprüft:** Die Quelle ist der öffentliche FUSSBALL.DE-
   Vereinsspielplan, kein authentifizierter DFBnet-Abruf. Eine Verlegung vom
   September in den Oktober ließ den Abruf vor dem zweiten Quartalsfenster
   abbrechen. Die korrigierte Verarbeitung ergab am 09.09. 113 Datensätze und 46
   lokale Belegungen statt zuvor 45; zusätzlich korrekt erkannt wird der
   Rasenblock am 08.10., 17:00–20:25 Uhr. [Importreview](import-review.md),
   [PR #46](https://github.com/Rohdeo87/ssv53-heimspiele/pull/46).
2. **Trocknung getrennt von Datenvertrauen:** Der bisherige gemeinsame Timer
   konnte eine vollständige Wartezeit neu beginnen lassen, in einem anderen
   Ablauf aber auch notwendige Trocknung verlieren. Der tatsächliche alte
   Git-Code wurde gegen identische synthetische Beobachtungen wiederholt.
   Im Einzelpoll-Beispiel entfallen 148 zusätzliche Sperrminuten; nach externer
   Bewässerung werden 127 notwendige Minuten wiederhergestellt. Die historische
   240-s-Lücke bleibt konservativ gesperrt. [Code-Replay](gap-replay.json).
3. **Robuste Steuerungsgrenzen entwickelt:** Ungültige Ventilwerte erlauben
   keinen Start; Dockbestätigung verfällt nach langer Lücke; START wird vor dem
   Senden persistent reserviert; ungeklärte Wirkung bleibt über Neustart und
   Dockmeldung gesperrt. Bindende Sperren sind nicht übersteuerbar. [Review](control-review.md).
4. **Berechtigungen und Anzeige vereinheitlicht:** Belegungsänderungen benötigen
   servergeprüfte Anmeldung; die bisherige Clientrolle genügt nicht. Die App
   zeigt mehrere Sperren, Datenalter, echte Trocknungsfrist, unbekanntes Ladeende
   und unbestätigte Befehlswirkung. Der tatsächliche FULL_FAILSAFE-Lesepfad ist
   ohne State-Schreiben getestet. [Architektur und Rollenwirkung](architecture.md).
   Die zusätzliche CI-Prüfung deckte einen echten Zeitzonenfehler beim Anlegen
   und Verlegen auf: Berliner Vereinszeit wird jetzt unabhängig vom Gerät als
   korrekter UTC-Zeitpunkt übermittelt; unklare DST-Eingaben werden abgewiesen.
5. **Produktivität nachvollziehbar gemessen:** Die Baseline enthält 2.870,59
   als MOWING ohne Fehler gemeldete Minuten. Heimfahrt, Fahrt zum Platz, Laden,
   Parken und Fehler werden getrennt; rund 609 Minuten tragen Code 93. Dies
   belegt API-Zustände, keine unabhängig gemessene Schnittqualität oder
   vermeidbaren Stillstand. [Betriebsnachweise](versions-and-evidence.md).
6. **Gemeinsame Planung simuliert:** Unter identischen angenommenen Bedingungen
   steigen produktive Modellminuten von 642 auf 720; Wasser 160 Minuten,
   Trocknung 150 Minuten und Restenergie bleiben vergleichbar. Eine einfache
   Laderegel erreicht hier dasselbe Ergebnis wie 54 Vorschaukandidaten.
   **Keine dieser 78 Minuten ist live nachgewiesen.** [Vergleich](coordination-simulation.md).

## Status der Arbeit

| Aussage | Status |
|---|---|
| Repositories, Historie, Infrastruktur, Trigger, Konfiguration, kritische Codepfade | Analysiert; 197 Ausgangspfade inventarisiert |
| FUSSBALL.DE-Quelle und vorhandene Azure-Betriebslogs | Live lesend geprüft |
| Fehlerkorrekturen, Diagnose, Auth-Grenze, Appack-Templates | Im isolierten Entwicklungsbranch umgesetzt |
| Gesamttests | 694 Python-Tests + 406 Untertests, 73 Node-Tests bestanden |
| Import-Hotfix separat auf aktuellem main | 119 Tests + 192 Untertests; CI am exakten Kopf grün |
| Kurze/lange Datenlücken, Konkurrenz, Neustarts, Tagesplanung | Offline reproduziert bzw. simuliert |
| Desktop-/Mobil-/Ausfallansicht und gemeinsame Zeitleiste | Lokal im Browser geprüft, Screenshots vorhanden |
| Neue Planung im produktiven Parallelbetrieb | Nicht beobachtet; kein Ausführungsadapter installiert |
| Neue Steuerungssoftware/Appack-Templates produktiv veröffentlicht | Nicht durchgeführt |
| Gerätewirksamkeit, sichere Vor-Ort-Abläufe, reale Mehrleistung | Nicht abgenommen |

## Dokumente und Artefakte

- [Architektur, Schnittstellen, Zeitregeln, Deckung und Quellen](architecture.md)
- [Versionen, aktuelle Fehlerketten und Ausgangsmessung](versions-and-evidence.md)
- [Priorisierte Verbesserungstabelle und Risikoregister](improvements-and-risks.md)
- [Sieben Einführungsstufen, Fehlerbehandlung und gerätebewusster Rückfall](rollout-and-operations.md)
- [Tests, unabhängige Reviews, Replay, Screenshots und offene Prüflücken](testing-and-coverage.md)
- [Interaktive gemeinsame Tageszeitleiste](coordination-comparison.html)
- [Appack-Vorschau mit synthetischen Werten](appack-preview.html)
- [Analyse vor Beginn der Entwicklungsänderungen](initial-analysis.md)
- [Commit-/PR-/Paket-/CI-Zuordnung](delivery.json)
- [Geprüftes Quellpaket und Hashes](package-evidence.json): 47 Dateien inklusive
  Manifest, alle enthaltenen Dateihashes geprüft. Das ZIP liegt lokal unter
  `dist/ssv53-platzpflege-audit-20260909-source.zip`, benötigt einen Azure-Remote-
  Build und ist nicht installiert. Die Paketerzeugung sendet keine Gerätebefehle.

Wesentliche offene Maßnahmen sind die exakte installierte Versionskette,
native Gerätezeitpläne/Queue, EPOS-Fehlerursache, Stations-/Weggeometrie,
Ventil-/Bedarfsnachweise, autorisierte Teamliste, verbindliche Saison-/Ferientage,
additive Importquarantäne und ein gemeinsames API-Abrufbudget. Jede hat einen
konkreten nächsten Schritt und eine Freigabegrenze in den genannten Dokumenten.

Nächster prüfbarer Produktionsschritt ist ausschließlich der freizugebende
Import-Merge mit kontrolliertem Datenlauf und anschließendem JSON-/ICS-/API-
Vergleich. Ein Mäher-/Bewässerungspilot bleibt eine gesonderte Entscheidung
nach Schließen der physischen und technischen Pilotbedingungen.

## Weitere Integration

Die [anschließende Integration](integration-update.md) verbindet die gemeinsame Trainingsquelle, sichert unabhängige Herstellerbeobachtungen und ergänzt feste Dateimomentaufnahmen. Neue UI-Kontrollaufnahmen, ein begrenzter Verschiebungsentwurf und genaue Aktivierungsgrenzen sind dort dokumentiert. Der technische Liefernachweis folgt unter [integration-delivery.json](integration-delivery.json); frühere Mess- und Paketstände bleiben getrennt erhalten.
