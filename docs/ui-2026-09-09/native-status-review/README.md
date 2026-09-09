# Korrektur nach der tatsächlichen Appansicht

Status: Korrektur entwickelt, getestet, gemergt, in Azure installiert und in
Appack gespeichert und erneut ausgelesen. Gerätebetrieb weiterhin DRY_RUN;
native Nachkontrolle nach dieser Veröffentlichung noch offen. Der [vorherige Einführungsstand](../../audit-2026-09-09/live-introduction-progress.md)
bleibt als zeitgebundener Nachweis erhalten.

## Belegter Fehler

Das Nutzerfoto zeigt die angemeldete SSV53-Platzpflege am 09.09. mit Telefonzeit
20:50. Damit ist die sichtbare Seite auf einem echten Appgerät nachgewiesen,
nicht ihre vollständige funktionale Abnahme. Winter-Schalter, untere Ansichten,
Sitzungsablauf und Gerätewirkung sind im Foto nicht sichtbar.

[Unverändertes Nutzerfoto](native-before.jpg), [Abgleich mit den Timerzyklen](photo-time-evidence.json).

Die Geräteabfrage um 20:50:02 enthielt die letzte Herstellermeldung von
20:45:43.897: Station, `RESTRICTED`, `FORCE_PARK`/`PARK_OVERRIDE`, verbunden,
100 Prozent. Die Meldung war rund 259 Sekunden alt. Der Grenzwert von
180 Sekunden wurde nicht verändert; eine neue GET-Antwort aktualisiert den
Herstellerzeitstempel nicht automatisch. Um 20:51 und 20:52 kamen neue
Herstellerzeitstempel an. Die Daten waren somit zeitweise veraltet, nicht
vollständig verschwunden.

| Problem | Tatsächliche Ursache | Korrektur |
|---|---|---|
| Oben „Mäherstatus fehlt“, unten „Verbunden“, 100 % und 43 % ohne Einschränkung | Der Warnbereich prüfte das Alter; die Detailkarte verwendete ungekennzeichnet dieselben alten Rohwerte. | Gemeinsame Altersangabe und Kennzeichnung als letzter Stand; keine erfundene aktuelle Verbindung. |
| „Zurzeit nicht im Mähbetrieb“ statt konkretem Stationszustand | Der Anzeigeadapter verdichtete RESTRICTED vor der bekannten PARKED_IN_CS-Aktivität; das Frontend bevorzugte wiederum den generischen Anzeigecode. | Konkrete Station-/Ladeaktivität erhalten, Fehler und manuelle Stopps behalten Vorrang. |
| Roter Startknopf trotz geschlossenem Gerätebetrieb | Der Lesestatus signalisierte verfügbare Daten, aber keine gesonderte Gerätebedienfreigabe; Frontend prüfte außerdem den Telemetrieblocker für diesen Knopf nicht. | Eigene serverseitige Bedienfähigkeit und konservative Frontendprüfung, ohne Datenanzeigen oder Winter-Control daran zu koppeln. |
| Wartezeit „22:41“ bei tatsächlichem Ende 22:41:51.487 | Minutenformatierung schnitt Sekunden ab. | Nur das Ende der Sicherheitswartezeit zur nächsten vollen Minute aufrunden: 22:42. Exakte Termine nicht global verändern. |

Im beobachteten Produktionsstand waren alle Gerätegates geschlossen;
die tatsächliche Startanfrage wäre serverseitig mit `AUTOMATION_LOCKED`
abgewiesen worden. Es wurde kein Knopf betätigt und kein Gerätebefehl gesendet.
Die Prüfung fand zusätzlich eine Abweichung zwischen Betriebsart und
Annahme von Bedienanfragen: Die bisherigen Schreibflags allein prüften nicht,
ob FULL_FAILSAFE tatsächlich aktiv war. Auch diese Annahmegrenze wird mit der
ausgegebenen Bedienfähigkeit vereinheitlicht, damit im Vergleichsbetrieb keine
später wirksame Geräteanfrage vorgemerkt wird.

## Umfang und Risiken

Diese Korrektur betrifft Statusadapter, Annahmegrenze für Gerätebedienung,
Anzeige und Regressionen. Timer, Belegungsregeln, Bewässerungsbedarf,
Herstellerabfrageintervall und Trocknungsfristen bleiben fachlich unverändert.

Neue beziehungsweise fehlende Vertragsfelder sperren Gerätebedienung
konservativ. Der Winter-Schalter bleibt an seinen eigenen aktiven Kalender
gebunden. Stoppmöglichkeiten dürfen bei offenem Gerätegate nicht allein wegen
alter Mäherdaten verschwinden. Ein UI-Knopf ist weiterhin keine Startfreigabe.

Die Foto-Nachstellung verwendet echte Timerfelder und ausdrücklich ergänzte
Ansichtszustände; sie ist keine heimlich ausgelesene Benutzersitzung und keine
tatsächliche Antwort des geschützten Statusendpunkts. Die native Nachkontrolle
nach Veröffentlichung bleibt gesondert erforderlich.

## Prüfung und Auslieferung

Lokale Prüfung: 1164 Python-Tests und 445 Untertests bestanden; 112
Appack-JavaScript-Tests bestanden. Der unabhängige Terra-Review fand zusätzlich
einen erneut aktivierten Plan-Dialog; nach Korrektur wurden die tatsächlich
gerenderten gesperrten Knöpfe geprüft.

[Prüfprotokoll](development-verification.json), [Mobilansicht](mobile-after-simulation.png),
[Zeitangaben und Mäherkarte](mobile-times-simulation.png),
[isolierte Vorschau](preview/appack-preview-mobile.html). Die Vorschau nutzt eine
390-Pixel-Inhaltsbreite, ersetzt das Logo und blockiert das Netzwerk per CSP;
jeder simulierte POST wird abgewiesen. Reine DOM-Nachweise liegen daneben.

Quellstand: [d2e09c1](https://github.com/Rohdeo87/ssv53-heimspiele/commit/d2e09c1ab023a136fc4ca90ad22d2308041fe14a),
[PR 51](https://github.com/Rohdeo87/ssv53-heimspiele/pull/51),
Merge [c3b59dd](https://github.com/Rohdeo87/ssv53-heimspiele/commit/c3b59dd0802e5bd1eb3ba15b45d5656f5a60ce82).
Beide GitHub-Prüfungen waren vor dem Merge grün; Head, Basis und Mergefähigkeit
wurden unmittelbar geprüft: [CI-/PR-Nachweis](pr-ci-verification.json).
Ein erfolgreiches Testbild ersetzt weder native Bedienprüfung noch
Geräteabnahme. Die erteilte Freigabe für die betreute Einführung gilt weiter;
Geräteschreibrechte werden für diese Anzeigenkorrektur nicht geöffnet.


## Tatsächliche Veröffentlichung am 09.09.2026

- Das [CI-Paket](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34394181513) wurde vollständig gegen den kanonischen Git-Quellstand geprüft. Alle 61 Quellen sind enthalten; gegenüber der zuvor installierten Version änderte sich nur platzwart_console.py. [Paketnachweis](ci-package-verification.json).
- Paket-SHA256: `9379280242f2237b97a34297b9a0b88d5b71bf3ae9834c4eb43647c3f3269fca`. Manifest-SHA256: `fc9cec4b2d66fe9c39685023a5a17bdf7e9bbf4b3a6e4329a0b48ce1fee1b43f`.
- Azure CLI beendete den Remote-Build erfolgreich um 21:23:17 Uhr. Danach wurden alle 61 installierten Quelldateien bytegenau gegen das CI-Paket gelesen; das Manifest blieb währenddessen identisch. [Installationsnachweis](installed-source-verification.json). Dependency-Wheels sind dadurch nicht bytegeprüft.
- Die Azure-Deploymentmetadaten nennen `7b6e4542-b627-4001-9a8c-eaf7d0c29e46`, Status 4, aktiv und vollständig. Azure hängte allerdings eine HTML-Fehlerseite an die JSON-Metadaten an. Dieser formal fehlerhafte Metadatenabruf ist nur ein ergänzender Nachweis; CLI-Ergebnis, tatsächliche Quelldateien und laufende Timerzyklen belegen die Installation unabhängig. [Einschränkung und Zeitstempel](active-deployment-verification.json). Es wurden dafür keine Zugriffsrechte erweitert; der gesperrte Zugriff auf das allgemeine Dateisystem wurde nicht geöffnet. Gelesen wurden die Quellen unter dem von Azure ausgewiesenen scriptHref.
- Die realen Zyklen um 21:23–21:26 Uhr melden das neue Manifest, jeweils 61 geprüfte Quellen, DRY_RUN, keinen Gerätebefehl und weiterhin PARKED_IN_CS. Das bestehende Warteende blieb 22:41:51.487 Uhr; dieser Rollout löste keine zusätzliche volle Wartezeit aus. Das Ende bezeichnet eine Freigabeschranke, keinen zugesagten Mähstart und keinen Nachweis einer tatsächlich erfolgten Bewässerung. [Zyklen](post-deployment-cycles-summary.json).
- Sämtliche App-Einstellungen blieben unverändert, insbesondere geschlossene Gerätegates, DRY_RUN und SHADOW. [Einstellungsnachweis](post-deployment-settings-verification.json). Der Timer wurde nicht absichtlich unterbrochen; eine unterbrechungsfreie Herstellerkommunikation ist damit nicht zugesichert.
- Platzpflege.tpl wurde um 21:27 Uhr in Appack gespeichert. Nach vollständigem Neuladen und erneutem Öffnen des Editors stimmt der normalisierte Quelltext exakt mit dem getesteten Template überein: `8e10db32cf6575a63cab02157f78bd96ca8e3b93852342cdba8dfd5e9305fc61`. Die Datenquelle Belegungsplan_db und die Verwendung in Platzpflege blieben zugeordnet. [CMS-Nachweis](appack-publication-verification.json).

## Verbleibende Prüfung und Rückfall

Die neue Anzeige muss auf dem bereits angemeldeten echten Appgerät nach dem
Schließen und erneuten Öffnen der Platzpflegeseite nachkontrolliert werden.
Das Browsergerät besitzt keine native Geräteaktivierung; deshalb wurde weder
ein Benutzer nachgeahmt noch ein Aktivierungscode erzeugt. Die authentifizierte
Statusantwort auf diesem Appgerät nach Veröffentlichung ist noch nicht
unabhängig ausgelesen. Das Foto belegt nur den vorher sichtbaren oberen Bereich.

Die bisherigen Einführungsgrenzen gelten weiter: Keine Gerätebefehle in diesem
Änderungsschritt, kein Nachweis eines vollständig freigegebenen Automatikbetriebs.
Ein betreuter Pilot braucht den bestätigten Abgleich von Geräteprogrammen und
Übernahmezustand sowie die bereits dokumentierten Freigabekriterien. Die
vorliegende Anzeigenkorrektur ersetzt diesen Abgleich nicht.

Rückfall für diese begrenzte Änderung: Gerätegates geschlossen lassen; die
vorherige gesicherte CMS-Vorlage zurückspielen und bei Bedarf das zuvor
installierte 61-Dateien-Paket mit SHA256
`570858f6da7f85ddbb4182fd4539f534ca5225d2c47dc1a03a1ebdf66535350f`
per Remote-Build installieren. Keine Rücksetzung persistenter Bewässerungs- oder
Stoppsperren. Jeder erneute Hostwechsel kann eine Bestätigungslücke verursachen;
eine daraus resultierende Sperre darf nicht ohne belegte Voraussetzungen
entfernt werden. In diesem Rollout wurden keine neuen Geräteaktionen erzeugt,
keine Geräteprogramme geändert und keine kostenpflichtigen Ressourcen angelegt.
