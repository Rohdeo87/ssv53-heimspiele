# Betreute Einführung am 9. September 2026

**Stand: 20:17 Uhr Europe/Berlin.** Die Nutzerfreigabe liegt vor. Zustandssicherung,
Softwareinstallation einschließlich PR 50, Sommerinitialisierung, aktuelle
Spielveröffentlichung und Appack-Veröffentlichung sind ausgeführt. Die
installierten Quellen wurden bytegenau geprüft. Echte Beobachtungszyklen und
Kalenderabfragen laufen im Vergleichsbetrieb. Die Geräteschreibrechte bleiben
geschlossen; die Abnahme des unbeaufsichtigten Vollbetriebs steht aus.

Dieser Bericht aktualisiert die [Startprüfung von 17:55 Uhr](live-introduction-check.md).
Die maschinenlesbaren Belege stehen in [live-introduction-evidence.json](live-introduction-evidence.json).
Private Zustandsdaten, Rollenkennungen, Sicherungen und Zugangsdaten werden
nicht im Repository veröffentlicht.

## Bestätigt und ausgeführt

| Gegenstand | Nachweis | Grenze der Aussage |
|---|---|---|
| Aufsicht und Platz | Nutzer persönlich vor Ort; Training bis 19:00; Station und Zufahrt laut Nutzer außerhalb der Beregnung | Keine eigene Ortsbesichtigung und keine vorzeitige Freigabe während Training |
| Tabellenzugriff | Ausschließlich `Storage Table Data Reader`, nur auf `MowerAutomationState`; 18:03 eingerichtet, 18:16 wieder entfernt; Abwesenheit der exakt erstellten Rolle nachgeprüft | Keine bestehenden Rollen verändert; Ablauf bereits ausgestellter Zugriffsberechtigungen nicht behauptet |
| Rückfallsicherung | 28 Seiten, 27.005 eindeutige Einträge, 30 Partitionen, davon 26.231 Bewässerungsjournale; alle Seitenhashes und Fortsetzungsmarken unabhängig geprüft | Kein atomarer Gesamtsnapshot über alle Partitionen |
| Kontrollzustand vor Installation | Revision 42031; Kontroll-Entity um 18:51 erneut mit identischem ETag und gleichen Rohfeldern wie in der vollständigen Sicherung gelesen | Für diesen letzten Abgleich kurz erneut erteilte Table-Leserolle um 18:51:38 wieder entfernt und Abwesenheit geprüft |
| Frischer Spielabruf | PR 46, Abruf 18:17:02–18:17:14; vier erfolgreiche öffentliche FUSSBALL.DE-Abfragen, 113 Datensätze, neun Teams, 46 lokale Belegungen: 41 Rasen, fünf Kunstrasen | Nicht authentifiziertes DFBnet; Vollständigkeit gegen eine separate verbindliche Vereinsmannschaftsliste weiterhin offen |
| Lokale Veröffentlichungsvorbereitung | Bestehende 45 Belegungen plus eine zusätzliche; keine bestehende Sperre entfernt, keine Verlegung oder Rücknahme im aktuellen Vergleich | Nur lokale Dateien; keine Veröffentlichung |
| Lokales Laufzeitbundle | 41 Rasenspiele und 16 Trainingsblöcke im 14-Tage-Zeitraum aus derselben freigegebenen Kalenderkopie verarbeitet | Bewusst `LOCAL-PREFLIGHT-UNPUBLISHED`; dieses Probeobjekt darf nicht veröffentlicht werden |
| Trainingsende | Tatsächlichen Kalenderplaner aufgerufen: nominal 17:30–19:00, mit Puffern `[17:00, 19:30)`; um 19:29:59 gesperrt, Kalenderfenster ab 19:30 | Nur Kalenderfreiheit. Geräte-, Wasser-, Daten- und Stoppsperren müssen zusätzlich aufgelöst sein |

Manifest-SHA-256 der vollständigen privaten Zustandssicherung:
`c4f4dd9b11d1497161b512aa1c4373d82291958660d1acfda8aa192e02d0abb7`.
Fehlgeschlagene frühere Teilsicherungen sind getrennt erhalten und zählen nicht
als vollständige Sicherung.

## Vor Ort bestätigte Station und begrenzter Installationsumfang

Die automatische Freigabeprüfung hat den Fernbefehl „Parken bis auf Weiteres“
vor dem Start des aufrufenden Prozesses zurückgewiesen. Über „blocked by policy“
hinaus wurde keine Begründung geliefert. **Es wurde kein Gerätebefehl gesendet
und kein anderer Ausführungsweg für diesen abgewiesenen Befehl versucht.**

Der Nutzer hat anschließend ausdrücklich bestätigt: „Der mäher ist im dock“.
Der Herstellerabruf um 18:44:37 zeigte ebenfalls `PARKED_IN_CS`, `RESTRICTED`,
100 Prozent Akku, verbunden, Fehlercode 0 und eine damals 163 Sekunden alte
Statusmeldung. Der native Kalender war leer; `nextStartTimestamp=0` und
`restrictedReason=WEEK_SCHEDULE`. Hydrawise lieferte um 18:45 alle sieben
erwarteten Zonen frisch und ohne laufende Bewässerung.

Das unabhängige Review bewertet diese Vor-Ort-Bestätigung zusammen mit dem
frischen Parkzustand und dem fehlenden bekannten nativen Start als ausreichend
für die **kurze Installation mit gesperrten Gerätenbefehlen**. Dafür war kein
erneuter Fernparkversuch erforderlich. `override.action=NOT_ACTIVE` ist
weiterhin kein Nachweis eines unbegrenzten nativen Parkauftrags. Eine vollständige
Herstellerwarteschlange und dauerhafte Parkgarantie wurden nicht bewiesen.
Diese begrenzte Installationsentscheidung ersetzt keine spätere Geräteabnahme.

Die alte Steuerung hatte zuletzt um 14:17 Uhr einen erfolgreichen Zyklus
gespeichert. Die neue Behandlung ihres nachgewiesenen Konfigurationsausfalls
ist jetzt installiert; die Wiederherstellung der vollständigen aktuellen
Datenkette wird gesondert geprüft. Aus der längeren Beobachtungslücke kann beim
Wiederanlauf eine zusätzliche konservative Bewässerungssperre folgen.
**19:30 Uhr ist keine zugesagte Startzeit.**

## Tatsächliche Installation und Kalenderinitialisierung

Um 18:52:44 wurden `CONTROL_MODE=OFF`, sämtliche Live-/Park-/Start-/Wassergates
auf `false`, beide zusätzlichen Betriebsbestätigungen auf `LOCKED` und
`AzureWebJobs.ssv53_mower_timer.Disabled=true` lesend zurückgeprüft. Die neuen
Ausführungsoptionen blieben ebenfalls ausgeschaltet. Andere bestehende
Einstellungen wurden bei diesem Schritt nicht verändert. Die Timerabschaltung
allein ist kein vollständiger Befehlsschutz; die zusätzlichen Codegates gelten
auch bei einer expliziten administrativen Auslösung.
[Azure-Dokumentation zur Funktionsabschaltung](https://learn.microsoft.com/en-us/azure/azure-functions/disable-function)

Das bei der ersten Installation eingespielte CI-Quellpaket hat SHA-256
`12c7484f8618f1d68efa03ee6e509f4438fd4141af0168bf5a0e71370e54fc71`.
Deployment-ID dieser ersten Installation: `c702baf7-e385-410d-9d84-68827919a508`.
Azure bestätigt den erfolgreichen Remote-Build mit Abschluss um 18:55:14;
der CLI-Aufruf endete um 18:56:19 erfolgreich.

Alle **61 tatsächlichen Quelldateien** wurden über den authentifizierten
Host-Dateizugang bytegenau gegen das Paket verglichen. Ein einzelner
Verbindungsfehler beim Lesen von `special_occupancy.py` wurde durch erneutes
lesendes Abrufen aufgelöst; das Manifest blieb über die Prüfung unverändert.
Manifest-SHA-256:
`fbab7b7078c769f141f4532888e8336029d69d8767c7d527f613cd5e5772589e`.
Es sind 15 Funktionen registriert. Der authentifizierte Host-GET meldete um
19:03 `Running`; die Initialisierungsroute lieferte ohne Schlüssel HTTP 401
und bei noch geschlossenem Einrichtungsgate mit Administratorschlüssel HTTP
403 mit `TRAINING_CONTROL_INITIALIZATION_DISABLED`. Sämtliche Gerätesperren
waren nach dem Deployment unverändert geschlossen.

Der bestätigte Sommerplan wurde um 19:11 einmalig über den geschützten
Verwaltungszugang gespeichert: Zustandsrevision 42031 → 42032,
Trainingsrevision 1, nachgewiesener Anfangstag 09.09.2026. Das Einrichtungsgate
wurde um 19:11:23 wieder geschlossen und alle Gerätesperren nochmals geprüft.
Kein Gerätebefehl war Bestandteil dieses Vorgangs. Der gemeinsame Kalender
benötigt für Nachttermine den nachgewiesenen Vortag; seine Aktivierung bleibt
frühestens ab 10.09. um 00:00 Uhr vorgesehen.

## Geschlossene Lücke zwischen Import- und Steuerungsbranch

Der aktive Spielimport läuft auf `main`, der Azure-Verbraucher auf
`feature/azure-mower-migration`. Die additive Veröffentlichung aus PR 47 wäre
durch den Merge nur im Steuerungsbranch angekommen. [PR 48](https://github.com/Rohdeo87/ssv53-heimspiele/pull/48)
übernimmt sie deshalb auf die tatsächliche Importlinie. Neue Spiele werden
übernommen, ungeklärte verschwundene oder geänderte Sperren bleiben in JSON und
ICS erhalten. Wiederholte Verarbeitung derselben Quelldatei zählt nicht als
zweite unabhängige Bestätigung; beschädigte Bestätigungsstände geben nichts frei.

Vor den Merges wurden jeweils der tatsächliche Kopf, Basiscommit, erfolgreiche
Checks und Mergebarkeit neu gelesen; die Ergebnisse wurden anschließend geprüft:

| PR | Geprüfter Kopf | Tatsächlicher Merge |
|---|---|---|
| [46](https://github.com/Rohdeo87/ssv53-heimspiele/pull/46) | `efe932cbaf5186f050847a29347566782e891c20` | `a304f004a98fff91d5c8915598db43f6d831d316` |
| [47](https://github.com/Rohdeo87/ssv53-heimspiele/pull/47) | `accd7851f9927cd09d0076a203795a303b06517d` | `bd7238124fb942c35b628b4dafab094556cb3e1b` |
| [48](https://github.com/Rohdeo87/ssv53-heimspiele/pull/48) | `44e484a09484fe9f41e266d3143892435529879f` | `c4218fcbdda0fcd4b3ec5b4160b0bf93e7aa2c66` |
| [50](https://github.com/Rohdeo87/ssv53-heimspiele/pull/50) | `4e4d60125108097743bfaac6b18c8405eb48013b` | `03c95f75dfca8921532849f3721e4b237823da90` |

PR 48 wurde nach PR 46 auf den neuen Main-Stand umgestellt; sein inzwischen
überflüssiger Vorabcommit wurde entfernt. Die [Prüfung am finalen Kopf](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34381811876)
war erfolgreich; 182 Tests und 199 Untertests bestanden. Die sechs
produktionsrelevanten Importdateien entsprechen dem geprüften Steuerungsbranch.

Der automatische Lauf nach PR 48 übersprang wegen hinreichend frischer Daten
den Abruf. Deshalb wurde zusätzlich genau ein normaler vollständiger
[Importlauf](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34382702542)
ausgeführt. Er rief die Quelle tatsächlich ab und veröffentlichte am 09.09.
um 19:24:56 Uhr 46 lokale Spiele. Gegenüber dem unmittelbar vorherigen Stand:
keine Zu-/Abgänge, Änderungen oder ungeklärten Rücknahmen. Datencommit:
`112f35a190d3aeb9a3bdae5f98d0c8ae3d56d398`.

## Datenpaket veröffentlicht und tatsächlich verbraucht

Nach [gesonderter Validierung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34383165799)
veröffentlichte [Lauf 34383735588](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34383735588)
das frisch geprüfte Paket und las seine drei Nutzdateien zurück. Version:
`20260909T173454Z-bd7238124fb9-34383735588`.
Der genehmigte Kalender ist in Konfiguration und strukturierten Spieldaten
eingebettet; es braucht keine zusätzliche separate Kalenderdatei.

Der lokale Benutzer darf die Blobdaten nicht direkt lesen (HTTP 403); auch
der Host-Dateizugang verweigert `/tmp`. Dafür wurden keine zusätzlichen Rechte
erteilt. Der tatsächliche Verbrauch ist über einen anderen lesenden Nachweis
belegt: Die installierte Laufzeit bildet den Snapshotnamen aus SHA-256 der
Konfigurationsbytes, einem Nullbyte und den ICS-Bytes. Vier echte Timerzyklen
melden exakt den aus dem CI-Artefakt nachgerechneten Hash
`d85acc052089da005362460fe9dce04640a504618bd9b279230c0c0787ed5526`,
die neue Veröffentlichungszeit, 41 Rasenspiele und keinen Rückfallstand.
Ein eigener direkter Download der Produktionsblobs durch den lokalen Benutzer
wird nicht behauptet.

Die öffentliche Azure-Belegung antwortete für 09.–10.09. und 08.10. mit HTTP
200, frischer Quelle von 19:24:56 und ohne Fallback. Das Nachholspiel am 08.10.
wird mit nominal 18:00–19:25 und Sperre 17:00–20:25 ausgegeben. Vorhandene
manuelle Buchungen bleiben erhalten. Hashes und Grenzen:
[runtime-publication-evidence.json](runtime-publication-evidence.json).

## Echte Beobachtungen und zusätzlicher Kalenderfehler

Ab 19:46 lief DRY_RUN mit Herstellerabfragen. PARK, START, Bewässerung und
Koordinationsausführung blieben ausgeschaltet; beide Schreibbestätigungen
standen auf LOCKED. Die erste Folge von vier erfolgreichen Zyklen meldete
Station, 100 Prozent Akku, Fehlercode 0 und `FORCE_PARK`. Alle sieben
Wasserzonen waren frisch und aus. Es wurde kein Gerätebefehl gesendet.

DRY_RUN speichert seine Beobachtung mit Versionsprüfung in AutomationState
und im Bewässerungsjournal; es ist kein vollständig schreibfreier Datenbankbetrieb.
Der vorherige Kontrollzustand bleibt gesichert. Die mehrstündige Datenlücke
bis zur ersten Beobachtung erzeugte eine vorsorgliche Sperre bis 22:16:04 Uhr.
Die folgenden Minutenabfragen verschoben diese Frist nicht. Dieser Stand vor
der erneuten Installation ist historisch; spätere Lücken können eine neue
konservative Bewertung erfordern. Die Parksperre bleibt wirksam.
[Erste Zyklusbelege](live-readonly-first-cycles.json).

Die konkrete Probe des genehmigten Kalenders zeigte eine Einführungslücke:
SHADOW ohne Winterzustand meldete 333 Tage ohne verbindliche Saisonzuordnung;
mit eingeschaltetem Winter-Control wurde SHADOW pauschal abgelehnt. Damit
war gerade der neue manuelle Kalender nicht im Vergleichsbetrieb prüfbar.
[PR 50](https://github.com/Rohdeo87/ssv53-heimspiele/pull/50) trennt deshalb
lesenden Zugriff und Änderung:

- SHADOW liest den bestätigten persistenten Sommer-/Winterzustand für Kandidaten.
- Nur ACTIVE liefert verbindliche neue Belegungen und erlaubt Schalteränderungen.
- Fehlender Vortag, fehlender Zustand und ungültige Übergänge liefern keinen Kandidaten.
- Der Dashboard-Schalter bleibt in SHADOW gesperrt.

Am finalen Kopf bestanden **1.159 Tests und 439 Untertests**; 79 Trainingstests
wurden zusätzlich als gezielte Teilmenge ausgeführt. Zwei unabhängige Reviews prüften Geräteschreibgrenzen
und App-Vertrag; eine Regression sichert die gesperrte öffentliche Schalterausgabe.
[CI-Paketlauf](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34385868992),
[Codeprüfung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34385868824).
Für die Nachinstallation wurden OFF und Timerabschaltung wiederhergestellt;
die Geräteschreibrechte blieben durchgehend geschlossen.

## Installierte Kalenderkorrektur und aktueller Vergleich

Das finale Paket aus PR 50 wurde zunächst gegen alle 61 kanonischen Gitdateien
geprüft und bei geschlossenen Gerätegates mit Remote-Build installiert.
Quellcommit: `4e4d60125108097743bfaac6b18c8405eb48013b`; Merge:
`03c95f75dfca8921532849f3721e4b237823da90`. Paket-SHA-256:
`570858f6da7f85ddbb4182fd4539f534ca5225d2c47dc1a03a1ebdf66535350f`.

Aktive Deployment-ID zum Prüfzeitpunkt: `6631c7aa-66af-4c80-8005-50a1fc7b6d51`.
Azure meldete den Remote-Build um 20:01:17 erfolgreich; der CLI-Aufruf endete
um 20:02:21 ohne Fehler. Um 20:09:51 wurden alle **61 tatsächlich installierten
Quelldateien** über den Host-Zugang unabhängig bytegenau gegen das CI-Paket
verglichen. Hoststatus: Running; Manifest unverändert. Manifest-SHA-256:
`7c93901b640250814fe29a5217526b5b98a834dfbc02b2dc41c0594e77da077b`.
Die während des Remote-Builds installierten Bibliotheksbinärdateien wurden
nicht byteweise verifiziert.
[Quellnachweis](live-installed-shadow-source-evidence.json),
[Azure-Deployment](live-shadow-deployment-evidence.json),
[Testprotokoll](manual-training-shadow-tests.txt).

Die um 20:11:37 zurückgelesenen Einstellungen aktivieren DRY_RUN, echte
Statusabfragen, den Timer und Kalender-SHADOW. Winter-Control darf dabei den
bestehenden Sommerzustand lesen; der App-Schalter und die Schreibroute bleiben
gesperrt. PARK, START, Bewässerung und Koordinationsausführung bleiben false,
beide Gerätebestätigungen LOCKED und die einmalige Einrichtung geschlossen.
Andere Einstellungen blieben unverändert.
[Einstellungsnachweis](live-shadow-settings-evidence.json).

Die öffentliche Belegungs-API wurde um 20:17 für drei Bereiche geprüft:

| Abfragebereich | Ergebnis des neuen Kalenders | Verbindliche Wirkung |
|---|---|---|
| 09.–10.09. | Kein Kandidat: der für den 09.09. benötigte 08.09. liegt vor dem bestätigten Historienbeginn | SHADOW, bisherige Belegung bleibt erhalten |
| 10.09. | Kalenderrevision 2, bestätigter Sommerzustand, Kandidat vorhanden, keine Blocker | Weiter SHADOW, noch keine Umschaltung |
| 15.09. | Derselbe bestätigte Kalender, Kandidat vorhanden, keine Blocker | Weiter SHADOW |

Alle Antworten waren HTTP 200, die Spielquelle frisch, ohne Fallback;
Absagen und Sonderbelegungen verfügbar. Inhaltshash und Umschaltzustand stimmen
mit der veröffentlichten Freigabekopie überein.
[Abfragenachweis](live-shadow-occupancy-evidence.json).
Das Bestehen für einen zukünftigen Zeitraum ist kein Nachweis, dass ACTIVE
heute zulässig wäre. Es ist keine automatische Umschaltung vorgemerkt.

Fünf weitere echte Timerzyklen von **20:13:02 bis 20:17:02** melden die neue
Quellmanifest-Prüfung, dasselbe veröffentlichte Datenpaket, Station, 100 Prozent,
FORCE_PARK und sieben ausgeschaltete Bewässerungszonen. Alle fünf melden
`command_sent=false`. Aktuelle Telemetrie wurde nach zwei Minuten bestätigt.

Die installationsbedingte Beobachtungslücke überschritt 180 Sekunden. Da eine
Bewässerung in dieser Lücke nicht ausgeschlossen werden kann, begann die
konservative Sperre bei Wiederaufnahme um 20:11:51 erneut und reicht bis
**22:41:51 Uhr** (in einer Minutenanzeige aufgerundet: **22:42 Uhr**).
Alle fünf Folgezyklen erhalten exakt diese Frist; normale Statusabfragen
verlängern sie nicht. Das belegt Wiederanlauf und stabile Folgetakte, keinen
gezielt injizierten Einzelpollausfall. Die Parksperre gilt zusätzlich; aus
22:42 folgt keine Startzusage.
[Zyklusnachweis](live-shadow-cycles-evidence.json).

Der native Hydrawise-Sollplan meldet für den 10.09. weiterhin **04:30–07:10 Uhr**:
fünf Zonen mit je 20 Minuten, zwei Zonen mit je 30 Minuten, zusammen 160 Minuten.
Das ist ein gemeldeter Plan; tatsächliche Endzeit und Wassermenge sind noch
nicht beobachtet. Der Koordinator hat bewusst keinen aktivierten gebundenen
Bedarf (`APPROVED_COORDINATION_CONFIG_DISABLED`). Die installierte Geräteanbindung
ist vorhanden; Bedarf, Unterdrückung des Originaltermins und physische Wirkung
sind vor ihrer Ausführung noch nachzuweisen.

## App-Veröffentlichung und verbleibende Abnahme

`Platzpflege.tpl` wurde am 09.09. um 19:53 im bestehenden Appack-Modul gespeichert.
Nach Seitenreload und erneutem Öffnen des Quelltexteditors stimmt der Inhalt
mit der geprüften Fassung überein, abgesehen von den durch den Editor
vereinheitlichten CRLF-Zeilenumbrüchen. Normalisierte SHA-256:
`d0573fa45b944dc70524d43c43d64b691b9b7ff8e523890984300ee05c2d13a5`.
Die vorherige Vorlage ist privat gesichert. Alle 108 Appack-Tests bestanden
unmittelbar vor Veröffentlichung.

[Quelltextnachweis](appack-publication-evidence.json),
[tatsächliche CMS-Ansicht](../ui-2026-09-09/final-preflight/appack-published-cms.png),
[geprüfte mobile Winteransicht mit Beispieldaten](../ui-2026-09-09/final-preflight/winter-switch-390.png).
Die CMS-Vorschau fordert für diesen Browser eine eigene Geräteaktivierung.
Die angemeldete native Appansicht wurde deshalb nicht als beobachtet ausgegeben;
der Nutzer wurde um Prüfung der drei Bereiche gebeten. Es wurden keine
Aktivierungscodes oder PINs erzeugt oder geändert.

Für die Fortsetzung gilt der [Einführungsablauf](final-activation-runbook.md).
Historienbeginn ist 09.09.; Kalender ACTIVE frühestens am 10.09. um 00:00 Berlin.
Der nächste native Wasserlauf 04:30–07:10 bleibt unverändert. Zum Testen wird
kein zusätzlicher Bedarf angelegt oder Wasserlauf ausgelöst. Die
Koordinationsausführung bleibt aus.

Die beiden kurzen Beobachtungsfolgen ersetzen noch keinen vollständigen Vergleich:
Laden, notwendige Bewässerung einschließlich Ende/Trocknung, Sporttermin,
Tageswechsel und API-Wiederherstellung müssen im realen Verlauf ausgewertet
werden. Erst danach kann der begrenzte Gerätepilot innerhalb der erteilten
betreuten Freigabe konkret angesetzt werden. Für einen späteren Termin muss
die Aufsicht tatsächlich verfügbar sein. Unbeaufsichtigter Vollbetrieb,
zusätzliche produktive Mähminuten und eine erfolgreiche vorgezogene
Bewässerung sind noch nicht live nachgewiesen.

Die abschließende unabhängige Dokumentprüfung mit dem kleineren Modell Luna
hat Installationsstatus, getrennte Beobachtungsfolgen, datumsabhängigen
Kalendernachweis und den korrigierten Simulationsgewinn erneut abgeglichen.
Die vier gefundenen Darstellungsabweichungen sind behoben; die offenen
Geräteabnahmen wurden dadurch nicht als erledigt bewertet.
