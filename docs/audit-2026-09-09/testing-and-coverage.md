# Test-, Review- und Sichtnachweise

Abschluss der lokalen integrierten Prüfung am 09.09.2026: **694 Python-Tests und
406 Untertests bestanden**, **70 Node-/Appack-Tests bestanden**. Python 3.12.14,
isolierte Umgebung mit `requirements-test.txt` einschließlich `tzdata` und
pytest; der frühere fehlende Zeitzonen-Testbedarf wurde tatsächlich installiert.
Python-Kompilierung und `git diff --check` sind erfolgreich.

PR- und Quellpaketworkflow verwenden jetzt die vollständige pytest-Suite und
`requirements-test.txt`. Die bisherige unittest-Discovery erfasste die neuen
pytest-Funktionstests nicht. Nach der zusätzlichen reinen Workflowänderung
bestanden die Paketprüfungen erneut (2 Tests und 14 Untertests); diese Zahlen
sind in den obigen Gesamttests enthalten und werden nicht dazugezählt.

- [Python-JUnit](python-tests.xml)
- [Appack-JUnit](appack-tests.xml)
- [Import-Teilprüfung](import-test-results.xml)
- [Unabhängiger Importreview](import-review.md)
- [Unabhängiger Steuerungsreview](control-review.md)
- [Planungsreview](planning-review.md) und [Metrikreview](metrics-review.md)

Der Main-Hotfix wurde getrennt gegen aktuelle Main-Basis getestet: 119 Tests und
192 Untertests, gegenüber 112/192 zuvor. GitHub-CI für den exakten Kopf
`efe932cbaf5186f050847a29347566782e891c20` ist
[erfolgreich](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34318897870).
Der neue Audit-PR-/Buildnachweis wird in `delivery.json` festgehalten.

Die Tests verwenden lokale Fixtures, temporäre Stores, Fake-Sender und injizierte
Uhren. Standardtests sind keine Integration mit echten Gerätebefehlen. Bei den
Liveprüfungen wurden ausschließlich Status/Logs/Quellen gelesen. Es gab keine
Testmail, Ventilaktion, Start-/Parkaktion, Scheduleränderung oder Veröffentlichung.

## Szenarienmatrix

| Gefordertes Szenario | Nachweis und Ergebnis | Aussagegrenze |
|---|---|---|
| Mäher lädt, Wasser sinnvoll vorziehen | `test_coordination_simulation.py`: einfacher Vorschlag 03:05 statt 04:30, gleicher Bedarf | Ausschließlich Modell, Dock/Bedarf/Wege angenommen |
| Verschiebung und Dienstneustart | Persistentes SQLite-Simulationsjournal wieder öffnen, gleiche Bedarfs-ID/Reservierung | SQLite ist kein produktiver Azure-Dispatcher |
| Ursprungstermin nicht zusätzlich auslösen | Originaldisposition unterdrückt, zweite Ausführung abgewiesen; parallel und nach Neustart | Echte Hunter-Programmänderung nicht ausgeführt |
| Einzelner Statuspoll während Trocknung fehlt | State-/Full-/Readonly-Tests und Originalcode-Replay; physische Frist bleibt, Bestätigung beginnt neu | 148 Minuten weniger zusätzliche Sperre im konkreten Replay; keine gemessene Schnittzeit |
| Längere Lücke kann Wasser enthalten | 240-s-Fälle mit internem/externem Wasser; konservative neue 150-Minuten-Frist | Kurzer unbeobachteter manueller Lauf innerhalb von 180 s ist mit API allein nicht ausschließbar |
| Spiel kurzfristig verlegt/abgesagt/neu | Quellfenster-/Festival-/Change-Guard-Tests; neue Belegung vor Planstart neu validiert | Mannschaftsmeldeliste/DFBnet-Privatdaten fehlen |
| Import leer/unvollständig/falsches Format | Abgeschnittenes HTML, geänderte Zeilenklasse, fehlende IDs, defekte ICS/Zeitzonen/Reviewqualität abgewiesen | Gültig leer wird unterschieden; atomare Publikation wartender Rücknahmen bleibt begrenzt |
| Zwei Scheduler | ETag/CAS vor START, zwei Schreiber und persistentes Simulationsjournal; ein Sieger | Fremder nativer Sender mit eigener Zustandsquelle umgeht diesen Lock |
| Antwort auf angenommenen Befehl verloren | START-Pending bleibt über Neustart/Dock; kein Retry; Schutzparken separat reserviert | Hersteller hat keine bewiesene absolute Deadline/Queueleerungs-Transaktion |
| Station nicht rechtzeitig erreicht | Vorhandene Full-Tests plus frische Dockketten; Wasser bleibt gesperrt | Physische Heimfahrt und EPOS-Störung nicht künstlich behoben |
| Native Planung kollidiert | Simulationsvoraussetzung `own_park_confirmed`/Unterdrückung fehlt → keine Ausführung; Produktionsabweichung dokumentiert | Kein echter Geräte-Scheduletest; bleibt Pilotblocker |
| Manueller Stopp trotz Neuplanung | Full-Regression und Journalstopp; Start-/Wasserplanung bleiben gesperrt | Auflösung nur ausdrücklich und nach erneuter Sicherheitsprüfung |
| Mitternacht und beide DST-Wechsel | UTC-Intervallvereinigung, Berliner fold-Zeiten, nicht existente Uhrzeit, 23-/25-Stunden-Tag | Winter-/Ferien-Fachkalender fehlt unabhängig von Zeitzonentechnik |
| API-Ausfall und Wiederkehr | Fehler-/Freshness-/Storage-Fakes; kurze/lange Lücke getrennt; keine State-Mutation im Status | Ratebudget/`nextpoll` und reale Wiederanlaufsequenz noch praktisch zu prüfen |
| Verbindliche Sperre übersteuern | Negative Backendtests: special, fail_closed und verschachtelte Schutzmarkierungen | Manuelle Sportausnahme bleibt bewusst eng, kein globaler Override |
| Authentifizierung | Fehlend/gefälscht/abgelaufen/falsches Signing abgewiesen vor Storage; 5 Appack-Schreibstellen reichen Session weiter | Echte Appack-Sitzungsweitergabe/Trainer-Identityprovider offen |
| Metriken | Reise/Laden kein Mähen, Lücken zensiert, Befehlsversuche dedupliziert, Flächenherkunft getrennt | Kein Durchfluss-/Schnittqualitätssensor; geeignete Fenster nicht vollständig rekonstruierbar |

## Reproduzierbarer Vorher/Nachher-Replay

[Replay-Skript](../../scripts/replay_hydrawise_gap_audit.py) lädt den tatsächlichen
Basiscode aus Git-Objekten von `9c2d0fc…`; [Ergebnis](gap-replay.json) enthält
SHA256 der verglichenen Quellen. Gemeinsame Annahme: Bewässerungsende 07:00 UTC,
minütliche Beobachtung und 150 Minuten Pflichtfrist.

| Fehlerablauf | Basiscode | Auditcode | Interpretation |
|---|---|---|---|
| Ein Poll bei Minute 149 fehlt | Freigabe 12:00 | Freigabe 09:32 | 148 Minuten zusätzliche Sperre vermieden; 2 Minuten erneute Datenbestätigung bleiben |
| Externer Lauf, ein Poll bei Minute 20 fehlt | Freigabe 07:23 | Freigabe 09:30 | 127 Minuten notwendige Sperre wiederhergestellt; Sicherheitskorrektur |
| Interner Lauf, 240-s-Lücke | Freigabe 12:00 | Freigabe 12:00 | Kein Gewinn; unbeobachtetes Wasser weiter konservativ behandelt |
| Externer Lauf, 240-s-Lücke | Freigabe 07:25 | Freigabe 09:53 | Konservative Absicherung möglicher Bewässerung |

Die protokollierte Lücke vom 08.09. war ein **langer** Ausfall und gehört nicht
zum 148-Minuten-Erfolgsbeispiel. Dieser Unterschied verhindert eine falsche
Mähzeitbehauptung.

## App- und Zeitleistenprüfung

Die [synthetische Appack-Vorschau](appack-preview.html) wird direkt aus dem
geänderten Template mit [Generator](../../scripts/build_audit_preview.py)
erstellt; [Herkunft](appack-preview-provenance.json) enthält dessen Bytehash.
Fremdnetz ist durch CSP gesperrt, Fetch ist auf Fixtures begrenzt, POST ergibt
nur eine lokale Fehlermeldung. Es sind keine echten Zugangsdaten enthalten.

- [Desktop](appack-desktop.png): Haupt-/Nebensperre, Trocknung und Datenalter.
- [Mobil, 390 px](appack-mobile.png): keine horizontale Dokumentüberbreite,
  Textumbruch und scrollbare Karten; keine native Gerätezertifizierung.
- [Statistikansicht](appack-statistics.png): geschätzter Flächenumfang ist als
  Schätzung gekennzeichnet; Gerätetelemetrie und rechnerische Näherung bleiben
  unterscheidbar.
- [Verbindungsausfall](appack-offline.png): oberster Status unbekannt, letzte
  Werte gekennzeichnet, Bedienung gesperrt; Dialoge können sie nicht reaktivieren,
  weil auch der lokale Status `controlsAvailable=false` erhält.
- [Gemeinsame Zeitleiste](coordination-comparison.html) und
  [Browseraufnahme](coordination-timeline.png): alle drei Modellabläufe,
  überlappende Wasser-/Lade-/Sperrzeiten, identische Bilanzannahmen.

Das unabhängige Teilreview hatte keinen Browserzugriff. Der Hauptaudit konnte
anschließend den lokalen In-App-Browser nutzen und hat die Sichtprüfung
tatsächlich durchgeführt. Diese spätere Prüfung schließt die dort genannte
Browserlücke, nicht die offene Appack-CMS-/native Geräteabnahme.

## Noch nicht nachgewiesen

Kein vollständiger Produktionsrollout, kein neuer Parallelbetrieb, kein Livepilot
der Bündelung, keine exakt identifizierte installierte Azure-Codefassung,
keine Prüfung aller nativen Geräteschedules, keine Vor-Ort-Prüfung von Station,
Fahrwegen, Ventilen oder Rasenbefahrbarkeit. Kein statistisch bereinigter Gewinn
gegen eine identische reale Referenz. Keine vollständige autorisierte Team- oder
Ferienliste, keine zentrale Sommer-/Winter-Fachentscheidung, keine Wasserbilanz
in Litern. Diese Lücken stehen mit nächsten Schritten im
[Einführungsplan](rollout-and-operations.md) und [Risikoregister](improvements-and-risks.md).
