# Ausgefallener Nachtstart und gesperrter App-Start

## Nachgewiesener Fehler

Am 10.09.2026 sollte die Automatik nach der Belegung um 22 Uhr wieder starten.
Die Produktionsspuren zeigen folgende Folge (Zeiten Europe/Berlin):

| Zeit | Nachweis | Bedeutung |
|---|---|---|
| 22:00:03 | `MOWER_START_SEND_BLOCKED`, `command_sent=false`, `PRE_SEND_BLOCKED`, `MOWER_STATUS_STALE` | Der Start wurde vor dem Geräteaufruf abgewiesen. Die letzte Gerätemeldung war von 21:52:56 und 428 Sekunden alt. |
| 22:01:03 | `MOWER_START_OUTCOME_UNCONFIRMED` | Eine reservierte Anfrage wurde fälschlich wie ein möglicherweise gesendeter Befehl behandelt. |
| 22:09:08 | Meldung von 22:08:26, nur 42 Sekunden alt; weiterhin derselbe Sperrgrund | Neue Telemetrie konnte die künstliche Dauersperre nicht auflösen. |
| 11.09., 04:30 | Erste erfasste aktive Bewässerungszone | Ab jetzt besteht zusätzlich ein tatsächlicher Wasserkonflikt. |
| 06:55 | Nutzerfoto: „Mäherstart nicht bestätigt“, Start und Automatik ausgegraut | Die App zeigt die persistierte Sperre. Der Akku war voll; fehlende Ladung war nicht die Ursache. |

Die 718 exportierten Minutenbeobachtungen umfassen den Übergang vor und nach
dem Deployment zur schnelleren Seitenanzeige. Der Nachtstartfehler trat bereits
**vor** diesem Deployment auf. Die zuvor installierten 69 Paketdateien wurden am
11.09. erneut byteweise gegen das veröffentlichte Paket geprüft; die Schutzflags
sind unverändert. Nachweise: `incident.json`, `installation-before.json`.

Der Controller speicherte vor dem letzten Versandcheck `mower_start_pending_*`.
Der Versandcheck lehnte alte Telemetrie korrekt ab, ließ aber diese Reservierung
stehen. Der nächste Zyklus interpretierte sie als ungewissen Geräteauftrag.
Diese Schutzsperre ist für verlorene Antworten erforderlich; bei nachweislich
nicht erfolgtem Versand war ihre Anwendung falsch.

Das Paket zur ursprünglichen Spur ist ebenfalls vorhanden:
`trainer-dashboard-full-failsafe.zip`, Manifest
`8b17b7d232de8a32593c0c2aee55b3cd8527d29338c2be9626199ef72fac3d77`.
Die Steuerungsdatei und der letzte Versandcheck sind in diesem Paket und im
danach installierten Ladezeitpaket bytegleich. Die Reparaturfreigabe im Code gilt
ausschließlich für diesen überprüften historischen Startzeitpunkt samt Endzeit,
Quellmanifest und Ablehnungsgrund. Das ist eine einmalige Datenkorrektur zusätzlich
zur allgemeinen Ursachenbehebung; neue unbekannte Vorfälle werden nicht automatisch
mit freigegeben.

## Korrektur

1. Bereits veraltete Mäherdaten werden vor einer Startreservierung abgefangen.
2. Lehnt ausschließlich der eigene letzte Versandcheck ab, kann der Controller
   seine unveränderte Reservierung mit einem atomaren Versionsvergleich aufheben.
   Die vorherige Befehlskennung und ein schon bestätigtes Mähintervall bleiben
   erhalten. Danach beginnt im nächsten Zyklus eine vollständige neue Prüfung.
3. Verlorene Antworten, Prozessabbruch, widersprüchliche parallele Änderungen und
   nicht zuordenbare Ausnahmen behalten ihre Sperre. Ein Zeitablauf oder ein
   geparkter Mäher ist ausdrücklich kein Beweis für einen leeren Geräteauftrag.
4. Die App liefert auch bei frischer Telemetrie kein Startrecht, solange wirklich
   ein Start ungeklärt ist. Bei alten Daten erklärt die Bedienung in einfacher
   Sprache, dass eine neue Mähermeldung benötigt wird. Parken bleibt erreichbar.
5. Die schon vorhandene falsche Reservierung benötigt eine getrennte, eng
   begrenzte Reparatur anhand der ursprünglichen serverseitigen Ablaufspur.
   Ein allgemeines „Sperre ignorieren“ wird nicht eingeführt.

Manuelles Starten benötigt weiterhin aktuelle Gerätemeldungen und die jeweils
erforderlichen Bestätigungen für Training/Spiel, Trockenzeit und Bewässerung.
Ein vorgemerkter Start aus alten Gerätezuständen wird mit diesem Fehlerfix nicht
eingeführt. Ein Tippen auf „Aktualisieren“ kann keine neue Gerätemeldung erzwingen.

## Risiken und Absicherung

| Risiko | Absicherung | verbleibende Grenze |
|---|---|---|
| Verlorene Antwort wird für „nicht gesendet“ gehalten | Herkunftsnachweis nur aus dem eigenen Check vor HTTP; Transportausnahmen bleiben ungewiss | Prozessabbruch zwischen Reservierung und Auflösung kann weiterhin manuelle Klärung erfordern. |
| Anderer Controller oder manueller Stopp wird überschrieben | Exakter Zustandsvergleich und atomare Revision/ETag-Prüfung; keine Auflösung bei Konflikt | Bei Konflikt bleibt die Sperre konservativ bestehen. |
| Historische Reparatur löscht eine echte Startsperre | Server liest Originalspur selbst; bekannte Quellversion, passender Startzeitpunkt/Endzeitpunkt und unveränderte Bedienung sind Pflicht | Fehlender Nachweis bedeutet Abweisung. |
| Automatik startet während Wasser oder Trockenzeit | Reparatur sendet keinen Gerätebefehl und ändert keine Wasser-, Belegungs- oder Trockenzeitdaten | Die anschließende Automatik muss alle aktuellen Bedingungen erneut prüfen. |
| Administrativer Reparaturzugang wird missbraucht | Ausschließlich Azure-Administratorauthentifizierung, Vorschau und exakter Versionsvergleich | Administratorschlüssel bleiben besonders schützenswert; keine Weitergabe an die App. |
| Veraltete Telemetrie blockiert kurzzeitig | Nächster Zyklus kann mit neuer Meldung erneut prüfen | Die Grenze von drei Minuten wird nicht verlängert. |

Ein Gewinn an produktiven Mähminuten wird noch nicht behauptet. Belegt ist der
verhinderte Wiederanlauf trotz neuer Meldung um 22:09 Uhr. Ladebedarf, Fahrzeit,
spätere Bewässerung und andere Sperren begrenzen die nutzbare Zeit weiterhin.

## Prüfung und Einführung

- Isolierte Tests senden standardmäßig keine Gerätebefehle.
- Abgedeckt: alte Daten, neue Daten im Folgezyklus, OAuth-Verzögerung, verlorene
  Antwort, Parallelprozess, manueller Parkauftrag, fehlgeschlagene Auflösung,
  über Neustart erhaltene Reservierung und unverändertes früheres Mähintervall.
- Die historische Reparatur wird zusätzlich gegen unpassende Version/Spur,
  einen echten Versand, geänderte Bedienung, Wiederholung und CAS-Konflikte geprüft.
- Vor Veröffentlichung: vollständige relevante Regressionen, unabhängige Prüfung,
  identischer PR-Kopf mit erfolgreichen CI-Prüfungen und Paketvergleich.
- Nach Veröffentlichung: installierte Dateien und Schutzflags prüfen, zunächst
  Reparaturvorschau, dann ausschließlich die belegte Reservierung korrigieren.
- Anschließend mehrere Controllerzyklen prüfen: falsche Sperre verschwunden,
  tatsächliche Wasser-/Trocken-/Belegungssperren erhalten, keine unbestätigte
  Wiederholung eines Starts.

Bei unpassender Reparaturvorschau oder geändertem Zustand erfolgt kein Eingriff.
Rückfallpaket ist `page-loading-full-failsafe.zip`, SHA256
`041ae5ec2c8fa5633248a3bcb74300fd95e02d99aa17f40115d44f8f4d433582`.
Ein Coderückfall darf bereits gesendete Geräteaktionen und nachträgliche manuelle
Eingriffe nicht durch Rückschreiben eines alten Zustands überschreiben. Vor einem
Rückfall sind deshalb aktueller Geräteauftrag, Bewässerung und Bedienung abzugleichen.

Der direkte Tabellenzugriff des lokal angemeldeten Azure-Benutzers ist nicht
berechtigt. Es wurden weder Rollen erteilt noch Speicherschutzregeln geändert.
Die administrative Reparatur verwendet die bestehende Backendidentität und einen
eng begrenzten, geschützten Anwendungspfad.

## Ausführungsnachweis

Entwicklung und vollständige Tests sind durchgeführt: **1.411 Python-Tests und
479 Teilprüfungen**, außerdem **152 Appack-Tests**, jeweils erfolgreich.
Der abschließende Python-Lauf ist in `python-tests-final.txt` festgehalten;
`python-tests.txt` dokumentiert die erste Fassung mit 1.408 Tests.
Die ersten drei Integrationsfehler im Testlauf sind behoben: Die Reparatur wird
nur vom FULL_FAILSAFE-Paket mitgeliefert und erst nach Prüfung der Betriebsfreigabe
geladen; die strikten Paketprüfungen der niedrigeren Freigabestufen bleiben bestehen.
Die Prüfung der Azure-Funktionsnamen erfolgt einmal, ohne die Testregistrierung
doppelt aufzubauen. Die neue Route ist ausschließlich für den Administratorschlüssel
freigegeben. Eine unabhängige Luna-Prüfung und die zusätzliche Hauptprüfung fanden
keine weitere konkrete Abweichung in der Startkorrektur. Die Reparatur selbst wurde
zusätzlich auf Sperren, Quellenprüfung und Befehlsfreiheit geprüft.

PR [#68](https://github.com/Rohdeo87/ssv53-heimspiele/pull/68) wurde nach beiden
erfolgreichen CI-Prüfungen zusammengeführt; Merge
`8a16f96302313827d042763e7309a78be08ed7a9`. Das erste Paket wurde installiert:
Deployment `ddee3074-449e-4b14-8fd4-a537f2cb2463`, 70 Dateien byteweise geprüft,
16 Funktionen und unveränderte Schutzflags (`installation-first.json`).

Die erste Reparaturvorschau blieb korrekt **inaktiv**, weil die Logabfrage
UTC-Zeitstempel als Text verglich: Kusto formt `+00:00` in siebenstellige
Sekundenbruchteile mit `Z` um. Der typisierte Zeitvergleich findet unter 580
abgerufenen Ablaufspuren genau den geprüften Fehlversuch (`normalized-trace-proof.json`).
Ein zusätzlicher Regressionstest deckt dieses reale Rückgabeformat ab. Die
Reparatur verwendet außerdem dieselben strikten Prüfer für manuelle Sitzungen und
Befehlsnachweise wie die Steuerung. Ein unvollständiger Testzustand wurde bereits
an der Zustandsgrenze abgewiesen; der entsprechende Test prüft diese Abweisung.

PR [#69](https://github.com/Rohdeo87/ssv53-heimspiele/pull/69) ergänzt den typisierten
Zeitvergleich und die kanonische Sitzungs- und Befehlsprüfung. Die
[Codeprüfung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34567259365)
und der [Paketbau](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34567259385)
waren für den unveränderten Kopf `4b9bbc96f447b3a3f4259199d0c2237f02bc26d3`
erfolgreich. Der tatsächliche Merge ist `52e330188d072698b5f8d553500640f8c4ca5e1f`.

Das zweite Paket ist installiert: Deployment
`c9cae529-8e2b-42d7-b17d-ac4b727930b5`, SHA256
`401123d45e647f2c176f5e5a07e6f2149f44285218358e51700f4e851553c24c`, Manifest
`58bbb8d780c10751161f01027f2440d76a0dcf38362baf6825a50b7af106b7c5`.
Die Prüfung bestätigte **70 bytegleiche Dateien, 16 Funktionen und unveränderte
Schutzflags** (`installation-after.json`, `package-comparison.json`). Gegenüber
dem ersten Korrekturpaket änderten sich nur das Reparaturmodul und das Manifest.

Die erste Ausführung nach längerer Werkzeugfreigabe wurde lokal wegen einer
über zwei Minuten alten Vorschau abgewiesen. Eine erneute Vorschau war zulässig,
doch der anschließende atomare Versionsvergleich wies einen inzwischen geänderten
Steuerungszustand mit HTTP 409 ab (`recovery-conflict.json`); es erfolgte kein
Schreibzugriff. Daraufhin wurde der aktuelle Zustand erneut geprüft.

Am **11.09.2026 um 08:23:48 Uhr (Europe/Berlin)** wurde die passende Korrektur
bestätigt angewandt: Revision 44298 → 44299, HTTP 200 und `applied=true`.
Die Originalspur `3bdc5d4a-ad52-11f1-aa9f-7ced8d24030b` belegt den nicht gesendeten
Start. Die dokumentierte Änderung entfernt ausschließlich dessen Reservierung
und stellt die vorherige Wiederanlaufberechtigung aus der Trainingspause wieder
her (`preview-applied.json`, `recovery-applied.json`). Der Reparaturaufruf hat
keinen Gerätebefehl angefordert.

Die folgenden **drei Controllerzyklen um 08:24, 08:25 und 08:26 Uhr** bestätigen
die Zustandskorrektur (`controller-after.json`):

- Die Startreservierung bleibt leer; die falsche Ungewissheitssperre kehrt nicht wieder.
- Der neue Sperrgrund ist die tatsächlich verbleibende Trockenzeit.
- Bewässerungsdaten sind aktuell, keine Zone läuft; Ende der Trockenzeit bleibt
  unverändert **09:40 Uhr**, gerechnet ab dem erfassten Bewässerungsende um 07:10.
- Der Mäher meldet weiterhin Station. In diesen drei Zyklen wurde kein Gerätebefehl gesendet.
- Der Wiederanlauf ist wieder berechtigt und muss weiterhin alle aktuellen
  Daten-, Wasser- und Belegungsbedingungen erfüllen. 09:40 ist eine frühestmögliche
  Freigabe, kein zugesicherter physischer Startzeitpunkt.

Die neue Serverlogik ist damit **umgesetzt, getestet, installiert und in drei
Produktionszyklen beobachtet**. Tatsächliches Mähen nach 09:40 und ein manueller
Start aus der angemeldeten Handy-App sind **noch nicht live nachgewiesen**.
Es wurde weder eine angemeldete App-Sitzung nachgebildet noch ein Teststart am
Gerät ausgelöst. Die bestehende App übernimmt die Bedienberechtigung vom Backend;
ihre Vorlagen wurden durch diese Fehlerkorrektur nicht geändert.

Der Startknopf kann bei mehr als drei Minuten alten Mäherdaten weiterhin zeitweise
deaktiviert sein; die Ursachenbehebung hebt diese Datenprüfung nicht auf. Bei
frischen Daten prüft der bestehende manuelle Startweg die erforderlichen expliziten
Ausnahmen erneut. Ein vorgemerkter Start während alter Telemetrie bleibt als
separate Bedienentscheidung offen. Ein angenommener Startbefehl ist noch kein
bestätigtes Mähen.
