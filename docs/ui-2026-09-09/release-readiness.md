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
| O01 – Trainingskalender | Gemeinsames versioniertes Modell, zwei Projektionen für App und Mäher, Validator, Inhaltsbindung und lesender Abweichungsvergleich entwickelt. Deaktivierte Vorlage erhält bestehende Wochenmuster und Sperren. [Nachweis](../audit-2026-09-09/training-calendar-update.md) | Verbindliche Saison-/Ferienregeln, vollständige gemeinsame Laufzeitanbindung einschließlich Pflege, Absagen und Konfliktprüfung; noch kein Wechsel der Quelle. Die entsprechende fachliche Rückfrage wurde gestellt. |
| A01 – API-Abfragen | Gemeinsamer Cache mit erhaltenem Quellzeitpunkt, atomarer Abrufreservierung, Herstellerbudget, begrenztem Wiederanlauf und Ablehnung verspäteter Antworten entwickelt. Im befehlsfreien Lesepfad angeschlossen; Appstatus schreibt keinen Automatikzustand. [Nachweis](../audit-2026-09-09/api-cache-update.md) | Standard `OFF`. `PARK_ONLY`, `FULL_MOWER` und `FULL_FAILSAFE` lehnen die Cacheoption technisch ab. Weitere Gerätebestätigungen müssen zunächst echte neue Beobachtungen statt Zyklen zählen; erst danach ist eine vollständige Geräteintegration zulässig. Keine aktuelle Einsparung an API-Kosten behauptet. |
| P01 – Laden und Wasser gemeinsam planen | Vollständige Eingangskopien aus vorhandenen Zyklen und lokaler Vergleich mit empirischer Ladeprognose entwickelt. Unbekannte Zeiten bleiben unbekannt. Keine neuen Herstellerabfragen. [Nachweis](../audit-2026-09-09/shadow-integration.md) | Erfassung standardmäßig deaktiviert, keine neue Parallelbeobachtung. Fachlich bestätigte Bedarfsfenster und ein geprüfter Geräteadapter für Terminverschiebung und Unterdrückung des Ursprungstermins fehlen weiterhin. |
| V01 – Installationsnachweis | Diagnose prüft die im Manifest aufgeführten Quellbytes und unerwartete zusätzliche Anwendungsmodule. [Code](../../mower/build_provenance.py) | Diagnose noch nicht installiert. Paketdateien auf Datenträger sind kein Nachweis der Remote-Build-Abhängigkeiten, des bereits geladenen Codes oder des Geräteverhaltens. |
| U01 – Einfache App-Anzeige | Die bereits entwickelte einfache Anzeige mit „Jetzt“, „Als Nächstes“, klaren Uhrzeiten und gekennzeichnetem Ladeende bleibt erhalten. [Vorschau und Screenshots](README.md) | Tatsächliche Veröffentlichung, WebView-/Sitzungs- und Rollenprüfung auf dem Appgerät. |

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

1. **Funktionsumfang:** O01 und A01 haben noch offene Integrationspfade;
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

Der abgeschlossene lokale Gesamtlauf umfasst **917 Python-Tests, 425 Untertests
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
