# Bedingte Livefreigabe und tatsächliche Bereitschaft

Stand 09.09.2026. Die Nutzerfreigabe lautet: **„Ok wenn wirklich alle
Optimierungen umgesetzt sind kann es live gehen“. Diese Bedingung ist noch
nicht erfüllt.** Es erfolgten kein Merge, keine Veröffentlichung im Appack-CMS,
kein Softwaredeployment, keine Geräteaktion und keine produktive Aktivierung
einer neuen Option. Die Zustimmung ist für einen späteren, vollständig
nachgewiesenen Stand festgehalten; sie ersetzt keine fehlenden Betriebsdaten.

## Was seit der Freigabe zusätzlich bearbeitet wurde

| Maßnahme | Tatsächlicher Entwicklungsstand | Noch offen |
|---|---|---|
| I03 – Neue und bisherige Belegung | Eigenständiger Publisher übernimmt neue validierte Sperren und behält unbestätigt zurückgenommene Intervalle im selben JSON-/ICS-Bundle. Unabhängige Prüfung und Fehlerfälle einschließlich Neustart, Quellverlust und paralleler Veröffentlichung. [Nachweis](../audit-2026-09-09/import-additive-update.md) | Zusammenführung, kontrollierter Quellenlauf und Prüfung des anschließend tatsächlich verwendeten Bundles. |
| O01 – Trainingskalender | Gemeinsame Quelle und Laufzeitanbindung von App, Mäher, Absagen, Verlegungs-Lookup und Konfliktprüfung umgesetzt. Unveränderliche Eingabekopien verhindern Versionswechsel während der Verarbeitung. [Nachweis](../audit-2026-09-09/integration-update.md) | Standard `SHARED_TRAINING_MODE=OFF`. Verbindliche Saison-/Ferienregeln und Freigabe des konkreten Kalenders fehlen; die Rückfrage wurde gestellt. Noch kein Wechsel der produktiven Quelle. |
| A01 – API-Abfragen | Gemeinsamer Cache im lesenden Pfad; Geräteketten verwenden tatsächliche Quellzeitpunkte. Wiederholungen verlängern keine Bestätigung und starten keine physische Trocknung neu. [Nachweis](../audit-2026-09-09/device-observation-update.md) | Cache bleibt `OFF`, Gerätebetriebsarten lehnen ihn ab. Herstellerbudget plus Minutentimer kann 120 Sekunden Abstand verursachen; die Revalidierung erlaubt höchstens 90 Sekunden. Zulässiger Takt ist vor Aktivierung nachzuweisen. Keine aktuelle Kosteneinsparung behauptet. |
| P01 – Laden und Wasser gemeinsam planen | Lokaler Vergleich und ein deterministischer Verschiebungsentwurf mit gleicher Zonenfolge, gleichen Pausen/Dauern sowie 45-Minuten-Vorlauf umgesetzt. Beispiel: höchstens 45 Minuten früher nutzbarer Platz; keine gemessenen Mähminuten. [Nachweis](../audit-2026-09-09/integration-update.md) | Erfassung deaktiviert, kein neuer Parallelbetrieb. Bedarfsfenster, Bedarfsspeicherung und Geräteverbraucher einschließlich bestätigter Unterdrückung des Ursprungstermins fehlen als gemeinsamer Ausführungspfad. |
| V01 – Installationsnachweis | Diagnose prüft die im Manifest aufgeführten Quellbytes und unerwartete zusätzliche Anwendungsmodule. [Code](../../mower/build_provenance.py) | Diagnose noch nicht installiert. Paketdateien auf Datenträger sind kein Nachweis der Remote-Build-Abhängigkeiten, des bereits geladenen Codes oder des Geräteverhaltens. |
| U01 – Einfache App-Anzeige | Jetzt, Als Nächstes, klare Uhrzeiten und gekennzeichnetes Ladeende bleiben erhalten. Bei gemeinsamer Quelle entfällt die Saisonwahl; beide Plätze bleiben auswählbar. [Neue Kontrollvorschau](shared-training-preview.html) und [bisherige Gesamtansicht](README.md). | Tatsächliche Veröffentlichung, WebView-/Sitzungs- und Rollenprüfung auf dem Appgerät. |

„Entwickelt“ und „getestet“ bedeuten hier ausschließlich den Entwicklungsbranch.
Deaktivierte Optionen sind keine vollständige funktionale Einführung. Die
weitere fachliche Entscheidung zu Nachtbetrieb, Bewässerungsbedarf, zulässigen
Verschiebungen und Rasenbefahrbarkeit wird nicht durch günstigere Modellzahlen
ersetzt. Sicherheits-, Sport- und Trocknungsregeln wurden nicht verkürzt.

## Nachweis vor den weiteren Entwicklungsänderungen

Die erste aktuelle Prüfung betraf HEAD
`734656bc0fe24c2d77d33e9cca60fc371854723b`, mit Laufzeitcode aus
`325a5564a1af71ff2a3d3b73241da2771f16f431`. Beide PRs waren offen, als Entwurf
markiert und nicht zusammengeführt. Ihre exakten geprüften Köpfe und damaligen
Paketnachweise stehen im [maschinellen Prüfstand](release-readiness.json).
Dieser zeitgebundene Prüfstand behauptet keine CI-Prüfung späterer Änderungen.

Die Azure-Ressource meldete `Running` und HTTPS. Ihr Änderungszeitpunkt
09.09., 08:11:58 UTC ist kein Softwareversionsnachweis. Der Deploymentabruf
ergab keine eindeutige vollständige Bestätigung der installierten Bytes.
In drei vorhandenen Steuerlogs von 09:38–09:40 UTC wurde der Mäher als verbunden
und mähend mit Fehlercode 0 gemeldet; es wurden keine aktiven Wasserzonen
gemeldet. Ein Versionsnachweis der neuen Software war darin nicht vorhanden.
Der frühere Fehlercode 93 wurde damit **nicht** als aktuell fortbestehend
dargestellt. Die Meldungen beweisen keine sichere Fläche, Station oder Ventile.

## Warum der vollständige Livegang weiterhin wartet

1. **Funktionsumfang:** O01 benötigt einen verbindlich freigegebenen Kalender, A01 einen nachgewiesenen zulässigen Abfragetakt;
   die vorgezogene Bewässerung besitzt weiterhin keinen produktiven Adapter.
2. **Steuerverantwortung:** Ursache der protokollierten Bewegung während
   Trocknungssperre, native Geräteprogramme, weitere Scheduler und ausstehende
   Befehle sind nicht vollständig abgeglichen.
3. **Versions- und Bediennachweis:** Tatsächlich installierte Software und
   Appack-Version sowie berechtigte Pflege über echte App-Sitzungen sind offen.
4. **Betriebsabnahme:** Station, Anfahrtswege, Ventilverhalten, Rückkehr und
   Wiederherstellung nach Ausfall benötigen überprüfbare Nachweise. Neue
   Parallelbeobachtung und beaufsichtigter Pilot wurden nicht durchgeführt.

Der [gestufte Betriebsplan](../audit-2026-09-09/rollout-and-operations.md)
bleibt die technische Grundlage. Die dort genannten Beobachtungsdauern sind
Vorschläge, keine bereits vom Nutzer bestätigten Kalenderfristen. Fachliche
Eingänge, Verantwortliche, lokale Stopmöglichkeit, ein konkreter Pilotzeitraum
und ein gerätebewusster Rückfall müssen vor dessen Ausführung feststehen.
Die bedingte Zustimmung wird dabei berücksichtigt; eine bereits erteilte
Freigabe muss nicht ohne Anlass erneut pauschal erfragt werden.

Ein grüner Testlauf, ein gebautes ZIP oder veröffentlichte Belegungsdaten
erfüllen diese Bedingungen jeweils nicht allein. Die aktuelle Versions-/Paket-
und Testzusammenfassung steht in
[follow-up-delivery.json](../audit-2026-09-09/follow-up-delivery.json).

Der vorherige lokale Gesamtlauf umfasste **917 Python-Tests, 425 Untertests
und 88 Appack-Tests**, jeweils ohne Fehler. Der Quellstand ist
`285d0018b9064ca7fe05c5822af9f808d4e16d03`. Das neue FULL_FAILSAFE-Quellpaket
enthält 51 Einträge einschließlich Manifest. Alle 50 Quelldateien wurden direkt
aus diesem Git-Commit gebaut, byteweise verglichen und anschließend ohne
Netzwerkzugriff importiert. So wird eine abweichende Windows-Zeilenumwandlung
nicht mit dem tatsächlichen Commitinhalt verwechselt. Das getrennte
GitHub-CI-Quellpaket hat weiterhin die Stufe `DRY_RUN_READ_ONLY`.

Bei der späteren Einführung muss der kompatible Importverbraucher vor dem
additiven Publisher bereitstehen. Alte Verbraucher lehnen das neue
Rückhalteformat zwar ab, übernehmen dadurch aber auch keine neuen Sperren.
Ein kontrollierter Rollout muss beide Seiten der Verarbeitung nachweisen.

Die anschließende Integration ist mit **970 Python-Tests, 425 Untertests und
89 Appack-Tests** geprüft. Zusätzliche gezielte Prüfungen folgten für die
Vortagsabfrage nächtlicher Absagen. Die neuen lokalen Kontrollaufnahmen sind
Vorschauen mit Testdaten. [Änderungen, Wirkung, Risiken und verbleibende
Grenzen](../audit-2026-09-09/integration-update.md).
Der neue Byte-/Paketnachweis steht getrennt vom historischen Stand in
[integration-delivery.json](../audit-2026-09-09/integration-delivery.json).
