# Bewässerung startet trotz geparktem Mäher nicht

Untersuchung vom 12.09.2026. Uhrzeiten im Text: Europe/Berlin. Kein Gerätebefehl
wurde für diese Untersuchung gesendet. Die beschriebenen Reparaturen sind
zunächst Entwicklungsstand; Veröffentlichung und physischer Erfolg müssen
gesondert nachgewiesen werden.

**Fortsetzung nach Nutzerbestätigung:** Die dauerhafte eigene HOME-Parksperre
ist inzwischen implementiert und getestet, standardmäßig ausgeschaltet.
[Regel, Umsetzung, vollständiger Vergleich und Einführungsnachweis](PARK_HOLD.md).
Die Paketangaben weiter unten dokumentieren den vorherigen Entwicklungsstand.

## Belegter Ablauf

Die [Installation](installed-before.json) wurde um 07:26 Uhr gegen das vorherige
Release-Paket geprüft: alle 70 Dateien stimmen, Host läuft, 16 Funktionen.
Installiertes Manifest:
`314afa1f974f3bb9bc682ed8c3ff757dad596fe37f95806e5d7317e98dbeb0fe`.
Der Fehler wurde also gegen tatsächlich installierten Code untersucht.

[259 Steuerungszyklen wurden gelesen; 15 ausgewählte Beobachtungen](incident-evidence.json)
belegen die folgenden Schritte. Auszug, ursprünglicher Abfragezeitraum und
Prüfsumme des vollständigen lokalen Abrufs sind im Nachweis enthalten.

| Zeit | Nachweis | Bedeutung |
| --- | --- | --- |
| 03:45 | `PARK_COMMAND_SENT` | Zentraler Parkbefehl gesendet |
| 03:46 | Gespeicherte Stationsbestätigung und `PARKED_IN_CS` | Parkposition war bereits bestätigt; das deckt sich mit der Beobachtung des Nutzers |
| 03:46–04:11 | Zonen werden einzeln suspendiert; sechs `IRRIGATION_PLAN_UPDATED` | Eigene Eingriffe verändern die zurückgemeldete native Warteschlange |
| 04:12 | `IRRIGATION_OPERATING_WINDOW`, Detail `INVALID / persisted zones overlap` | Gespeicherte Zonenzeiten überschneiden sich; die allgemeine Meldung zur Uhrzeit verdeckt die Ursache |
| 06:47 / 06:48 | `REVALIDATING` / `REVALIDATED` | Zwei getrennte Wasserrückmeldungen bestätigen weiterhin gesperrte Zeitpläne |
| 06:49 / 06:50 | Wieder `REVALIDATING`, dann `WAIT_FOR_CONFIRMED_PARK` | Erneuerte Bestätigung wird verworfen; danach ist die Mähermeldung älter als 180 Sekunden |
| 07:33 | `IRRIGATION_OPERATOR_STOPPED_BETWEEN_ZONES`, `COMPLETE_HOLD` | Eine Bedienaktion beendet den wartenden Lauf. Diese Untersuchung hat sie nicht ausgelöst |
| 07:49 | `COMPLETE_HOLD`, Wasserdaten frisch, aktive Zonen: 0 | Der Lauf wartet inzwischen nicht mehr auf seinen Start |

Es ist kein zentral gesendeter Zonenstart in diesem Beobachtungsfenster
nachgewiesen. Das ist kein unabhängiger Messnachweis für jeden Moment am Ventil:
07:28 und 07:29 waren die Hydrawise-Daten nicht hinreichend bestätigt.
Der direkte lesende Zugriff auf die Zustands-Tabelle mit der angemeldeten
Azure-Identität scheiterte mit HTTP 403 `AuthorizationPermissionMismatch`.
Es wurden keine Rechte geändert oder alternative Speicherzugangsdaten benutzt.
Die gespeicherte Überschneidung ist durch das Validator-Ergebnis im
Steuerungsprotokoll belegt; der komplette Tabelleninhalt wurde nicht exportiert.

## Ursachen und Reparaturen

### 1. Eigene Suspendierung verschiebt die native Warteschlange

Ursprünglich: Zone 1 um 04:30, Zone 2 um 04:50, Zone 3 um 05:10 usw.
Nach Suspendierung der ersten Zone meldet Hydrawise für Zone 2 bereits 04:30.
Die bisherige Zusammenführung behält für die suspendierte Zone 1 weiterhin
04:30 und übernimmt für Zone 2 ebenfalls 04:30. Dadurch entsteht die Kollision.

Die Reparatur erkennt ausschließlich die vollständig erklärbare Verschiebung
einer ursprünglich lückenlos aufeinanderfolgenden Zonenfolge nach Suspendierung
ihrer ersten Zonen. Identitäten und Dauern müssen unverändert sein; sämtliche
Zonen und der Gesamtzustand müssen frisch als inaktiv bestätigt sein. Dann
bleibt der ursprüngliche Ablauf erhalten. Alle sechs beobachteten Änderungen
werden im lokalen Replay als eigene Wirkung erkannt.

Andere Planänderungen werden weiter geprüft. Überschneidungen werden nicht mehr
als neuer Plan gespeichert. Bei einer ausschließlich geänderten Dauer werden
nachfolgende Starts erforderlichenfalls nach hinten verschoben; ursprüngliche
Pausen bleiben erhalten. Ein schon beschädigter Altplan wird nicht erraten oder
automatisch repariert.

### 2. Erneuerte Sperrbestätigung wird nach Sollstart unbrauchbar

Die bisherige Gültigkeitsprüfung verlangt trotz erfolgreicher Erneuerung, dass
die ursprüngliche Startzeit noch in der Zukunft liegt. Außerdem verwirft der
Erneuerungspfad seine eigenen Beobachtungen. Nach Ablauf des Sollstarts kann
die Freigabe so nie über den nächsten Zyklus hinaus wirksam werden.

Eine mit zwei unterschiedlichen Quellbeobachtungen bestätigte Erneuerung wird
jetzt gespeichert und ist innerhalb der bestehenden dreiminütigen Frist auch
nach Sollstart nutzbar. Die Frist beginnt an der bestätigten Quellzeit, nicht
erst beim späteren Ende eines HTTP-Abrufs. Serialisieren und neu Laden des
Zustands erhalten diese Bestätigung. Planänderung, Löschen, Abbruch und
Fehler-Rücksetzung verwerfen sie vollständig.

Unmittelbar vor jedem Zonenstart bleiben aktuelle vollständige Wasserdaten,
die aktuelle Mäherprüfung, Sperren, Befehlsjournal, Revision und Zeitfenster
verbindlich. Die 180-Sekunden-Grenze wurde nicht verlängert. Bewässerungsmenge,
150-Minuten-Nachlauf und die automatische Grenze 03:30–08:00 bleiben bestehen.
Die bestätigte manuelle Bedienung behält ihre bisherige separate Regel.

## Tests und Grenzen

- Die vollständige Python-Suite ist im [Testprotokoll](python-tests.txt) dokumentiert.
- `test_hydrawise_suspension_compaction.py`: sechs aufgezeichnete Quellenänderungen,
  echte abweichende Verschiebung, Daueränderung in beide Richtungen sowie fehlende,
  veraltete und widersprüchliche Wasserbestätigung.
- `test_irrigation_revalidation_late.py`: Start nach zwei Beobachtungen und
  Prozessneustart, Antwortverzögerung, erneutes Auftauchen nativer Starts,
  alte Mäherdaten, Offlinezustand, lange Lücke und Verwerfen alter Nachweise.
- Unabhängiges begrenztes Review mit Luna: Aggregatprüfung, Rücksetzung aller
  Bestätigungsfelder und Zeitunterschied zwischen Quelle und Verarbeitung wurden
  nachgeschärft. Gerätezugriffe in Tests sind ersetzt; die Testsuite sperrt Netzverkehr.

Die Fehlerbehebung ersetzt keine Live-Abnahme der kompletten sieben Zonen.
Husqvarna meldete im untersuchten späteren Stationsaufenthalt etwa alle 15,5
Minuten einen neuen Statuszeitstempel. Die aktuelle
[Herstellerbeschreibung zu WebSocket-Ereignissen](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/WebSocket.md)
beschreibt strom-/verkehrsbedingte Abstände bis etwa 15 Minuten. Ein erfolgreicher
REST-Abruf oder WebSocket-Ping ist deshalb kein neuer physischer Stationsnachweis.
Ein WebSocket allein beseitigt diese Wartezeit nicht.

## Vollständige Sequenz und verbleibende Wartezeit

Die [vollständige lokale Simulation](full-run-simulation.json) führt den echten
Controller mit ersetzten Geräten aus. Annahmen: bereits bestätigte Station und
vollständig suspendierter Plan, Minutentakt, ein Mäherereignis alle 15 Minuten,
stets vollständige Wasserdaten und freie Belegung. Erfassung/Suspendierung vor
dem Start, Netzausfälle, reale Hydraulik und manuelle Eingriffe sind hier nicht
nachgebildet. Es handelt sich nicht um einen live beobachteten Durchlauf.

| Planstart | Ergebnis nach beiden Codekorrekturen | Reine Wasserzeit | Grenze |
| --- | --- | --- | --- |
| 04:30 | Starts 04:32, 05:00, 05:30, 06:00, 06:30; fünfte Zone endet 06:50 | 100 von 160 Sollminuten | Zonen 6/7 passen ab 07:00 nicht mehr vollständig vor 08:00; korrekte Sperre statt Verkürzung |
| 03:30 | Starts 03:32, 04:00, 04:30, 05:00, 05:30, 06:00, 06:33; letzte Zone endet 07:03, bestätigt 07:05 | Alle 160 Sollminuten | In diesem Szenario 55 Minuten Reserve nach Bestätigung; kein Beweis für alle Ereignisphasen/Ausfälle |

Die zwei Reparaturen allein belegen folglich noch keinen zuverlässigen
vollständigen 04:30-Lauf. Eine bloße erfolgreiche Simulation des ersten Starts
wäre ein unzureichendes Abnahmekriterium. Der reale Zeitplan wurde nicht auf
03:30 geändert. Vor einer Änderung ist die vom Nutzer gewünschte fortbestehende
Parksperre vorrangig zu prüfen, damit Wartezeit nicht nur nach vorn verschoben wird.

## Nutzerregel: bestätigtes Parken fortführen

Der zentrale Sender verwendet `ParkUntilFurtherNotice`. Laut aktueller
[Husqvarna-Befehlsspezifikation](https://docs.developer.husqvarnagroup.cloud/automower-connect-api/swagger.yml)
parkt dieser Befehl unbefristet; `mode=HOME` bestätigt diesen Modus. Ein
`PARKED_IN_CS` allein bezeichnet dagegen nur den Zustand in der Station.
`FORCE_PARK` allein ist laut Spezifikation ebenfalls kein unbefristeter Nachweis.

Ein fortbestehender eigener HOME-Parkauftrag ist deshalb die richtige Grundlage
für eine spätere Freigabe ohne Wartezeit auf unveränderte Geräteereignisse.
Im unten beschriebenen ersten Paket war diese zusätzliche Änderung noch nicht
implementiert. Die [Fortsetzung](PARK_HOLD.md) setzt nun diese Anforderungen um:

- eine zunächst frische Stationsbestätigung nach dem eigenen Parkbefehl an
  Geräte-ID und Befehls-/Bediengeneration binden;
- Mäherstarts während der aktiven Bewässerung zentral weiterhin verhindern und
  die bestehende ausdrückliche Konfliktentscheidung erhalten;
- jeden beobachteten Fremdeingriff, Startauftrag, Moduswechsel, Gerätefehler,
  Kommunikationsausfall oder unklaren Wiederanlauf als Entwertung behandeln;
- direkt vor jedem Ventilstart erneut denselben Zustand und die unveränderte
  Bediengeneration prüfen;
- den Zugriff über Husqvarna/am Gerät ausdrücklich berücksichtigen: ein noch
  nicht übermittelter manueller Start ist für die Zentrale nicht erkennbar.

Die externe App und der Knopf am Gerät lassen sich über die vorliegenden APIs
nicht durch eine lokale Softwarevariable verriegeln. Ein verspätet gemeldeter
Fremdstart bleibt daher ein technisches Restrisiko. Auch die bestehende
180-Sekunden-Regel schließt dieses nicht vollständig aus. Der betriebliche
Grundsatz muss weiterhin sein: vor einem externen manuellen Start während
Bewässerung zuerst deren bestätigtes Ende abwarten. Keine unbemerkte Lockerung
der bisherigen Grenze nur aufgrund eines alten `PARKED_IN_CS`.

## Reviewfähiges Paket

Codecommit `1b7f8a85454cd390c30ac9ffb1c26251cb6c6812`.
[Paketnachweis](package-proof.json): nur `mower/full_failsafe.py`,
`mower/irrigation_recovery.py` und Manifest ändern sich. Alle übrigen Paketbytes
sind identisch zur zuvor nachgewiesenen Installation und ihr Inhalt entspricht
dem Quellcommit. Offline importiert: 16 Funktionen, Dateiprovenienz gültig,
Netzwerk gesperrt. ZIP-SHA256:
`ac519e857df869837e09ca2ecb643b08f813746c2c3fb27be133d1cfbe3fa073`.

1.476 Python-Tests plus 482 Untertests bestanden. Die danach ergänzte
Vollsequenzsimulation wurde gesondert in beiden Startvarianten geprüft.
Das Paket ist **gebaut und geprüft, nicht veröffentlicht**. Eine Freigabe der
kompletten unbeaufsichtigten Automatik wird daraus nicht abgeleitet.

## Einführung, Überwachung und Rückfall

1. Geprüftes Paket aus dem nachgewiesenen Altartefakt und ausschließlich den
   beiden geänderten Steuermodulen bauen. Alle übrigen Dateien und Einstellungen
   unverändert halten. ZIP, Manifest und Quelldiff im Paketnachweis festhalten.
2. Vor Installation erneut aktuelle Phase, aktive Zonen, offene Befehle und
   Stationszustand prüfen. Während einer laufenden Zone oder unbestätigten
   Start-/Stoppaktion kein ungeprüfter Neustart. Keine Testbewässerung auslösen.
3. Nach Installation alle 70 Dateien am Host, die unveränderten Flags und neue
   Steuerungszyklen vergleichen. Workflow-Erfolg allein reicht nicht.
4. Keine heutige Vollbewässerung automatisch nachholen: der ursprüngliche Bedarf
   umfasst 160 Minuten reine Ventillaufzeit und passt jetzt nicht mehr bis 08:00.
   Nicht kürzen oder als erledigt verbuchen. Der heutige Lauf ist laut Protokoll
   bereits durch Bedienung beendet; deshalb keinen zusätzlichen Reset ausführen.
5. Den nächsten überwachten vollständigen Lauf abnehmen: genau ein Start je Zone,
   bestätigte jeweilige Enden, alle sieben Sollzeiten erfüllt, keine Überlappung,
   Abschluss spätestens 08:00, kein Mäherstart während Wasser/Nachlauf.
   Mindestens zwei vollständige Tage und ein beobachteter Wiederanlauf sind
   nötig, bevor normale unbeaufsichtigte Zuverlässigkeit behauptet wird.
6. Abbruch bei erneutem unverändertem Revalidation-Kreislauf, widersprüchlichen
   Zonen, unbestätigten Befehlen, Mäher außerhalb der Station oder wenn die
   vollständige Restfolge nicht mehr in das zulässige Fenster passt. Dann genaue
   Ursache sichtbar machen und gezielt betreute Bewässerung planen. Ein Lade-
   oder Parkereignis erzeugt keinen zusätzlichen Wasserbedarf.
7. Rückfall: aktuelles Journal, Ventile und native Suspendierungsfristen zuerst
   sichern; bereits gesendete Aktionen berücksichtigen. Keine ungewisse Aktion
   wiederholen. Danach bei Bedarf das bytegenau gesicherte vorherige Paket
   installieren. Dieses enthält die hier belegten Fehler; ein Code-Rückfall
   allein ist daher kein betriebsbereiter Ersatz für den betreuten Ablauf.

Eine Benachrichtigung bei ausbleibendem Start und gefährdetem Abschluss ist
fachlich sinnvoll. Diese Untersuchung hat keine Nachrichten verschickt,
zusätzlichen Empfänger eingetragen oder neue kostenpflichtige Dienste angelegt.
