# Heimspielimport und Belegung – unabhängige Prüfung

Stand: 09.09.2026. Arbeitsbasis `audit/platzpflege-20260909`, Ausgangscommit
`9c2d0fc9c010b366cd49b58b8086b27f9d59a0dd`; tatsächliches `origin/main`
nach explizitem Ref-Fetch `d67e9d3fef24117f504a192c9ecaee998612ddd7`.
Der zuerst lokal vorhandene main-Ref `de198a97ed7e50fde5bd9328e24019921b2b657d`
war wegen eines Branch-beschränkten Fetch-Refspecs veraltet. Beide main-Stände
haben denselben hier relevanten Importcode; ihre Betriebsdaten unterscheiden sich.
Diese Untersuchung weist zunächst
Repositoryverhalten nach. Sie weist weder eine installierte Azure-Version noch
physisches Geräteverhalten nach. Die technischen Prüfungen lösen keine
Gerätebefehle oder Veröffentlichungsworkflows aus.

## Tatsächlicher Datenweg und geprüfter Umfang

`config.json` → öffentlicher FUSSBALL.DE-Vereinsspielplan als HTML über
`/ajax.club.matchplan/` → `poc_scraper.py` (adaptive Datumsfenster, Zuordnung nach
tatsächlicher Spielstätte) → `generated/*`, Qualitätsbericht und Teamregister →
`create_feed.py` (`matches.json` Schema 2, Rasen-/Kunstrasen-ICS) →
`report_changes.py` → GitHub-Repository `public/` → separater Runtime-Bundle-
Workflow → Azure Blob `current/manifest.json` → `occupancy/runtime_source.py` →
`occupancy/service.py` → Belegungs-API und App. Der Mähplan verwendet zusätzlich
die gepufferte Rasen-ICS und eine eigene Trainingskonfiguration; deren
Zusammenführung wird in der übergreifenden Prüfung vertieft.

Es ist **kein authentifizierter DFBnet-Direktabruf**. Es gibt weder DFBnet-Login
noch eine Exportdatei als Quelle. Die offizielle DFBnet-Dokumentation bestätigt,
dass die Freigabe des Spielplans zur Veröffentlichung auf FUSSBALL.DE führt;
zurückgenommene Freigaben können Daten dort wieder verbergen. Ein verschwundenes
Spiel ist damit nicht automatisch eine bestätigte Platzfreigabe.
[DFBnet: Spielplan](https://portal.dfbnet.org/de/service/online-hilfe/pokalspiele/spielplan)

Die offizielle Widget-Anleitung beschreibt einen Fan-Account, Widget-Erstellung
im Profil und Einbindung per Widget-Code. Sie stellt keinen Vertrag für den hier
verwendeten HTML-Ajax-Endpunkt bereit. Aus der Erreichbarkeit wird deshalb keine
garantierte API-Stabilität abgeleitet.
[FUSSBALL.DE: aktuelle Widget-Anleitung](https://portal.dfbnet.org/fileadmin/content/downloads/handbuecher/FUSSBALL_DE/Widgets_Next.pdf)

Geprüft werden Scraper, Timing-/Feedmodell, Qualitätswächter, Änderungswächter,
Zeitfensterschutz und Update-Workflow sowie Belegungsservice, Runtime-Quelle,
Trainingskonfiguration, Absagen und Sonderbelegungsgrenzen. Nicht verfügbar ist
eine unabhängig autorisierte vollständige DFBnet-Mannschaftsmeldeliste. Die
Vollständigkeit aller Vereins-/Spielgemeinschaftsmeldungen kann deshalb nicht
allein aus dem vereinweiten HTML-Abruf bewiesen werden.

## Ausgangsbefunde vor Änderungen

| ID | Problem und Ursache | Nachweis | Priorität und sichere Korrektur |
|---|---|---|---|
| IMP-01 | Entwicklungsbranch liegt bei Import-Schutz hinter main: einzelne künftige Entfernungen und Kürzungen werden sofort übernommen. Der Wächter kennt nur leeren Feed und großen Mengenverlust. | `report_changes.py::destructive_guard`, kein `confirmation-state`; main enthält bereits zwei zeitlich getrennte Beobachtungen. | P0: überprüfte Härtung aus main gezielt integrieren, einschließlich Persistenz im Workflow und Regressionstests. |
| IMP-02 | Der Branch lässt beliebige Redirect-Ziele, unbeschränkte HTML-Antworten und externe Quelllinks zu. | `Client.get_text` folgt Redirects standardmäßig; Quelllinks nur mit `urljoin`. main enthält Herkunfts-/Größenprüfung. | P1: vorhandene main-Härtung übernehmen; nur HTTPS/www.fussball.de, begrenzte Redirects/Bytes, keine Umgehung von Challenges. |
| IMP-03 | Abgesetzte oder ausgefallene Spiele können weiterhin als lokale Belegung enthalten bleiben. | `apply_venue_rules` im Branch behandelt nur „spielfrei“ gesondert. | P1: eindeutige Nichtstattfinde-Status aus main übernehmen; Entsperrung erst durch IMP-01 bestätigen. Puffer unverändert. |
| IMP-04 | Strukturierter Belegungsimport akzeptiert zeitzonenlose Matchzeiten, leere Quell-ID nach Präfixbildung sowie widersprüchliche Platzfelder. | `_structured_match_events` nutzt Request-Datumsparser mit impliziter Berliner Zone; ID wird vor Pflichtprüfung mit `match:` versehen; calendar gewinnt still vor place. | P1: Matchdaten explizit strikt validieren; eindeutige ID, Zeitversatz und konsistente Ressource. |
| IMP-05 | Fehlende Legacy-ICS wird als leere Eventfolge behandelt; unvollständige VEVENTs werden still übersprungen. | `_iter_ics_events` und `_legacy_ics_match_events`. | P0: fehlende/beschädigte Quelle muss als Fehler sichtbar werden; gültig leere Kalender bleiben unterscheidbar. |
| IMP-06 | Ein wartender Rücknahme-Kandidat blockiert den gesamten Feed, damit auch gleichzeitig neu hinzugekommene bzw. früher verlegte Spiele. | Workflow veröffentlicht nur bei vollständig erfolgreichem Change-Guard. Auch main-Guard hat diese Eigenschaft. | P0: kontrollierte additive Übernahme plus getrennte Altintervall-Sperren entwerfen. Bis dahin Bedien-/Betriebsprozess muss neue Sperren unabhängig nachpflegen; nicht als behobene Lücke ausweisen. |
| IMP-07 | App und Mähplan haben getrennte Trainingsdaten, Saisonwahl und Gültigkeitsgrenzen. | `occupancy/config.json`: Sommer/Winter; `mower/config.json`: acht Sommer-Rasenserien in ganzjährigem Bereich; API default Sommer. | P1: gemeinsame verbindliche Trainingsgrundlage und Konfigurationsvergleich; keine Termine still verschieben. |
| IMP-08 | Paketquelle wird ohne Quellalterprüfung als frisch gemeldet; Kalender-Generierungszeit ist nicht Quellenzeit. | `resolve_occupancy_match_source`, Zweig dynamic=false; `generated_at_utc` im Service. | P1: Herkunft, Quellalter und Abdeckungsgrenzen explizit ausweisen; Sicherheitsconsumer dürfen „Anzeige neu“ nicht mit „Quelle neu“ verwechseln. |
| IMP-09 | Verlegung über eine Datumsfenstergrenze bricht den gesamten Saisonimport ab. Der Parser fordert den Zieltermin vor Abruf des nächsten Fensters. | Live-Q3-GET und GitHub-Lauf 34312723317: identischer Fehler für `031MFGE2PG000000VS5489BUVVKNMR0J`. | P0: erst alle gültigen Zeitfenster erfassen, dann eindeutige identitätsgeprüfte Verlegungskette auflösen. Vier Requests bleiben vier Requests. |
| IMP-10 | Wiederholte Ausführung des Change-Reports zählt Laufzeit als neue Quellenbeobachtung; beschädigte Bestätigungszähler können fälschlich vertraut werden. | Bestätigung verwendet `datetime.now`, gespeicherte Zähler werden ungeprüft übernommen. | P1: `generatedAt` der Quellenerfassung verwenden; ungültigen/aufgeblähten Persistenzzustand konservativ neu beginnen. |

Die Anfangsdaten des Arbeitsbranches stammen vom 18.08.2026 20:07:58 UTC:
107 gelesene Datensätze, 42 aufgenommen (37 Rasen, 5 Kunstrasen), 0 Review,
65 ausgeschlossen. Das ist ein belegter historischer Repositorybestand, kein
aktueller Produktionsstand. Live-Stichprobe und Ergebnisse werden unten ergänzt.

## Verbindliche Regeln und Grenzen

- Spiele: `event_timing.before_minutes/after_minutes` und Belegungskonfiguration
  enthalten jeweils 60 Minuten. Der Feed trennt sichtbares Spielintervall von
  `occupancyStart/occupancyEnd`. Die ICS ist bereits gepuffert; der Mähplan muss
  `already_buffered=true` beachten.
- Training: Mähkonfiguration enthält 30 Minuten davor/danach. Die App liefert
  bisher nur sichtbare Trainingszeit. Historische Puffervorgaben werden hier
  nicht verkürzt.
- 06–22 Uhr ist im `schedule_guard.py` **das Abruffenster der Fußballquelle**.
  Der stündliche GitHub-Trigger erzeugt wegen 240-Minuten-Frischegrenze im
  Normalfall etwa vierstündliche Quellenabfragen. Nachtänderungen werden damit
  erst morgens erkannt; ein Nachtmähverbot lässt sich daraus nicht ableiten.
- Saisonzeitraum des Abrufs: 01.07.2026–30.06.2027; Training gültig
  11.08.2026–09.07.2027. Ein automatischer Saisonwechsel der Quellkonfiguration
  ist nicht vorhanden. Ferien sind nicht konfiguriert. Drei explizite
  Trainingsabsagen am 18.12.2026 und zwei ganztägige Kunstrasen-Sondertermine sind
  vorhanden. Fehlende Ferienregeln werden nicht als Trainingsfreiheit gedeutet.
- Adaptive Teilung bei mindestens 50 Quellzeilen/„mehr Ergebnisse“, harte Grenze
  10 Requests, mindestens 3 Sekunden Abstand, maximal ein Retry, 429-/Challenge-
  Sperren und Abdeckung aller Teilfenster sind sinnvolle vorhandene Schutzregeln.
  Ein vollständig fehlendes Team ohne Quellzeilen kann dadurch jedoch nicht
  bewiesen erkannt werden. `not_seen_in_current_run` im Teamregister ist Diagnose,
  keine verbindliche Meldeliste.
- Rasen und Kunstrasen werden anhand der lokalen Spielstätte/Platznummer
  zugeordnet; Perwenitz/Paaren ausgeschlossen. Heimrecht allein entscheidet nicht
  über Belegung. Unbekannte lokale Platzzuordnung führt zu Review/Publikations-
  sperre. Dies schützt auch neutrale Spiele und Spielgemeinschaften, soweit sie
  im Vereinsabruf enthalten sind.

## Geplante Abnahme der Entwicklungsänderungen

Offline: einzelne künftige Rücknahme/Kürzung, identische Wiederholung vor/nach
60 Minuten, Kandidatenwechsel, beschädigter Persistenzzustand, sichere Erweiterung,
Absetzung, Quellhost-/Redirect-/Größengrenzen, leere/fehlende/defekte ICS, fehlende
Match-ID, doppelte ID, unklare Zeitzone, widersprüchliche Platzfelder, Mitternacht
und beide Zeitwechsel. Tests verwenden ausschließlich lokale Fixtures und Mocks.
Es werden keine echten Gerätebefehle ausgelöst.

## Live-Nachweis und belegte Fehlerkette

Der tatsächliche aktuelle Repositorybestand auf main hat als letzten guten
Quellenabruf **08.09.2026 04:51:22 UTC**: 113 Datensätze, 45 aufgenommen
(40 Rasen, 5 Kunstrasen), 0 Review. `state/request_state.json` weist am
09.09.2026 04:57:03 UTC `last_status=failed`, einen HTTP-Request, Status 200 aus.
Der Hauptaudit hat das entsprechende GitHub-Log unabhängig gelesen: identischer
Verlegungsfehler und Quellenalter 1445,6 Minuten.
[Fehlgeschlagener Importlauf](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34312723317)

Der September-Stichprobenabruf am 09.09.2026 05:51:20 UTC (07:51 Uhr Berlin)
lieferte HTTP 200, 68.792 UTF-8-Bytes. Der Parser brach an der expliziten Verlegung
12.09. → 08.10. ab. Der anschließend exakt wie konfiguriert abgerufene
Q3-Zeitraum 01.07.–30.09. lieferte um 05:52:40 UTC ebenfalls HTTP 200
(101.675 UTF-8-Bytes) und denselben Fehler. Es war keine Login-, CAPTCHA- oder
Netzwerkstörung. Ein HTML-Status 200 war daher kein erfolgreicher Import.
[Monatsnachweis](import-live-sample.json), [Quartalsnachweis vor Korrektur](import-live-quarter-before.json)

Die Korrektur wurde zunächst offline getestet und danach als **lokaler,
ausschließlich lesender Quellenabruf** überprüft: 09.09.2026 05:56:01–05:56:12 UTC,
voller konfigurierter Saisonzeitraum 01.07.2026–30.06.2027, vier HTTP-200-Antworten
mit 41 / 36 / 12 / 28 Quellzeilen je Quartal. Nach Auflösung der mehrfach
dargestellten Verlegungszeilen: **113 eindeutige Datensätze aus neun
Mannschafts-IDs, 46 lokale Belegungen (41 Rasen, 5 Kunstrasen), 67 ausgeschlossen,
0 Review**. Keine Pagination-Lücke, keine fehlende Spiel-ID oder Festivalgruppe
wurde in den Antworten festgestellt. Das beweist die vollständige Verarbeitung
dieser Antworten, nicht die Vollständigkeit aller vom Verband gemeldeten Teams.
[Vollständiger maschinenlesbarer Saison-/Vergleichsnachweis](import-live-season-after.json)

| September-Stichprobe, Mannschaft nach ID zusammengefasst | Termine |
|---|---:|
| A-Junioren, Schönwalder SV / Schönwalder SV 53 | 4 |
| D-Junioren, Schönwalder SV | 4 |
| C-Junioren, Schönwalder SV (9er) | 3 |
| Ü40, Schönwalder SV | 3 |
| F-Junioren, Fußball-3/Jahrgang 2019 | 1 |
| E-Junioren, Fußball-5/Jahrgang 2016 | 1 |
| E-Junioren, Fußball-5/Jahrgang 2017 | 1 |
| Ü50, SpG Perwenitz/Schönwalde | 3 |
| Herren, Spielgemeinschaft Schönwalde-Perwenitz-Paaren | 3 |
| Gesamt September | 23 |

Davon sind zwölf Septembertermine lokal: neun Rasen, drei Kunstrasen.
Die einzige Abweichung zum letzten guten gesamten Rohbestand ist das C-Spiel
`031MFGE2PG000000VS5489BUVVKNMR0J`: bisher 12.09.2026 09:00 Uhr auf Kunstrasen
in Dallgow und ausgeschlossen; jetzt **08.10.2026 18:00 Uhr auf Rasen Platz 1
in Schönwalde**, mit getauschtem Heimrecht. Es ist daher eine zusätzliche lokale
Platzbelegung. Die Verarbeitung übernimmt die neue Quell-Spielstätte, nicht die
des veralteten Termins. Der lokal erzeugte Schema-2-Feed und der Belegungsservice
stimmen überein: sichtbare Spielzeit 18:00–19:25 (70 Minuten plus 15 Minuten Pause),
verbindliche Belegung **17:00–20:25 Uhr**. Gegen public/matches.json ist dies genau
eine sichere Ergänzung, keine Kürzung/Entfernung.
[Validierung des erzeugten Feeds bis zum Belegungsservice](import-feed-validation.json)

Die veröffentlichte Repositoryquelle war beim Vergleich ca. 1506,7 Minuten alt.
Der Hauptaudit bestätigte den Folgefehler des Runtime-Bundles bei überschrittenen
720 Minuten und eine Belegungs-API-Ablehnung um 05:54 UTC. Bei etwa 1502 Minuten
Quellenalter liegt auch `allow_stale_display=True` außerhalb des konfigurierten
Anzeige-Maximalalters von standardmäßig **1440 Minuten**. Das ist der beabsichtigte
24-Stunden-Hard-Cutoff, kein spezifischer Fehler eines kalten Caches. Innerhalb
des Anzeigekorridors werden aktuelle, gehashte Manifeste auch bei kaltem Cache
als veraltet angezeigt; konfliktkritische Prüfungen bleiben auf der strengeren
Grenze. Hier wurde keine Altersgrenze verlängert.
[Fehlgeschlagener Runtime-Bundle-Lauf](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34283743125)

## Tatsächlich umgesetzt und getestet

1. IMP-09: Saisonimport trennt Rohzeilenprüfung von globaler Identitäts-/
   Verlegungsauflösung. Einzelantworten bleiben auf fehlende IDs, Festivalgruppen
   und mögliche Pagination geprüft. Fehlendes Ziel, Zyklen, falsche Mannschafts-
   IDs/Spielnummer oder fehlende Ziel-Spielstätte stoppen weiterhin den Import.
   Die Festival-Zuordnung und Prüfsumme entstehen nach abschließender Auflösung.
2. IMP-01/02/03: geprüfte main-Härtungen in den abweichenden Entwicklungsbranch
   integriert. Einzelne Freigaben benötigen zwei identische Quellenbeobachtungen
   mit mindestens 60 Minuten Abstand; Bestätigungszustand wird vom Workflow auch
   bei blockierter Veröffentlichung gespeichert. Host-/Redirect-/Bytegrenzen,
   eindeutige Nichtstattfinde-Status und Festival-Quellidentität sind abgesichert.
3. IMP-04/05: Match-IDs, Duplikate, explizite Zeitzonen und konsistente Platzfelder
   werden geprüft. Fehlende, abgeschnittene oder falsch formatierte ICS sowie
   VEVENTs ohne Anfang/Ende werden als Fehler behandelt. Nicht unterstützte
   ICS-Serien werden abgelehnt. Eine gültig leere ICS bleibt erkennbar.
   Geänderte HTML-Zeilenklassen bei vorhandenen Spiel-/Festivallinks werden nicht
   als leere Saison gewertet.
4. Dauer-/Pufferprüfung im Belegungsservice verwendet verstrichene UTC-Minuten.
   Beide Sommerzeitwechsel und Überlappung über Mitternacht sind getestet.
   Mehrdeutige/nicht existierende lokale Uhrzeiten benötigen einen UTC-Offset.
5. IMP-08: Paketquellen erhalten echten Quellenzeitpunkt und Alter, sofern
   vorhanden. Ohne belastbaren Zeitstempel wird `fresh=false` statt eines
   erfundenen Frischenachweises gemeldet. Das ist Diagnose; die Nutzung als
   Freigabegrundlage muss weiterhin im jeweiligen Sicherheitsconsumer geprüft
   werden. Die Livekonfiguration nutzt die dynamische Quelle.
6. IMP-10: Die Bestätigung zählt den Zeitstempel des Quellenabrufs statt der
   Uhrzeit eines wiederholt gestarteten Reports. Beschädigte oder unplausible
   Zähler/Zeiten/Items starten die Bestätigung konservativ neu.

Aktuelle gezielte Prüfung: **211 Tests bestanden, zusätzlich 23 Subtests**, mit
pytest aus dem isolierten Audit-venv. Abgedeckt sind Import-/Festival-/Timing-
Regressionsfälle, die neuen Cross-Window- und Persistenztests, Belegungsquellen,
Runtime-Cache, API-Fehlerbehandlung, Trainingsabsagen, Sonderbelegung und Mähplan.
[JUnit-Testnachweis](import-test-results.xml). `git diff --check` ohne Diff-Fehler.
Der gesamte Repositorytest und Node-/App-Integration werden vom Hauptaudit separat
ausgeführt. Die Livequelle ist gelesen und mit korrigiertem Code verarbeitet;
der neue Import ist **nicht veröffentlicht oder in Azure installiert**.

## Zurückgestellte Risiken und nächste Abnahme

| Offener Punkt | Grund und konkreter nächster Schritt |
|---|---|
| IMP-06: Neue Belegungen bei gleichzeitig wartender Altintervall-Rücknahme | Die heutige Publikationsentscheidung ist atomar. Eine getrennte additive Übernahme benötigt über alle Consumer erhaltene Altintervall-Sperren samt stabilen IDs; bloßes Zusammenziehen alter/neuer Termine könnte wochenlange Fehlsperren erzeugen. Eigenständige konservative Quarantäne-Datenstruktur mit JSON-/ICS-/App-/Mähplan-Regressionen vorbereiten und vor Automatik-Erweiterung abnehmen. |
| IMP-07: Saison-/Trainingsquelle | Trainingsumschaltung, Sommer-/Winterregel und Ferienbehandlung brauchen eine bestätigte gemeinsame Grundlage. Konkreter nächster Schritt: dieselben Datensätze für App und Mähplan verwenden und Tag-für-Tag vergleichen; Abweichungen nicht automatisch als freie Fläche übernehmen. |
| Vollständigkeit aller Mannschaften/Spielgemeinschaften | Die neun Quell-IDs müssen gegen die autorisierte aktuelle Mannschaftsmeldeliste geprüft werden, einschließlich fehlender/neuer Teams und Festivals. Kein DFBnet-Login vorhanden. |
| Zeitkritische Belegung bei Quellenstörung | Bis zum korrigierten, nachgewiesen installierten Import müssen bestätigte neue Sperren über den berechtigten Betriebsprozess separat eingetragen werden. Keine Freigabe wegen leerer/alter Anzeige. Die neue C-Belegung vom 08.10. ist als konkretes Abnahmekriterium vorbereitet. |
| Quellenstand nach Release | Ein freigegebener Branchtest muss vollständige Saison, Qualitätsstatus, 46 lokale Termine zum hier beobachteten Stand und neue C-Belegung nachweisen; bei späteren fachlichen Änderungen sind aktuelle Mengenabweichungen zu erklären. Danach Runtime-Manifest, Source-SHA und API-Ereignis prüfen. Ein erfolgreicher Workflow allein beweist keinen Geräte-Rollout. |

Erwarteter Impact ist die Beseitigung eines **aktuell belegten Importabbruchs und
seiner Alters-/Bereitstellungsfolgefehler** bei unverändert vier HTTP-Abfragen.
Es wird keine zusätzliche produktive Mähminute aus dem Feedalter abgeleitet:
dafür fehlen in dieser Teilprüfung korrelierte Mäher-Betriebsdaten. Die geänderte
C-Belegung verlängert notwendige Schutzzeit am 08.10. um ein korrekt neu erkanntes
Intervall und darf nicht als Optimierungsverlust weggerechnet werden.
