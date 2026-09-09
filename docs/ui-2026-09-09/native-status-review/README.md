# Korrektur nach der tatsächlichen Appansicht

Status: Entwicklung und gezielte Prüfung; Veröffentlichung dieser Korrektur noch
nicht nachgewiesen. Der [vorherige Einführungsstand](../../audit-2026-09-09/live-introduction-progress.md)
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

Quellcommit, PR, Paket-/Installationsnachweis und CMS-Speichernachweis werden
nach ihrem tatsächlichen Abschluss ergänzt.
Ein erfolgreiches Testbild ersetzt weder native Bedienprüfung noch
Geräteabnahme. Die erteilte Freigabe für die betreute Einführung gilt weiter;
Geräteschreibrechte werden für diese Anzeigenkorrektur nicht geöffnet.
