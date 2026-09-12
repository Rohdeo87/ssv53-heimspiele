# Bestätigte Parksperre für die Bewässerung

12.09.2026; Uhrzeiten Europe/Berlin. Ergänzung zur [Ursachenanalyse](README.md).

## Freigegebene Betriebsregel

Der Nutzer hat bestätigt: Während der Bewässerung wird diese zuerst beendet.
Erst nach bestätigtem Ende darf der Mäher am Gerät oder über Husqvarna gestartet
werden. Die eigene API kann die Hersteller-App oder Gerätetasten nicht sperren.
Diese Regel ist Voraussetzung für die neue Option und kein physischer Interlock.

## Umsetzung

`IRRIGATION_CONFIRMED_PARK_HOLD_ENABLED` ist im Code standardmäßig aus.
Aktiviert darf die Bewässerung einen fortbestehenden, selbst veranlassten
`ParkUntilFurtherNotice` mit `HOME` weiterverwenden. Ein bloßer alter Status
„geparkt“ reicht nicht. Die erste Bestätigung benötigt weiterhin frische
Geräteereignisse, mindestens zwei getrennte Beobachtungen und eine Minute.
Die Bestätigung wird bereits vor dem geplanten Bewässerungsstart aufgebaut.

Der gespeicherte Nachweis bindet Geräte-ID, eigenen Parkauftrag, letzten Start,
Parkquelle, Rückstartrecht, manuelle Sitzung und Bedienauftrag samt Status und
Generation. Jeder erfolgreiche direkte Abruf muss weiterhin HOME, Station/Laden,
Verbindung und Fehlerfreiheit zeigen. Zwischen Abrufen sind höchstens 90 Sekunden
zulässig. Ein unveränderter alter Gerätezeitstempel entwertet diesen Nachweis
allein nicht mehr. Fremdstart, Moduswechsel, unbekannter Zustand, eigene Start-
oder Parkaktion und unterbrochene Abrufkette entwerten ihn.

Nach Entwertung verhindert eine gespeicherte Zeitmarke, dass derselbe alte
Zustand unmittelbar wieder freigibt. Auch ungültige persistente Daten werden
so verworfen. Eingabefehler und Transport-Ausnahmen widerrufen den Nachweis
vor dem separaten Schutzpfad per Revisionsvergleich; bei Schreibkonflikten gibt
es höchstens einen Wiederholungsversuch. Bei nicht verfügbarem Speicher wird in
diesem Durchlauf nicht bewässert. Die letzte gespeicherte Bestätigung erhält
keine Zeitverlängerung und verfällt nach 90 Sekunden.

Ein kurzer Prozesswechsel kann einen identischen gespeicherten Nachweis
innerhalb dieser Frist erhalten. Ein längerer Neustart verlangt eine neue
Bestätigung. Direkt vor dem Zonenversand werden Alter, Kontrollbindung und exakt
der reservierte Nachweis erneut geprüft, auch im persistenten Befehlsjournal.

Frische Wasserdaten, tatsächliche Zonenenden, bestehende Belegungssperren,
Idempotenz, die automatische Grenze 03:30–08:00 und 150 Minuten Trockenzeit
bleiben erforderlich. Mäherstarts erhalten keine Ausnahme von ihren bisherigen
Prüfungen. Aktive manuelle Starts erwerben keinen dauerhaften Parknachweis;
die bestehenden ausdrücklichen Konfliktentscheidungen und frischen
Stationsprüfungen gelten für diesen Pfad unverändert.

## Vergleich unter identischen Annahmen

[Reproduzierbare Ergebnisse](park-hold-full-run-simulation.json), echter Controller
mit ausschließlich simulierten Geräten: sieben Zonen, 160 Minuten Wasser,
Minutentakt, alle 15 Minuten ein Mäherereignis, freie Belegung, verlässliche
Wasserdaten, bereits suspendierter Zeitplan. Kein zusätzlicher Wasserbedarf.
Die erste Bestätigung der neuen Option wird in der Simulation erst ab 04:30
aufgebaut; im regulären Ablauf kann sie vorher entstehen.

| Variante | Zonenstartzeiten | Letztes Ende | Erfüllter Bedarf |
| --- | --- | --- | --- |
| Zwei Planreparaturen, bisherige Mäherfrische | 04:32, 05:00, 05:30, 06:00, 06:30 | 06:50 | 5/7 Zonen, 100/160 Min.; Rest passt später nicht mehr bis 08:00 |
| Zusätzlich fortbestehende Parksperre | 04:33, 04:56, 05:19, 05:42, 06:05, 06:28, 07:01 | 07:31 | 7/7 Zonen, 160/160 Min. |
| Gleiche Option, um 7 Min. versetzte Geräteereignisse | 04:41, 05:04, 05:27, 05:50, 06:13, 06:36, 07:09 | 07:39 | 7/7 Zonen, 160/160 Min. |
| Gleiche Option, um 14 Min. versetzte Geräteereignisse | 04:34, 04:57, 05:20, 05:43, 06:06, 06:29, 07:02 | 07:32 | 7/7 Zonen, 160/160 Min. |

Die Zwischenzeiten enthalten weiterhin die notwendige Endbestätigung. Es werden
keine Zonen überlagert oder verkürzt. Der Mähzeitgewinn ist damit noch nicht
live gemessen: gegenüber einer unvollständigen Bewässerung darf keine positive
Mähzeitbilanz auf Kosten der Rasenversorgung behauptet werden. Nach tatsächlichem
Ende gilt weiterhin die Trockenzeit; ohne weitere Sperren wäre im ersten neuen
Szenario eine frühestmögliche Freigabe ab 10:01 Uhr denkbar, kein garantierter Start.
Ausfälle, Belegung und Laden können diese Zeit verschieben.

## Test und unabhängiges Review

- [Gesamte Python-Suite](park-hold-python-tests.txt): 1.526 Tests und 482 Untertests
  bestanden. Netzverkehr in Tests gesperrt. Zwei vorbereitende Testläufe hatten
  ausschließlich Probleme mit dem temporären Verzeichnis; der erfolgreiche Lauf
  verwendet ein eigenes, zuvor nicht vorhandenes Verzeichnis im Workspace.
- 50 gezielte Tests: vier Stunden identischer Dockzustand, Wiederanlauf, Ausfall
  im echten Eingabefehlerpfad, Offline/Fehler/Fremdstart, Kontrolländerung,
  korrupte Daten, Ereignisrücksprung, doppelte Beobachtung, verzögerter Versand
  und Bedienänderung zwischen Reservierung und Sendeprüfung.
- Vollsequenz mit manuellen Sitzungen an/aus und drei Ereignisphasen;
  kein doppelter Zonenstart und keine Laufzeitverkürzung.
- Unabhängiges begrenztes Luna-Review: fehlender Widerruf im echten Fehlerpfad
  und unvollständige Kontrollbindung gefunden, beide korrigiert und abgesichert.

## Risiken und Abnahme

| Risiko | Erkennung / Gegenmaßnahme | Verbleibende Grenze |
| --- | --- | --- |
| Manueller Fremdstart unmittelbar nach Abruf | Verbindliche Betriebsregel; jeder gemeldete Modus-/Aktivitätswechsel verwirft den Nachweis; bestehender Konfliktschutz bleibt | Herstellersteuerung kann nicht atomar gegen Ventilstart verriegelt werden |
| Verbindungs- oder Speicherausfall | Widerruf mit Zeitmarke, Revisionsprüfung, 90-Sekunden-Ablauf; keine neue Zone bei fehlendem Nachweis | Aktive Ventile laufen gegebenenfalls ihre bereits gesendete Dauer ab; zentrale Software ist kein Hardware-Notaus |
| Veraltete/überholte Startreservierung | Unveränderte Identität und aktuelle Freigabe direkt am Versand; persistentes Befehlsjournal | Angenommener Befehl ist noch kein physisch bestätigter Zustand |
| Bewässerung kann nicht mehr bis 08:00 fertig werden | Gesamte Restfolge vor jedem Start prüfen, frühzeitige Diagnose; nicht kürzen | Betreute Ersatzbewässerung kann erforderlich sein |
| Rückfall auf alten Code | Option aus, aktive/ungewisse Aktionen und native Zeitpläne zuerst prüfen; gesichertes Altartefakt | Altcode enthält die belegten Fehler; keine unbeaufsichtigte Ersatzgarantie |

Einführung: Paket zuerst mit ausgeschalteter Option installieren; alle 71 Dateien,
16 Funktionen und bestehende Flags nachweisen. Dann ausschließlich das neue Flag
gemäß erteilter Freigabe aktivieren. Frische Protokolle müssen Aufbau, Fortbestand
bei altem Ereignis und unveränderte Sperren belegen. Keine Geräte-Testbefehle und
keine automatische Nachholung des heute abgebrochenen Laufs.

Physische Abnahme bleibt offen, bis mindestens zwei vollständige überwachte
Siebenzonenläufe und ein Wiederanlauf beobachtet sind: alle Sollzeiten erfüllt,
Enden bestätigt, Abschluss spätestens 08:00, keine Überlappung mit Mähen.
Abbruch bei widersprüchlichen Daten, unbekanntem Befehlsausgang, Fremdstart oder
zu knappem Restfenster. Betriebsdaten und tatsächliche Ventil-/Mäherbeobachtung
sind zusammen erforderlich.

## Veröffentlichungsnachweis

Codecommit: `c0019d1323c6cf85cc49a8896b117e2bcc6eb26c`.
[GitHub-Prüfung dieses exakten Quellstands erfolgreich](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34678485807).
[Review-PR 79](https://github.com/Rohdeo87/ssv53-heimspiele/pull/79).
[Paketnachweis](park-hold-package-proof.json): 71 Einträge, 16 Funktionen,
Offline-Import und alle Quelldateien geprüft. Gegenüber der Installation ändern
sich nur drei vorhandene Module, das neue Parkmodul und das Manifest.

Paket `dist/irrigation-park-hold-release.zip`, SHA256
`d374016b808a6171f7fb81f98f5957eef146c96ba8a586001e7c750de3d9ae10`.
Manifest SHA256
`6cab4b6a096571a3ad2402e31d54ecf9e1758e6b287d0aad2243bf9e55232002`.

Die [erneute Installationsprüfung](park-hold-installed-before.json) bestätigt
alle 70 Dateien des Altpakets und unveränderte Schutzschalter. Die
[unmittelbare Vorprüfung](park-hold-deployment-preflight.json) zeigt fünf Zyklen
mit HOME/geparkt, frischen inaktiven Wasserzonen, COMPLETE_HOLD und ohne offenen
Mäherstart. Der heutige Lauf war zuvor durch Bedienung beendet worden.

**Noch nicht installiert/aktiviert:** Die automatische Freigabeprüfung lehnte
den produktiven `config-zip`-Aufruf vor dessen Ausführung ab. Begründung: keine
für diese konkrete Bereitstellung ausreichend eindeutige Produktionsfreigabe.
Die bisherigen Nutzerzusagen und die bestätigte Betriebsregel wurden von dieser
Prüfung nicht als konkrete Rolloutfreigabe anerkannt. Es wurde anschließend
gezielt nach Installation dieses Pakets und Aktivierung dieses Flags gefragt.
Kein Ersatzweg, keine Geräte-Testbefehle und keine Einstellungsänderung.

Die Vorprüfung zeigt außerdem weiter `MOWER_START_SEND_BLOCKED` mit
`MOWER_STATUS_STALE`. Dieses Paket lockert die Mäherstartprüfung ausdrücklich
nicht; ein erfolgreicher Mäherstart ist durch diese Untersuchung nicht belegt.
Tests und Simulation allein belegen keinen erfolgreichen Livebetrieb.
