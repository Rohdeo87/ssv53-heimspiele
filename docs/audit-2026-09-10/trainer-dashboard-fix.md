# Trainerrechte und falsche Warteanzeige — 10. September 2026

Dieser Nachtrag betrifft die beiden gemeldeten Fehler und die bei ihrer Prüfung
gefundene fehlerhafte Rückkehr aus einer manuellen Mähfreigabe. Er ersetzt keine
Abnahme des gesamten Systems. Veröffentlichungsnachweise werden nach dem
tatsächlichen Rollout ergänzt; ein grüner Testlauf ist kein Gerätebeleg.

## Nachgewiesener Ausgangsstand

- Entwicklung auf `fix/appack-trainer-moves-20260910`, Ausgangscommit
  `c256fa5e95670e9f201eb4c2243022ec1f085bfb`; separater Worktree, andere
  Arbeitskopien und die offene Dokumentations-PR #61 bleiben erhalten.
- Vorher installiert: Paketmanifest
  `67600a05061ca393037ae872f2db6f6a4cb41499f06c0d622bfe3a351c990abc`.
  Die Minute für Minute protokollierte Steuerung meldet seit 09:42 UTC den bereits
  freigegebenen Vollbetrieb. Ein neues Datenbundle ist kein Software-Rollout.
- [Betriebsbeleg](trainer-dashboard-before.json): 359 Steuerungszyklen aus sechs
  Stunden, abgerufen am 10.09.2026 um 14:36 UTC. Export nur lesend, ohne
  Zugangsdaten; wiederholte zukünftige Kalenderlisten sind entfernt. Das Skript
  `scripts/read_grounds_diagnostics.py` begrenzt Zeitspanne und Datensatzanzahl.
- Letzter Stand dieses Exports: Mäher in der Station, Akku 100 %, Wasserstand
  aktuell, keine aktive Zone. Vorsorgliche Rasenpause bis **18:25 Uhr Berliner
  Zeit**. Nächste gemeldete Bewässerung am 11.09. ab 04:30 bis 07:10 Uhr.
  Das belegt die gemeldeten Zustände, keinen lückenlosen Blick auf die Ventile.

## Ursachen und Korrekturen

| ID | Ursache und Nachweis | Korrektur | Wirkung / Risiko / Abnahme |
|---|---|---|---|
| T1 | Die Kalenderoberfläche erkannte Trainer, der Schreibweg akzeptierte aber nur eine separate Platzwart-Sitzung. | Persönliche Appack-Anmeldung wird serverseitig über `whoami` geprüft. Aktive SSV53-Rollen TR oder AA erlauben Trainingsänderungen. | Trainer benötigen keine Platzwart-PIN. Ohne gültige Rolle keine Änderung. Aktive TR-Testidentität verlegt echte Serientermin-Fixtures mit unveränderten Puffern. |
| T2 | Clientseitige Identitätsangaben eignen sich nicht als Berechtigungsnachweis; mehrere Personen könnten dieselbe Befehls-/Termin-ID senden. | Identität ausschließlich aus Appack; getrennte Befehls- und Neuanlage-IDs je bestätigter Person. | Keine Übernahme fremder Einzelbelegungen durch erratene ID; Wiederholung derselben Anfrage bleibt idempotent. Tests mit zwei Trainern und gefälschten Angaben. |
| D1 | Nach frischer Meldung 13:52 UTC fehlten die Abfragen 13:53/13:54. Die neue Quellmeldung 13:55 ergab gegenüber der Zyklusuhr 180,000709 statt 180 Sekunden. Das löste nochmals 150 Minuten aus. | Für die physische Lücke fortschreitende Quellzeitstempel vergleichen. Aktuelle Datenbestätigung bleibt eine getrennte Prüfung. | Im reproduzierten Grenzfall entfällt die zusätzliche physische Frist. Ab 181 Sekunden, bei beobachtetem Wasser oder geplantem Wasser innerhalb der Lücke bleibt die volle Pause. Keine automatische Löschung einer bereits gespeicherten Pause. |
| D2 | Die Oberfläche nannte auch eine vorsorgliche Pause nach unklarer Bewässerung „Wartezeit nach Bewässerung“. | Backend liefert den tatsächlichen Grund; Anzeige „Rasenpause zur Sicherheit bis …“ / „Letzte Bewässerung unklar. Bitte den Platz prüfen.“ | Bekannte Bewässerung, vorsorgliche Pause und kurze Datenprüfung sind getrennt. Genaue Endzeit; unbekannter Start bleibt offen. Geprüfte Darstellung auf mobilen Breiten. |
| M1 | Eine dauerhaft gespeicherte manuelle START-Sitzung mit Status ENDED führte vor den normalen Sicherheits- und Startprüfungen zum vorzeitigen Rücksprung. Der Export zeigt diese Entscheidung durchgehend, auch beim Mähen und nach dem Laden. | Beendete Freigabe gewährt keine Ausnahme mehr und lässt die normalen Prüfungen wieder laufen. Unmittelbare Ladeheimfahrt wird nicht umgekehrt; ein späterer automatischer Start erhält wieder seine normalen Rückkehrregeln. | Nach Laden ist Wiederanlauf unter allen regulären Bedingungen möglich. Platzbelegung und Wasser können wieder Schutzparken auslösen. Tests über Neustart, Ladefahrt, Laden, weiteren automatischen Zyklus und Wasser-/Trainingskonflikte. |

Die Daten zeigen am Vormittag den Wechsel der manuellen Sitzung auf ENDED. Ob
diesen ersten Wechsel eine Nutzerfreigabe oder eine zwischenzeitliche
Gerätemeldung auslöste, ist damit nicht bewiesen. Der dauerhafte Rücksprung danach
ist im Code und in den Zyklen eindeutig. Keine Umdeutung des früher gemeldeten
Zusammentreffens von Mähen und Bewässerung zu einer bereits vollständig
rekonstruierten Ursache.

Eine einzelne fehlende Abfrage am Vormittag und um 13:49 UTC ließ die bestehende
physische Frist bereits unverändert. Deshalb wird nicht behauptet, jeder kurze
Ausfall habe immer 150 Minuten verursacht. Ebenso sind 150 Minuten theoretisch
vermiedene Sperre im Grenzfall keine 150 zusätzlich belegten Mähminuten:
Training, Ladezeit und andere Sperren sind abzuziehen.

## Berechtigung und tatsächliche Appack-Integration

Primärquellen, geprüft am 10.09.2026:
[Appack API-Schema](https://developer.appack.de/api/index.html),
[Appack API-Leitfaden](https://developer.appack.de/dev-guide/1/appack-api.pdf),
[offizielles Graph-API-Modul](https://cdn.appack.de/modules/graph-api.js).
Das offizielle Modul liest das persönliche JWT aus dem Cookie `jwt` oder dem
URL-Parameter `jwt`. SSV53s tatsächliche CMS-Rollenkonfiguration verwendet
**TR** für Trainer/Mitarbeiter und **AA** für App-Administratoren. Ein allgemeines
Rollenbeispiel aus einer Dokumentation ersetzt diese Konfiguration nicht.

Eine lesende Prüfung mit der sichtbar angemeldeten CMS-Identität gegen das echte
`whoami` bestätigte Appscope `ssv53`, eine aktive SSV53-Identität und die Rollen
AA/M/PW/VS/TR. Das ist ein realer Anbieterbeleg, aber **kein Test mit einem reinen
Trainerkonto in der installierten Handy-App**. Letzterer bleibt als praktische
Nachkontrolle offen. Erfundene oder echte Trainings wurden nicht zu Testzwecken
in der Produktion verändert.

Alle bestätigten Trainer können gemeinsame Serientrainings und bereits verlegte
Serientrainings bearbeiten. Separat angelegte Einzelbelegungen behalten die
bisherige Ersteller-/Administrator-Regel. Spiele und verbindliche Platzsperren
werden durch die neue Traineranmeldung nicht veränderbar. Die technische
Platzwart-Anmeldung bleibt auf ihrem bisherigen Weg möglich.

Appack-Abfragen gehen nur an einen festen HTTPS-Endpunkt, ohne Weiterleitung,
mit begrenzter Antwortgröße und Zeitlimit. Eine fehlerhafte Prüfung erlaubt
keine Änderung. Rollen werden bei jedem Schreibaufruf neu geprüft; beantragte
Rollen und frei übermittelte Profile werden nicht als Rechte akzeptiert.

## Risiko und Rückfall

| Risiko | Eintritt / Ausmaß | Erkennung und Prävention | Wiederherstellung / Restrisiko |
|---|---|---|---|
| Bewässerung während längerer Datenlücke | Möglich / erheblicher Anlagenkonflikt | Lücke über 180 s oder bekannter Bewässerungstermin innerhalb der Lücke bleibt volle Pause; aktuelle Daten separat erforderlich. | Daten wiederherstellen, Frist abwarten oder ausdrücklich bestätigte manuelle Ausnahme nach Prüfung. Nicht beobachtete Fremdeingriffe lassen sich ohne unabhängige Sensorik nicht ausschließen. |
| ENDED-Sitzung umgeht Schutzprüfung | Vorher belegt / erheblich | Frühe Rücksprünge entfernt; Tests für neue Belegung und aktive/unklare Bewässerung. | Bei Regression automatische Starts aussetzen, Schutzparken und Sperren erhalten; Geräte und offene Befehle vor Ort abgleichen. Altes fehlerhaftes Backend nicht blind zurückspielen. |
| Appack nicht erreichbar oder Anmeldung abgelaufen | Möglich / Terminänderung vorübergehend nicht möglich | Keine Freigabe aus fehlenden Daten, verständliche erneute Anmeldung bzw. Wiederholung. Bestehende Belegungen bleiben erhalten. | Kalender in angemeldeter App neu öffnen; bei Anbieterausfall vorhandenen Platzwartweg verwenden. |
| Missbrauch des offenen Anmeldeprüfwegs | Möglich / Verfügbarkeit und API-Kosten | Pro Worker höchstens drei laufende Anbieterabfragen und 30 je Minute; keine Warteschlange neben der Steuerung. | 429 fordert kurze Wiederholung. Gemeinsames Limit kann durch gezielte Anfragen ausgeschöpft werden; Schutz gegen verteilte Angriffe ist ohne vorgeschaltete vertrauenswürdige Drosselung nicht bewiesen. Bestehende Plätze werden dadurch nicht freigegeben. Infrastrukturänderung zurückgestellt, bei Missbrauch Zugangsbegrenzung separat einführen. |
| Veraltete App-Seite nach Veröffentlichung | Möglich / widersprüchliche Anzeige | Beide Vorlagen zusammen mit ihrer Textkopie prüfen; CMS nach Speichern neu öffnen und Inhalt vergleichen. | Seite in der App neu öffnen; Kalender-UI kann separat zurückgenommen werden, Backend-Prüfung bleibt. |
| Falsche Aussage „alles live getestet“ | Hoch ohne Trennung / falsches Sicherheitsvertrauen | Commit, Paket, installierte Dateien, Hostzustand, CMS-Inhalt und Gerätezyklen getrennt dokumentieren. | Nur beobachtete Ereignisse als Livebeleg werten; vollständiger Gerätezyklus und reines Trainerkonto bleiben bis zur tatsächlichen Beobachtung offen. |

Das Aufruflimit ist eine bewusste Begrenzung des Schadensumfangs, kein
vollständiger Schutz gegen einen Dienstverfügbarkeitsangriff. Es kostet keine
zusätzliche Infrastruktur. Die unabhängige Prüfung hat diesen verbleibenden
Punkt ausdrücklich benannt.

## Gestufte Lieferung und Abnahme

1. Lokale Tests: reale gespeicherte Zustandsfolge mit steuerbarer Zeit
   nachstellen; Netzwerk in allen Python-Tests standardmäßig verboten.
   Rollenentzug, falsche App, Ablauf, manipulierte Identität, doppelte IDs,
   Trainingspuffer und Wiederholung testen. HTML-/TXT-Kopien und mobile Ansicht
   abgleichen.
2. Geprüften Commit per PR nach `feature/azure-mower-migration` integrieren;
   unmittelbar davor SHA, Basis und erfolgreiche Prüfungen prüfen.
3. Aus genau diesen Git-Dateien Paket bauen und offline importieren. Erst dann
   vorhandene Function App aktualisieren. Keine Änderung der Live-Flags,
   Bewässerungszeiten, Akkuschwellen oder Sicherheitsfristen.
4. Installierte Quelldateien samt Manifest zurücklesen, Host und 15 Funktionen
   prüfen; frischer Steuerungszyklus muss das neue Manifest melden.
5. Kalender und Platzpflege in Appack speichern und nach erneutem Öffnen mit
   den geprüften Vorlagen vergleichen. Erst danach als veröffentlicht melden.
6. Nachkontrolle: mindestens drei frische Zyklen auf dem neuen Manifest sowie
   nächster tatsächlich auftretender Start/Ladevorgang und eine echte
   Traineränderung. Kein künstlicher Gerätebefehl und kein verschobenes echtes
   Training allein für einen Test. Alarmzeichen sind wiederholter ENDED-Halt,
   nicht erklärbare Freigabe, veraltete Zustände oder neue 401/403 bei aktiven TR.

Die bereits erteilte Freigabe gilt für die Korrektur im laufenden System. Eine
noch aktive vorsorgliche Rasenpause wird beim Rollout nicht von Hand entfernt.
Ein Rückfall muss Geräte, ausstehende Befehle und deren eigene Zeitpläne
berücksichtigen; ein Git-Rücksprung allein ist nicht ausreichend.

## Prüfprotokoll und Veröffentlichung

- Vollständige [Python-Suite](trainer-dashboard-tests-python.txt): **1.359 Tests
  und 479 Subtests bestanden**, Netzwerk gesperrt. Ein erster Gesamtlauf wurde
  wegen unzugänglicher alter temporärer Verzeichnisse abgebrochen. Der endgültige
  Lauf verwendet ein neues Temp-Verzeichnis im Worktree und keinen pytest-Cache.
- Vollständige [Appack-Suite](trainer-dashboard-tests-appack.txt): **137 Tests
  bestanden**, darunter die echten Rendering-Funktionen für Sperrgrund und Uhrzeit.
- Unabhängiger Backend-Nachcheck mit Sol: **117 Tests und 35 Subtests**;
  ursprünglicher Turnaround-Befund behoben, keine verbleibenden konkreten
  Sicherheitsbefunde in den geprüften Änderungen. Das Anbieter-Verfügbarkeitsrisiko
  bleibt wie oben dokumentiert. Luna prüft die mobilen Ansichten separat.
- Paket- und Veröffentlichungsnachweise werden nach dem tatsächlichen Rollout
  ergänzt. Aktuell: **umgesetzt und lokal geprüft, Veröffentlichung ausstehend**.
