# Abschlussprüfung: Lücken vor Livefreigabe

**Folgestand:** Die nachfolgenden Angaben beschreiben ihren damaligen Prüfstand.
Die anschließend umgesetzten Korrekturen, der zentrale Winter-Schalter, die
bestätigten Feiertagsregeln und das Bewässerungsfenster 03:30–08:00 sind im
[aktuellen Abschlussbericht](final-preflight.md) mit Nachweisen zusammengefasst.
Frühere offene Saison- und Uhrzeitentscheidungen sind dadurch ersetzt.

Stand: 09.09.2026, geprüfter Worktree-HEAD `bcacc44e64fac3926e16247e526408fa21fd07f3`.
Dies ist eine unabhängige, schreibfreie Lückenprüfung. Die lokale Entwicklung ist
umfangreich verifiziert, aber die Bedingung „alle Optimierungen umgesetzt“ ist
noch nicht erfüllt; insbesondere wurden weder Merge/Deployment/CMS-Veröffentlichung
noch Geräteaktionen durchgeführt.

## 1. Veraltete oder widersprüchliche Dokumentation

| Befund | Beleg | Korrektur vor Freigabe |
|---|---|---|
| Mehrere Teststände sind nur historische Zwischenstände, wirken aber im aktuellen Readiness-Dokument wie die jüngste Zusammenfassung. | `docs/ui-2026-09-09/release-readiness.md:71-91` nennt 917 bzw. 970 Python-Tests und 88/90 Appack-Tests; `docs/audit-2026-09-09/coordination-execution-update.md:158-165` und `coordination-execution-delivery.json:315-327` belegen für den aktuellen Ausführungsstand 1.020/425/102. | Readiness und Verweise auf einen einzigen, klar datierten HEAD-/Paketstand aktualisieren; alte Zahlen ausdrücklich als historisch markieren. |
| README-Status und Paketbeschreibung stammen aus dem früheren 694/406/73-Stand und nennen ein 47-Dateien-Paket. | `docs/audit-2026-09-09/README.md:80-105`; aktueller Ausführungsnachweis: `coordination-execution-update.md:226-232` (56 Quelldateien, Paket-SHA) und `coordination-execution-delivery.json:1-8`. | Status-/Paketlinks auf den aktuellen Nachweis umstellen; historische Nachweise separat labeln. |
| I03/O01/A01-Textstände sind zeitlich gemischt: die Readiness-Tabelle behauptet gemeinsame Laufzeitanbindung, der Trainingsbericht grenzt gemeinsame Laufzeitdateien weiterhin aus. | `docs/ui-2026-09-09/release-readiness.md:14-19` gegen `docs/audit-2026-09-09/training-calendar-update.md:122-147`. | Einen verbindlichen Status je Commit festhalten: entwickelt/offline geprüft, produktiv aktiviert oder nicht; keine Sammelaussage „umgesetzt“ ohne Laufzeit-/Aktivierungsbeleg. |
| Der priorisierte Risikoeintrag P02 beschreibt noch einen Verbraucher „bisher ohne persistenten Verbraucher“, obwohl der aktuelle Ausführungsbericht genau diesen Pfad dokumentiert. | `docs/audit-2026-09-09/coordination-execution-update.md:169-172` gegen `:187-205`. | P02 als „persistent implementiert, Live-/Geräteabnahme offen“ fortschreiben. |
| Der „nächste“ Schritt im Betriebsplan zeigt noch auf den alten Import-PR-Kopf und kann mit dem aktuellen Audit-HEAD verwechselt werden. | `docs/audit-2026-09-09/rollout-and-operations.md:95-114`. | Nach erneutem PR-/Basis-/CI-Check den tatsächlichen externen Status eintragen; bis dahin als ausstehende historische Handlungsanweisung kennzeichnen. |

## 2. Unabhängig erledigbare Entwicklungs- und Prüfaufgaben

| Aufgabe | Warum sie noch offen ist | Vorhandenes Artefakt / Abschlusskriterium |
|---|---|---|
| Dokumentations- und Nachweisstand auf `bcacc44` konsolidieren. | Die Drift in Abschnitt 1 ist redaktionell und kann ohne externe Aktivierung behoben werden. | `coordination-execution-delivery.json`, `coordination-execution-tests.xml`, `coordination-execution-appack-tests.txt`; danach Links, Zahlen und Status maschinell gegen HEAD/Paket prüfen. |
| Aktuellen Quellpaketnachweis reproduzieren und als Freigabeartefakt referenzieren. | Der Nachweis ist vorhanden, aber Remote-Build und Installation bleiben getrennt. | `scripts/verify_full_failsafe_delivery.py` gemäß `coordination-execution-update.md:218-224`; Abschluss nur mit neuem Bericht für den tatsächlich zu veröffentlichenden Commit. |
| I03-Consumer/Publisher-Vertrag als einheitlichen, kontrollierten Rolloutpfad dokumentieren und prüfen. | Readiness verlangt, dass der kompatible Consumer vor dem additiven Publisher läuft. | `docs/audit-2026-09-09/import-additive-update.md` plus `release-readiness.md:80-83`; prüfbar durch Bundle-Vergleich, Neustart-/Quellverlustfälle und monotone Versionsprüfung vor Aktivierung. |
| O01-Kalendervorlage mit einer versionierten, validierten Freigabe befüllen und den befehlsfreien Tagesvergleich wiederholen. | Der Adapter ist getestet, aber `enabled=false`, Saison-/Ferienregeln und Produktionsumschaltung fehlen. | `training-calendar-update.md:149-173`; Codepfad/Tests sind vorhanden, die fachlichen Eingaben müssen zuerst als unveränderliches Paket vorliegen. |
| A01-Lesepfad weiter verifizieren, ohne Gerätepfad freizuschalten. | Cachetests sind offline grün; gemeinsamer Azure-Table-/Rollenvertrag, Alarmreaktion und reale Aufrufmischung fehlen. | `api-cache-update.md:80-108`; vorhandene Tests decken Fakes ab. Ergänzbar sind statische Konfigurations-/Consumer-Prüfungen und ein befehlsfreier Paralleltest, sobald die echte gemeinsame Ressource lesend freigegeben ist. |
| Release-Matrix automatisch auf Standardflags und Sicherheitsgates prüfen. | Drei Erweiterungen sind absichtlich deaktiviert; aktivieren ohne vollständige Nachweise wäre eine Freigabeumgehung. | `api-cache-update.md:13-19`, `coordination-execution-update.md:187-205`, `release-readiness.md:15-18`; Abschluss ist ein reproduzierbarer OFF-/Gate-Test im finalen Paket. |

Diese Arbeiten schließen Software-/Nachweisrisiken, erzeugen aber keinen Beleg für
Gerätewirksamkeit oder einen produktiven Rollout.

## 3. Tatsächlich externe Fakten, menschliche Entscheidung oder Aktivierung

| Lücke | Externer Nachweis, der erforderlich ist | Grenze |
|---|---|---|
| Import-I01/I03 | Exakten PR-Kopf, Basis, grüne Pflichtchecks und Mergebarkeit prüfen; danach kontrollierter Quellenlauf und JSON/ICS/API/Runtime-Manifest vergleichen. | `rollout-and-operations.md:99-107`; Merge/Veröffentlichung ist eine externe Releaseaktion. |
| Trainingskalender O01 | Verantwortete Sommer-/Wintertage 11.08.2026–09.07.2027, Ferien-/Feiertagsregeln, Ausnahmen, Mannschaften/Plätze/Zeiten und Freigabezeit bestätigen. | `training-calendar-update.md:149-168`; öffentliche Ferienkalender ersetzen keine Vereinsentscheidung. |
| Hydrawise A01 | Gemeinsame Table/Managed Identity, Controller-/Relay-Vertrag, Hersteller-Abfragetakt, synchronisierte Hosts, echte 429/Retry-After- und `escalation_required`-Reaktion im Read-only-Betrieb nachweisen. | `api-cache-update.md:106-112`; Gerätepfade bleiben bis zur unabhängigen Bestätigungsketten-Prüfung gesperrt. |
| Versionskette V01 | Remote-Build, Deployment, tatsächlich installierte Bytes/Manifest und laufende Versionsmeldung lesend abgleichen. | `release-readiness.md:18,36-43`; lokales ZIP oder Azure-Status „Running“ genügt nicht. |
| Appack U01 | CMS-Veröffentlichung, echte WebView-/Sitzung-/Rollenprüfung und Berechtigungsänderung am Zielgerät durchführen. | `release-readiness.md:19,52-56`; lokale Browseraufnahmen sind nur Vorschauen. |
| P01/P02 und S01 | Bedarfsfenster, native Pläne/Queues und weitere Scheduler abgleichen; Station, Zufahrt, Ventile, Rückkehr und lokale Stoppmöglichkeit vor Ort prüfen; anschließend beaufsichtigte Parallelbeobachtung und begrenzten Pilot ausführen. | `coordination-execution-update.md:195-205`; simulierte Station/Ventile sind ausdrücklich Testannahmen (`:154-156`). Kein unbeaufsichtigter Nachtbetrieb. |

Bis diese externen Nachweise vorliegen, bleiben `HYDRAWISE_STATUS_CACHE_MODE`,
`SHARED_TRAINING_MODE` und die koordinierte Ausführung in ihren sicheren
deaktivierten Zuständen. Die vorhandenen Artefakte schließen die Offline-
Entwicklungsprüfung, aber nicht die Livefreigabe.
