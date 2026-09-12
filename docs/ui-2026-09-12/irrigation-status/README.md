# Bewässerungsstatus auf der Startseite – 12. September 2026

## Befund

Das Nutzerfoto um 06:56 Uhr zeigt „Mähermeldung ist älter“ und „Bewässerung beenden“.
Die Behauptung eines tatsächlichen Wasserflusses lässt sich daraus nicht ableiten.
Der vorhandene Stoppknopf erscheint bereits in den Vorbereitungsphasen.

Die rein lesend abgerufenen Azure-Protokolle um 06:55, 06:56 und 06:57 Uhr Berlin melden:

- Entscheidung `IRRIGATION_WAIT_FOR_CONFIRMED_PARK`, Phase `READY`.
- Hydrawise: verfügbar, frisch, `clear_now=true`, aktive Zonen `0`.
- Letzter Mäherzustand `PARKED_IN_CS`, Akku 100 %, aber alte Gerätemeldung.

[Die archivierten Ausschnitte](controller-observations.json) enthalten unveränderte relevante Felder aus den Steuerungszyklen. Der zweite Abruf umfasste 58 Zyklen; keine der beobachteten Meldungen bestätigte laufende Zonen. Dies ist ein Schnittstellennachweis, keine Beobachtung der Regner vor Ort. Die Rückfrage, ob der Nutzer Wasser gesehen oder den Betrieb aus dem Knopf abgeleitet hat, war bei Veröffentlichung noch offen.

## Ursache und Korrektur

Die Überschrift priorisierte den allgemeinen Mäher-Datenhinweis vor der konkreten Vorbereitung der Bewässerung. Gleichzeitig war der Beenden-Knopf unabhängig von tatsächlichem Wasserfluss beschriftet. Die vorhandene Vorrangregel für frisch bestätigtes Wasser war bereits korrekt; sie traf auf den protokollierten Fall nicht zu.

| Situation | Hauptmeldung | Bedienung |
|---|---|---|
| Vorbereiteter Lauf, aktuell keine Zone aktiv, Mähermeldung älter | **Bewässerung wartet** – „Es fehlt eine aktuelle Meldung vom Mäher. Bitte prüfen, ob er in der Station ist.“ | **Bewässerung abbrechen**; gezielte Bestätigung ohne vermeintlich laufende Zone |
| Frisch bestätigter Wasserfluss | **Bewässerung läuft** | Bestehender Stoppdialog |
| Laufphase, aber aktuell bestätigt keine aktive Zone | **Bewässerung macht Pause** – „Zurzeit läuft keine Zone. Bitte den Platz noch nicht betreten.“ | Stopp bleibt erreichbar |
| Lauf-/Startphase, Wasserfluss nicht ausreichend bestätigt | **Bewässerung nicht bestätigt** – Anlage vor Ort prüfen | Keine zusätzliche Freigabe |

Unbestätigte Mäherstarts, Gerätestörungen und ausgefallene Steuerung behalten ihre Warnpriorität. Ebenso bleiben die bestehenden Konfliktwarnungen bei Wasser und Mäherbewegung sowie die Unterscheidung zwischen automatischer Zeitgrenze und bestätigtem manuellem Bewässerungslauf erhalten.

Die Abbruchaktion verwendet unverändert `STOP_IRRIGATION_NOW`, dieselbe Berechtigungsprüfung, denselben Bestätigungsschritt und dasselbe Befehlsjournal. Keine Änderung an Scheduler, Stationsprüfung, Bewässerungsdauer oder Trockenzeit. Kein Gerätebefehl wurde zu Test- oder Veröffentlichungszwecken ausgelöst.

## Nachweise

- 96 bestehende gezielte Node-Tests bestanden: `test_appack_simple_dashboard`, `test_appack_wunschdesign`, `test_appack_parked_report`, `test_appack_coordination_execution`, `test_appack_return_status`.
- 6 zusätzliche Tests in [test_appack_irrigation_status.js](../../../tests/test_appack_irrigation_status.js) bestanden, einschließlich Wiederholung des aufgezeichneten 06:56-Zyklus, Stoppberechtigung, gesperrtem Start, Zonenpause, fehlenden Daten und Warnpriorität.
- `python scripts/sync_platzpflege_design.py --check`: HTML, Designquellen und CMS-Kopie synchron.
- Browserprüfung der echten Vorlage mit isolierten Beispieldaten: Überschrift „Bewässerung wartet“, passender Abbruchknopf und anschließender Bestätigungsdialog geprüft. CSP sperrt Netzwerkzugriffe; simulierte POSTs werden abgewiesen. Keine native Geräteabnahme und kein Screenshot-Nachweis in diesem Schritt.
- Vorschauen reproduzierbar mit `python -m scripts.build_irrigation_status_preview`; Fälle `waiting` (aufgezeichnete Zustandsfelder mit synthetischen Berechtigungen), `running` und `unknown` (Simulation).

## Veröffentlichung und Rückfall

Die zunächst geöffnete Appack-Sitzung war abgelaufen. Anmeldung mit den bereits hinterlegten Daten wurde wie vom Nutzer autorisiert durchgeführt. Die richtige Vorlage wurde danach im Editor vollständig gegen den lokalen Ausgangsstand abgeglichen und lokal als `appack-before.tpl` gesichert.

- Ziel: `Platzpflege.tpl`, ID `6a86ab6c4b3c829dd60de9b7`.
- Ausgangs-SHA-256, LF-normalisiert: `475f9788034952015a67f70bb5244cf6911130f5b6b3d2c21f0b3c678cf21b85`.
- CMS bestätigt die Speicherung am **12.09.2026 um 07:15 Uhr Berlin**.
- Öffentliche Auslieferung um **07:16 Uhr Berlin** gegen die drei geänderten Funktionen und den Stopp-Handler geprüft: [publication.json](publication.json).
- Prüfbefehl: `python scripts/verify_irrigation_status_publication.py` (nur lesender öffentlicher Abruf).

Rückfall: gesicherte Vorlage wiederherstellen oder die vier UI-Dateien aus dem Vorgängercommit beziehen und die alte Fassung erneut in Appack speichern. Es wurden keine Geräteaktionen geplant oder gesendet, die durch diese Anzeigekorrektur zurückgenommen werden müssten.

**Offen:** Die Korrektur beseitigt die missverständliche Anzeige, nicht die fehlende aktuelle Stationsmeldung. Bei tatsächlich beobachtetem Wasserfluss trotz der aufgezeichneten Nullmeldung muss die Hydrawise-Zuordnung beziehungsweise Rückmeldung separat untersucht werden. Die Anzeige darf diesen Widerspruch nicht durch eine erfundene Laufmeldung verdecken.
