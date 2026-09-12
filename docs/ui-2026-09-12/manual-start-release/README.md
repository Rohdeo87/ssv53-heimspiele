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
sechs produktiven Belegen muss nach Installation über die jetzt ergänzte
ADMIN-Diagnose bestätigt werden; die Zahl allein beweist deren Typ nicht.

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
  zwei weitere gezielte Fälle ergänzt, alle 27 neuen Fälle grün.
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
unbenutzt. Installations- und Veröffentlichungsbelege werden nach Ausführung
separat ergänzt; dieser Entwicklungsnachweis behauptet keinen Geräteerfolg.
