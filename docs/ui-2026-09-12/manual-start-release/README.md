# Manueller Start bei bestätigtem Parken – 12. September 2026

## Ausgangslage und Nachweis

Um 09:02 zeigte die App eine ältere Mähermeldung und keinen Startknopf.
Die [Steuerungsdaten](../start-release-before.json) bestätigen HOME/geparkt,
Verbindung, fehlerfreien Mäher und frische inaktive Wasserzonen. Die reale
Trockenfrist endete bereits am Vortag; die zusätzliche Datenbestätigung ist
ebenfalls abgeschlossen. Eine neue Trockenzeit wurde nicht gesetzt.

Der bestehende, ausschließlich lesend aufgerufene ADMIN-Prüfendpunkt meldet
[sechs ungelöste Geräteaufträge](existing-admin-preview.json). Es gibt keinen
offenen Mäherstart-Latch. Deshalb passt die frühere Reparatur für einen
ungesendeten Start hier **nicht**; ihr schreibender Endpunkt wurde nicht benutzt.
Direkter Tabellenzugriff wurde zuvor verweigert. Keine Rechteerweiterung.

Die [Gerätebefehlsprotokolle](device-action-traces.json) enthalten sieben
Aussetzbefehle für Hydrawise zwischen 03:46 und 04:11 Uhr Berlin, jeweils bis
08:10 Uhr. Keine zentrale Wasserstartaktion im abgefragten Zeitraum seit
11.09., 14:00 Uhr. Der generische Abgleich verlangt bisher `scheduled=false`;
der weitergehende Abgleich bindet an die **aktuelle** Plan-ID. Frühere
Planänderungen lassen damit alte Aussetzbelege offen, obwohl Hydrawise bereits
den nächsten Lauf nach der Aussetzfrist meldet. Der Zusammenhang zu exakt den
sechs produktiven Belegen wurde nach Installation durch die ergänzte
ADMIN-Diagnose bestätigt: Alle sechs waren alte Aussetzbelege und wurden am
12.09. um 10:20:10 Uhr Berlin anhand neuer Hydrawise-Daten abgeschlossen.

## Änderungen und Grenzen

1. Aussetzbelege werden gegen **ihre eigene unveränderliche Frist** aus dem
   persistierten Sendeauftrag abgeglichen, unabhängig von späteren Plan-IDs.
   Erforderlich sind zurückgekehrter Transport (`SENT_UNCONFIRMED`), neuere
   vollständige Hydrawise-Daten, keine aktive Zone und ein gültiger nächster
   Start nach Frist **und** aktueller Zeit. `UNKNOWN`, fremde Befehlstypen,
   Teilantworten und unbekannte Zeiten bleiben gesperrt. Kein Journalreset.
2. Ein vom Server kontinuierlich bestätigter HOME-Parknachweis kann einmalig
   an einen ausdrücklich angeforderten manuellen App-Start übergeben werden.
   Die Übergabe gilt maximal 120 Sekunden und ist an dieselbe Anfrage,
   Sitzung und Generation gebunden. Sie wird weder verlängert noch als
   Bewässerungsfreigabe benutzt. Der letzte Versandcheck prüft sie nach OAuth
   erneut. Neustart verlängert nichts; Leseausfall, anderer Auftrag, Abfahrt,
   Gerätestörung und unbekannter Versand widerrufen sie. Automatische Starts
   behalten ihre bisherige Prüfung des Ereignisalters.
3. Der Startknopf richtet sich weiterhin nach der Backendfreigabe. Bei
   bestätigter Station ersetzt eine einfache Stationsmeldung die reine
   Alterswarnung; „In Station lassen“ ist die passende Parkbeschriftung.
   Kein erfundenes aktuelles Akku- oder Ladeende. Echte Wasser-, Trocken- und
   Belegungskonflikte bleiben sichtbar und bestätigenpflichtig.
4. Der vorhandene ADMIN-GET zeigt begrenzte Gerätebelege ohne Geheimnisse,
   Geräte-IDs oder vollständige Nutzlast. Ein Kontrollzyklus protokolliert,
   welche alten Arten/Zeitpunkte er anhand neuer Quelldaten abgeglichen hat.

Die Aussetzfrist ist laut [Hunter REST API v1.6, S. 5](https://www.hunterindustries.com/sites/default/files/2024-10/Hydrawise%20REST%20API%20Ver%201.6_0.pdf)
eine absolute Unix-Zeit; der Status enthält den nächsten programmierten Lauf.
Ein späterer Lauf am selben Tag ist deshalb ebenso zulässig wie am Folgetag.
Dies ist eine Schlussfolgerung aus der dokumentierten Schnittstelle. Eine
HTTP-Bestätigung allein ersetzt weiterhin keine spätere Zustandsbeobachtung.

## Tests und Review

- Vollständige Python-Suite: 1.551 Tests und 482 Teiltests grün; anschließend
  drei weitere gezielte Fälle ergänzt, alle 28 neuen Fälle grün. Der letzte
  Integrationstest durchläuft den Abgleich aller sieben alten Belege, dessen
  Persistenz, die manuelle Annahme und den einmaligen simulierten Start.
- Fälle: altes Dock-Ereignis ohne Wasser, manueller Start, Neustart vor Versand,
  kein Doppelstart, neue Parkanforderung, Datenfehler, unbekanntes/laufendes
  Wasser, Störung/Abfahrt, Fristablauf unmittelbar am Versandcheck,
  erhaltene Trainings-/Trockenbestätigung, deaktivierte Option, unbekannter
  Versand, keine Verlängerung durch neue Anfrage; sieben Aussetzbelege mit
  alten Plan-IDs und neun negative Varianten.
- 106 gezielte JavaScript-Tests; kanonische Designquelle und CMS-Kopie synchron.
- Unabhängiger gezielter Review mit günstigerem Modell: kein gefundener
  Wiedereinstieg nach Fehler/Parallelauftrag. Ein zunächst angemerkter
  Folgetagszwang wurde anhand der absoluten API-Frist als unnötig verworfen.
- Keine echten Gerätebefehle in Tests. Die aktuelle App-Sitzung verlangt im
  Vorschaufenster eine Geräteaktivierung; Nutzerbedienung auf einem realen
  Telefon ist damit nicht automatisch nachgewiesen.

## Einführung, Abnahme und Rückfall

Das Paket wird ausschließlich aus einem Commit über dem zuletzt installierten
71-Dateien-Paket gebaut. Nur sechs Backenddateien und der Manifestinhalt dürfen
sich ändern. Das Appack-Template wird gegen die gesicherte aktuelle CMS-Kopie
abgeglichen und gesondert veröffentlicht. Keine Schalteränderung, keine
Testbewässerung, kein erzwungener Mäherstart und keine Verlängerung des heutigen
Bewässerungszeitfensters sind Teil dieser Korrektur.

Abnahme: bytegenauer Installationsnachweis, unveränderte Schutzschalter,
laufender Host, protokollierter gezielter Belegabgleich und keine Wasser- oder
Mäheraktion außerhalb der bestehenden Freigaben. Der echte manuelle Start ist
erst nach Nutzerbedienung und beobachteter Abfahrt live nachgewiesen.

Rückfall: vorangehendes Paket `dist/irrigation-park-hold-release.zip` und gesicherte
CMS-Vorlage wiederherstellen. Ein eventuell bereits gesendeter Start wird dadurch
nicht rückgängig: erst Geräte-/Wasserzustand und offene Belege prüfen, erforderlichenfalls
Mäher parken und tatsächliche Station bestätigen. Alte Versionen akzeptieren
`START_READY` nicht als Freigabe. Der schreibende Alt-Recovery-Endpunkt bleibt
unbenutzt. Die folgenden Installations- und Veröffentlichungsbelege bestätigen
die Softwarebereitstellung und den Belegabgleich, keine physische Abfahrt.

## Bereitstellungsstand nach der Freigabeprüfung

- Implementierung: Commit `fecb803451efadaddbb701d01a228f8e41f3659b`.
- [PR #80](https://github.com/Rohdeo87/ssv53-heimspiele/pull/80), aufbauend auf
  dem bereits installierten vorherigen Reparaturbranch. Keine automatische Zusammenführung.
- [CI des exakten Implementierungscommits](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34680873647)
  erfolgreich. Spätere Änderungen betreffen nur ergänzende Tests/Belege.
- [Paketnachweis](package.json): ZIP `dist/manual-start-release.zip`, SHA-256
  `f16fd90869b4a79564dc6ab74ee5607554992eba35e592a5a660bb9eebb8a627`,
  Manifest `cc36ebb779787ee97d0c499da61410cf9810505fb384400e1379c3262760a89c`.
  71 Einträge; genau sechs geänderte Quelldateien plus Manifest;
  isolierter Import mit 16 Funktionen und verbotenem Netzwerk bestanden.
- [Unmittelbarer Versionsabgleich](installed-before.json): alle 71 Dateien
  des bestehenden Livepakets korrekt, Host Running, Schutzschalter unverändert.
- [Vorprüfung](pre-deployment/recent-cycles.json): um 09:28 Uhr weiterhin HOME,
  inaktive frische Wasserzonen, abgeschlossene Trockenfrist und gehaltener
  Parknachweis. Zu diesem Zeitpunkt ist das verbleibende automatische Mähfenster
  bereits zu kurz; die Belegung darf nicht pauschal als frei behandelt werden.
- **Appack veröffentlicht**: CMS meldet Speicherung 09:33 Uhr;
  [öffentliche Auslieferung](publication.json) um 09:35:58 Uhr aller sechs
  geänderten UI-Funktionen vollständig mit dem lokalen Stand abgeglichen.
  Die Backendfreigabe wird dabei nicht vorweggenommen.
- **Backend installiert und geprüft**: Nach der ausdrücklichen Bestätigung
  „Ja“ wurde genau das oben genannte ZIP mit `config-zip --build-remote true`
  installiert; der Befehl endete erfolgreich. Zuvor war derselbe Schritt wegen
  fehlender gesonderter Bestätigung von der automatischen Freigabeprüfung
  abgelehnt worden. Die erneute Ausführung erfolgte nach der Nutzerfreigabe.
- [Installationsabgleich](installed-approved.json) um 10:21:49 Uhr Berlin:
  alle 71 installierten Dateien stimmen mit dem freigegebenen Paket überein;
  Host Running, 16 Funktionen. Alle im Prüfskript erfassten Betriebs- und
  Schutzschalter sind unverändert. Keine Konfigurationsänderung durchgeführt.
- [Live-Nachweis](live-verification.json): sechs offene Geräteaufträge vor
  Installation, null danach. Die sechs alten Aussetzbelege wurden um 10:20:10
  Uhr Berlin einzeln durch neuere Hydrawise-Daten bestätigt. Kein Löschen oder
  pauschales Zurücksetzen des Journals. Die erneute lesende Abfrage um
  10:24:03 Uhr bestätigt weiterhin null offene Aufträge.
- Drei beobachtete Kontrollzyklen mit dem neuen Manifest (10:21–10:23 Uhr)
  melden HOME/geparkt, gehaltenen Stationsnachweis, alle sieben Wasserzonen
  frisch und inaktiv sowie eine erlaubte Freigabe nach abgelaufener Trockenfrist.
  In diesen Zyklen wurde kein Gerätebefehl gesendet. Die eingetragene
  Spielbelegung von 10:00 bis 13:45 Uhr bleibt wirksam.

Die konkrete Reparatur ist damit live. Die App wurde bereits veröffentlicht;
nach Aktualisieren verwendet sie die neue Backendfreigabe. Eine Bestätigung
für tatsächliche Platzbelegung bleibt beim manuellen Start gemäß vereinbarter
Regel notwendig; aktuell ist keine Trockenwarnung begründet. Ein echter
manueller Start samt beobachteter Abfahrt und ein kompletter Bewässerungslauf
wurden im Rahmen dieser Einführung nicht ausgeführt oder live nachgewiesen.
