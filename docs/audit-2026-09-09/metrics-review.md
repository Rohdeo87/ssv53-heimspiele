# Unabhängiger Review von Betriebsmetriken und Freigabeanzeige

09.09.2026. Read-only-Abgleich des vorhandenen Exports, anschließende gezielt
beauftragte Korrektur dreier Tagesberichtfehler. Keine Mail, kein Gerätebefehl,
keine Änderung des Betriebsdatenexports.

## Reproduzierbarkeit der Ausgangsbilanz

`scripts/analyze_control_observations.py` reproduziert
`operating-baseline.json` aus dem vorhandenen bereinigten Export exakt:
02.09.2026 06:00 UTC bis 09.09.2026 05:00 UTC, 10.016 Zeilen, 10.020 Minuten
Auswertungszeitraum, 10.009,93 geschätzte Beobachtungsminuten und 99,90 %
Abdeckung. Die einzeln gerundeten Aktivitätswerte weichen in der Summe um
0,01 Minuten vom gerundeten Gesamtwert ab. Das ist Rundung, keine zusätzliche
Aktivität oder Doppelzählung.

Die 362,99 Minuten gemeldete aktive Bewässerung sind eine zusätzliche Spur.
Sie dürfen insbesondere nicht zur Lade-/Stillstandszeit addiert werden.
Wasserverbrauch, vermeidbare Sperrzeit und erfüllte Wasserbedarfe sind zu Recht
unbekannt (`null`). 2.870,59 Minuten Zustand MOWING bleiben ausdrücklich eine
Telemetrieschätzung und kein Nachweis physischer Schnittwirkung.

## Reproduzierte und korrigierte Tagesberichtfehler

| ID | Priorität | Reproduktion vorher | Ursache | Korrektur / Abnahme |
|---|---|---|---|---|
| MR01 | P2 | GOING_HOME um 09:00, nächste Probe erst PARKED um 09:30: Ø und P95 wurden beide als 30 Minuten ausgegeben. | `_return_durations_minutes` interpolierte die ganze Rückkehr über eine beliebig lange Datenlücke bis maximal 60 Minuten. | Jede Lücke über 90 Sekunden zensiert die Heimfahrt. Ein anschließender GOING_HOME-Rest wird nicht als vollständige kurze Heimfahrt neu begonnen. Erst eine neue beobachtete Zustandsfolge kann wieder zählen. |
| MR02 | P2 | `command_sent=true` um 09:00:01, `false` um 09:00:59: Tagesbericht zählte null Versuche. | `parse_cycle_rows` ersetzte die ganze Zustandsminute einschließlich ihrer Befehlsinformation. | Befehlsversuche werden separat an der Minute erhalten und über ihren ursprünglichen Ereigniszeitpunkt ausgewertet. Neu abgefragte Invocation-/Retry-Metadaten deduplizieren mehrfach eingegangene Meldungen. Eindeutig verschiedene Versuche in derselben Minute bleiben getrennt. |
| MR03 | P2 | Zwei frische MOWING-Proben mit 99 % und direkt danach 0 %, durchgehend `lastTimeCompleted=0`: Tagesbericht meldete einen „bestätigten Abschluss“; der Fußtext behauptete ausschließlich lastTimeCompleted als Quelle. | Die bestehende Fortschrittsheuristik ergänzte dieselbe Liste wie Gerätezeitstempel. Anzeige und Status verloren dadurch die Herkunft. Zusätzlich ließ die Zeitprüfung Geräteabschlüsse bis drei Stunden vor Berichtsanfang und aus der Zukunft zu. | Gerätezeitstempel und zusätzliche Fortschrittsschätzungen werden getrennt gezählt und angezeigt. Die bestehende 10-Minuten-Deduplizierung bleibt erhalten, auch wenn die Gerätemeldung erst später beobachtet wird. Gerätetimestamps müssen innerhalb des Berichtszeitraums liegen. |

Die Ausgabe heißt nun „Gemeldete Befehlsversuche 24 h“. Sie behauptet weder
Geräteannahme noch physische Ausführung. `command_sent` beschreibt die
Zyklusmeldung; die vorhandene Telemetrie kann nicht beweisen, ob hinter einer
Meldung mehrere einzelne API-Schreibschritte standen. Für ältere Zeilen ohne
Invocation-ID verwendet die Deduplizierung den genauen Ausführungs-/Tracezeitpunkt
und Entscheidungscode. Eine Identität tatsächlicher Gerätebefehle lässt sich
aus fehlender historischer Instrumentierung nicht nachträglich konstruieren.

In dem geprüften Export bleiben alle 145 gemeldeten Versuche auch bei bisheriger
Minutendeduplizierung erhalten. MR02 ist daher ein reproduzierbarer
Regressionsschutz, keine nachgewiesene Korrektur dieser konkreten Ausgangszahl.

Die 24-Stunden-Grenzen werden jetzt anhand des Versuchszeitpunkts geprüft. Eine
spätere Zustandsprobe derselben Minute kann einen alten Versuch weder in den
Zeitraum hineinziehen noch einen aktuellen Versuch daraus entfernen.
Exakte Duplikate zählen einmal; ein eigenständiger Retry zählt als weiterer
gemeldeter Versuch. Die Zustandsminute selbst verwendet unabhängig von der
Eingabereihenfolge die zeitlich letzte Probe.

Die bestehende 99→0-Heuristik wurde nicht fachlich neu bewertet oder erweitert.
Sie bleibt eine zusätzliche Schätzung: mindestens zwei MOWING-Proben mit
systematischem Fortschritt ≥99 %, Abstand höchstens 90 Sekunden, danach innerhalb
von 90 Sekunden ein Fortschrittswert ≤1 %. Das beweist keine physische
Schnittwirkung. Gerätezeitstempel werden zuerst gesammelt; eine Schätzung im
Abstand von höchstens zehn Minuten zu einer solchen Meldung oder bereits
gezählten Schätzung wird nicht zusätzlich aufgenommen. Das Zeitfenster ist die
vorhandene Heuristik zur Deduplizierung, keine nachgewiesene Ereignisidentität.

Der Statusvertrag ist nun ausdrücklich:

| Feld | Bedeutung |
|---|---|
| `completedAreaCycles7d` / `lastCompletedAreaUtc` | Ausschließlich eindeutige `lastTimeCompleted`-Zeitstempel innerhalb des Berichtszeitraums / letzter solcher Zeitstempel. |
| `inferredAreaCycles7d` / `lastInferredAreaUtc` | Zusätzliche, deduplizierte Fortschrittsschätzungen / letzter Zeitpunkt einer solchen Schätzung. |
| `estimatedAreaCycles7d` | Deduplizierte Gesamtschätzung aus beiden Zählwerten; zusätzliche geschätzte Ereignisse bleiben Schätzungen. |
| `areaCompletionNote` | Menschenlesbarer Hinweis auf beide Quellen, Deduplizierung und fehlenden unabhängigen Schnittwirkungsnachweis. |

Auch der Rückgabestatus des Tagesreportprozesses enthält die getrennten und
gesamten Zahlen. Text- und HTML-Bericht verwenden „Flächenabschlüsse laut Gerät“
und „Zusätzliche Abschlüsse – Schätzung“ sowie getrennte letzte Zeitpunkte.
Die bisherige unbegründete Wortwahl „Bestätigte Abschlüsse“ wurde entfernt.
**Integrationshinweis:** Verbraucher, die bisher aus `completedAreaCycles7d`
eine gesamte Flächenleistung ableiten, müssen den gewählten Wert und seine
Herkunft sichtbar benennen. Eine Gesamtschätzung darf nur aus
`estimatedAreaCycles7d` als ausdrückliche Schätzung angezeigt werden.
Console/Appack wurden in diesem begrenzten Metrikpatch nicht geändert.

Prüfung: `python -m pytest tests/test_daily_safety_report.py -q` — **31 bestanden**.
Neue Szenarien: Datenlücke bei Heimfahrt, nicht vollständig beobachteter Rest,
spätere neue Heimfahrt, zwei Versuche in einer Minute, Duplikat derselben
Invocation, eigenständiger Retry, unsortierte Zeilen und beide 24h-Randminuten.
Für MR03 zusätzlich: reine Gerätenachweise, reine Fortschrittsschätzung,
gemischte getrennte Ereignisse, wiederholte Gerätetimestamps, nahe doppelte
Schätzungen, verspätete Gerätemeldung für dieselbe Schätzung, Gerätezeitstempel
an und außerhalb der Berichtsgrenzen, Statusfelder sowie Quellenhinweis in
Text- und HTML-Bericht. Alle Mailtests benutzen einen lokalen Fake-Sender.
Der gemeinsame Lauf mit `tests/test_platzwart_console.py` bestand mit
**71 Tests und 10 Subtests**. `git diff --check` für diese Metrikdateien war
ohne Whitespacefehler.

## Weitere an die Integration gemeldete Befunde

Diese Punkte wurden im unabhängigen Review gemeldet. Ihre Korrektur liegt bei
der Integration; der endgültige Status ist im Abschlussbericht nachzuweisen.

- **Freigabesnapshot fehlt im FULL_FAILSAFE-Anzeigepfad:**
  `_coordination_payload` las ausschließlich
  `details.hydrawise.release_confirmation`. Der von `live_status` aufgerufene
  `run_read_only_cycle` erzeugte diesen Snapshot nur bei `ControlMode.DRY_RUN`.
  Reproduktion: COMPLETE_HOLD, Ende 09:40 UTC, jetzt 10:00 UTC, alle aktuellen
  Rohzustände frei — Ergebnis `dryUntil=null`, `releaseNotBefore=null`, keine
  Sperre, obwohl die physische Frist erst 12:10 UTC endet. Der neue Snapshot muss
  aus demselben persistenten Zustand und denselben Release-Regeln wie die
  Steuerung entstehen. Die UI darf ohne diesen Snapshot keine frühe Prüfuhrzeit
  aus einem normalen Platzfenster ableiten.
- **DST-Grenzprüfung im Offline-Baseline-Helfer:** `summarize` prüfte
  `start >= end` vor der UTC-Normalisierung. Bei zwei Europe/Berlin-Datetimes
  am 25.10.2026 wird 02:30 fold=0 bis 02:30 fold=1 trotz 60 realer Minuten
  abgewiesen; 02:10 fold=1 bis 02:50 fold=0 wurde als negative 20 Minuten
  akzeptiert. Die CLI und die vorhandene Baseline sind nicht betroffen, weil
  dort vorher UTC normalisiert wird. Die allgemeine Funktion muss vor dem
  Größenvergleich beide Zeitpunkte nach UTC umrechnen.

Die ursprüngliche Zwei-Zeilen-Korrektur, LEAVING nicht länger als produktives
Mähen zu zählen und fehlerbehaftete MOWING-Meldungen auszuschließen, bleibt
erhalten. Der Review erhebt keinen Anspruch auf eine Sensorbestätigung von
Rasenqualität, Messerlauf oder vollständiger Bewässerungsmenge.
