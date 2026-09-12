# Bewässerung nach bestätigtem Aufenthalt in der Station

## Auftrag und Abgrenzung

Der Nutzer hat am 12.09.2026 ausdrücklich zugestimmt, bei einem gestoppten Mäher eine Bestätigung „Ich sehe den Mäher in der Station“ anzubieten. Die Bewässerung darf dafür den physischen STOP nicht aufheben. Die gleichzeitig angefragte [konkrete Mäherstörung](../concrete-mower-error/README.md) ist eine separat veröffentlichte Anzeigekorrektur: Der danach gemeldete Fehler 9 („Mäher steckt fest“) erfüllt diese zusätzliche Freigabe ausdrücklich nicht.

**Livefreigabe am 12.09.2026:** Auf die ausdrückliche Anweisung „Bitte live setzen“ wurden Backend und Appack-Vorlage veröffentlicht und die Stationsbestätigung sowie die Abschlussbereinigung aktiviert. Die Voreinstellung im Code bleibt ausgeschaltet. Nachweise der Veröffentlichung und ihre Grenzen stehen im [Livebericht](live/README.md). Es wurde kein Geräte-Testlauf gestartet; die physische Abnahme der neuen Stationsbestätigung bleibt offen.

## Bedienung

Bei berechtigter Bedienung, vollständig verfügbarem Wasserstatus und geeigneter Mähermeldung erscheinen „Alle Zonen starten“ sowie die Einzelzonenaktionen. Der Bestätigungsdialog verlangt zusätzlich:

> Ich sehe den Mäher in der Station.

Darunter steht: „Vor einem Start direkt am Mäher oder über Husqvarna muss die Bewässerung beendet sein.“ Die Bestätigung gilt für genau den ausgewählten Auftrag. Sie ist keine allgemeine Freigabe und keine dauerhafte Annahme des Standorts.

Bei geänderter Meldung, aktivem Fehler, fehlender Verbindung oder geänderter Auftragsbindung wird keine alte Bestätigung gesendet. Der bestehende Stopp und die Regeln für Belegung, Wasserfreiheit und Trockenzeit beim späteren Mäherstart bleiben bestehen.

## Technischer Vertrag

- Schalter: `IRRIGATION_ONSITE_DOCK_CONFIRMATION_ENABLED=false` als Voreinstellung, produktiv seit 12.09.2026 um 15:26 Uhr aktiviert; zusätzlich ist `ENABLE_MANUAL_SESSIONS=true` samt persistentem Befehlsjournal erforderlich.
- Status: `irrigationDockConfirmation` mit `enabled`, `required`, `canConfirm`, `contextToken`, `expiresInSeconds`, `reason`.
- Zulässige Ausgangsmeldung: exakt der konfigurierte Mäher, verbunden, `STOPPED`, `NOT_APPLICABLE`, `HOME`, expliziter ganzzahliger Fehlercode 0; keine offenen Gerätebefehle, Startreservierung, Wartung, aktive Bewässerungsphase oder konkurrierender Auftrag. Ein frischer Herstellerstatus ist für die erstmalige Aufnahme erforderlich. Die allgemeine 180-Sekunden-Grenze wird nicht geändert.
- Der vorhandene geschützte `POST /platzwart/action` erhält Vertrag 4, die normale Aktionsbestätigung und `manualControl: {operation: "CONFIRM_DOCK_FOR_IRRIGATION", confirmed: true, contextToken}`. Normale Wasseraufträge ohne Stationsbestätigung verwenden weiterhin Vertrag 2. Einzelzone und Laufzeit bleiben Teil des Auftrags.
- Der Server liest vor Annahme erneut und speichert per CAS. Die Bindung hängt von Steuerungsdaten und Mähermeldung ab, nicht von einer allein durch Routinezyklen erhöhten Revision.
- Persistenz: `irrigation_onsite_dock_proof_json`, zunächst `REQUESTED`, danach an unveränderliche Plan-ID und SHA-256 des Plans gebunden (`BOUND`). Die Aufnahme muss innerhalb von 120 Sekunden in einen Plan übergehen. Mäher, Auftrags-ID, Aktion, Zone und Dauer werden geprüft.
- Das absolute Programmende wird einmal aus den gewählten Laufzeiten, den Endbestätigungen und zehn Minuten Reserve berechnet; höchstens acht Stunden. Ein Neustart oder weitere Abfrage verlängert dieses Ende nicht.
- Während des gebundenen Laufs sind erfolgreiche direkte Lesungen mit maximal 90 Sekunden Abstand erforderlich. Ein unverändertes älteres STOP-Ereignis ist bei solcher Lesekontinuität zulässig; rückwärts laufende Ereigniszeiten, Leselücke, Abfahrt, Fehler, Offlinezustand oder Steuerungswechsel widerrufen den Nachweis.
- Unmittelbar an der Wasser-Sendegrenze gilt der bisherige sichere Stationszustand **oder** der gültige, genau zu diesem Plan gehörende Vor-Ort-Nachweis. Der neue Pfad entfernt keine normalen Schutzbedingungen.
- Bekannte sichere Hersteller-Overrides werden bei Aufnahme geprüft und zusätzlich unveränderlich gebunden. Eine neue Startvorgabe oder ein anderer Override widerruft die Bestätigung auch bei noch unveränderter Aktivität und identischem Zeitstempel.
- Der komplette Installationssatz enthält das neue Modul. Varianten ohne Bewässerungssteuerung laden die Konsole weiterhin ohne dieses Modul. Ein irrtümlich gesetzter Schalter bei fehlender Komponente erzeugt keine Freigabe. Diese Grenze wurde durch echte Importe aus ausgepackten Installationsarchiven geprüft.

## Prüfung und Belege

- Appack-Suite: **227 Tests bestanden**. Neue Fälle prüfen fehlende/ungeprüfte Bestätigung, Freigabe nur durch das Backend, Meldungs-/Tokenwechsel im offenen Dialog, Fehler 9, Offlinezustand, bereits laufendes Wasser sowie Erhalt von Einzelzone/Laufzeit und normalem Vertrag 2. Der Test führt den tatsächlichen Bestätigungs-Handler mit einem Ersatzsender aus.
- Abschließende Backend- und Paketprüfung: **338 Tests und 81 Subtests bestanden**; [Protokoll](backend-tests.txt). Davon 36 gezielte Proof-Tests: Aufnahme, zwei Zonen nacheinander, Neustart, alte unveränderte STOP-Ereignisse bei fortlaufenden Lesungen, Overridewechsel, Verbindungsfehler, verlorene Sendeantwort, konkurrierende Speicherung, Schutz-PARK und unterbrochene Endbestätigung. Die ergänzten Paket-Metadaten wurden danach nochmals gezielt erfolgreich geprüft.
- [Synthetischer Ausgangszustand](fixture.json), [sichtbarer Dialog ohne gesetzten Haken](preview-dialog.txt). Die Vorschau sperrt Netzwerkzugriffe und ersetzt sämtliche POSTs durch einen lokalen Fehler. „Alle Zonen starten“ ist im passenden Zustand erreichbar; ohne gesetzten Haken bleibt der Dialog offen und fordert die Bestätigung an.
- Die 390-px-Ansicht wurde im Browser geöffnet. Dialogbedienung wurde in der isolierten Browserseite geprüft. Keine native Geräteabnahme und kein ausgeführter Bewässerungsversuch.

## Verbleibende Sicherheitsgrenze und Einführung

Die Überwachung kann einen Start direkt am Gerät oder über die Hersteller-App erst nach einer Rückmeldung erkennen. Selbst ein automatischer Wasserstopp kann deshalb keine verzögerungsfreie Verriegelung vor der Abfahrt garantieren. Vor solchen Starts muss die Bewässerung beendet sein. Ein STOP-Status allein beweist den Standort nicht; eine falsche Vor-Ort-Bestätigung bleibt ein menschliches Fehlerrisiko.

Der Schutzstopp bei verlorenem Stationsnachweis ist implementiert und mit Ersatzsendern geprüft: ausschließlich die bereits zugeordnete eigene Zone wird über eine dauerhafte Reservierung und das Befehlsjournal gestoppt. Die Bindung bleibt über mehrfache Lesefehler, Neustart, Operatorwechsel und das Ausschalten des Schalters erhalten. Ein möglicherweise angenommener Stop wird nicht blind erneut gesendet. Bei gemeldeter Abfahrt folgt ein eigener protokollierter Schutz-PARK für genau den konfigurierten und vorher gebundenen Mäher. Erst vollständige, konsistente und fortlaufende Wasser-Endbeobachtungen schließen den Lauf ab; aktive oder unklare Zwischenmeldungen beginnen diese Bestätigung neu. Die volle Trockenfrist bleibt auf das Bewässerungsereignis bezogen.

Zwei unabhängige Reviewdurchgänge und die Integration durch den Hauptagenten fanden und behoben: fehlende Overridebindung, Verlust des Stopptargets nach wiederholten Fehlern/Bedienung/Schalterwechsel, unterbrochene Endbeobachtungen, blockierten Schutz-PARK und fehlenden Zielvergleich beim Schutz-PARK. Das Abschlussreview meldet keinen weiteren reproduzierbaren Blocker im geprüften Umfang. Dies ersetzt keine Abnahme mit echten Geräten.

| Risiko | Erkennung und Behandlung | Verbleibende Grenze |
|---|---|---|
| Falsche Bestätigung des Standorts | Ausdrückliche Sichtbestätigung, konkreter Mäher und genau ein Auftrag; keine Übernahme aus `HOME` allein | Der Server kann die tatsächliche Sichtprüfung nicht unabhängig beweisen |
| Externer Start während Wasser läuft | Override-/Bewegungswechsel widerruft die Freigabe; persistenter Stopp für die eigene Zone; protokollierter Schutz-PARK bleibt erreichbar | Cloud- und Geräteverzögerung; vor externem Start muss Wasser beendet sein |
| Lesefehler, Neustart oder geänderter Auftrag | Unwiderruflicher Verlust der Startfreigabe; Stopptarget darf über wiederholte Ausfälle und Bedienaktionen nicht verloren gehen | Bei Geräte-/Netzausfall kann kein physischer Stopp garantiert werden |
| Antwort des Wasserstopps geht verloren | Ungeklärter Sendebeleg bleibt bestehen; keine blinde Wiederholung; physisches Ende separat bestätigen | Angenommener Befehl ist noch kein Ausführungsnachweis |
| Wasser erscheint zwischen Endbeobachtungen erneut | Endbeobachtungen verwerfen; Bestätigungsdauer neu beginnen; Trockenereignis nach tatsächlichem Ende bewahren | Keine Verkürzung fachlicher Trockenzeiten ohne gesonderte Entscheidung |
| Rückfall während eines Laufs | Neue Starts sperren; laufenden bzw. möglicherweise angenommenen Auftrag samt nativem Zeitplan und Trockenfrist abgleichen | Das Ausschalten des Schalters allein beendet kein Wasser |

Tests, unabhängiges Review, Installationsartefakt und Abgleich der tatsächlich laufenden Version und Konfiguration wurden für die ausdrücklich freigegebene Veröffentlichung durchgeführt. Für die Geräteabnahme bleibt ein überwachter Pilot mit physischem Dock- und Wasserendnachweis erforderlich. Dabei müssen STOP bestehen bleiben, genau ein gewünschter Durchlauf erfolgen und widersprüchliche Meldungen einen belegten Schutzstopp auslösen. Zeitgrenzen 03:30–08:00 Uhr gelten weiterhin für automatische Bewässerung, nicht als neue Einschränkung manueller Aufträge.

Rückfall umfasst mehr als das Ausschalten des Schalters: laufende bzw. möglicherweise angenommene Wasserbefehle und native Zeitpläne abgleichen, Wasserende bestätigen, Folgeaufträge sperren und vorhandene Trockenfristen erhalten. Erst anschließend den vorherigen Softwarestand wiederherstellen. Keine alte Stationsbestätigung rekonstruieren oder zurückkopieren.

## Nachweisstufen

| Stand | Ergebnis |
|---|---|
| Analysiert | STOP, fehlender Stationsnachweis, alter COMPLETE_HOLD, Fehler 9 und externe Startwege abgegrenzt |
| Implementiert | Opt-in-Aufnahme, Auftrags-/Planbindung, Widerruf, Wasserstopp, Schutz-PARK, Paketgrenzen und Bedienung |
| Getestet / simuliert | 338 Backend-/Pakettests, 81 Subtests, 227 Appack-Tests; synthetische Browserbedienung; keine Gerätebefehle |
| Parallelbetrieb | Für diese neue Erweiterung noch nicht durchgeführt |
| Live | Backend und Appack-Vorlage veröffentlicht, Stationsbestätigung und Cleanup aktiviert; installierte Dateien und laufende Zyklen geprüft. Kein physischer Testlauf der neuen Bestätigung |

Der nächste Abnahmeschritt ist ein überwachter Pilot mit bestätigtem Dock, beobachtetem Wasserstart/-ende, STOP-Erhalt und kontrollierter Abbruchsituation. Softwareaktivierung und physischer Ausführungsnachweis bleiben getrennt.

## Artefakt

[artifact.json](artifact.json) dokumentiert das frühere allgemeine Entwicklungspaket (`5c9feff`, SHA-256 `e4923cd32ad41991d7e6380a7b7232b4c4096eea4cfd96b9005fc655900fc76b`). Dieses wurde nicht unverändert veröffentlicht. Für die Liveinstallation wurde ein begrenztes Paket über dem bytegenau geprüften vorherigen Installationssatz gebaut. [package-clock-fix.json](live/package-clock-fix.json) enthält den endgültigen Quellcommit `4fe90d7532bde36c6bd00864e6cdc351bffc1442`, ZIP-Hash `c5bb19a88021b0276166038725ca95e24368df3d612e9ca6e6ba833ac9519d79` und Manifest-Hash `88e418f5c967991d6f842ad732e26ab223ecfd6be24709140816e5cd77d26264`. Der separate Installationsnachweis im Livebericht belegt die tatsächlich ausgelieferten Dateien.
