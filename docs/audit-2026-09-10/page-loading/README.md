# Platzpflege: Wartezeit beim Öffnen verkürzen

Stand: Entwicklung und isolierte Prüfung am 10.09.2026. Veröffentlichung und
Installation werden unten erst nach deren tatsächlicher Prüfung ergänzt.

Die bisherige Statusantwort wartet neben dem aktuellen Geräte- und Belegungsstand
auch auf zwei Wochenstatistiken und Vereinsheimtermine. Beim Ablauf der fünfminütigen
Zwischenspeicherung können mehrere entfernte Abfragen nacheinander laufen.
Der Client wartet auf die gesamte Antwort, bevor er eine Zustandskarte zeigt.

Die [Produktionsmessung vor der Änderung](before-performance.json) umfasst die
letzten sechs Stunden, Abruf am **10.09.2026, 22:13:08 Uhr Berlin**:

| Messgröße | Nachweis |
|---|---:|
| Erfolgreiche Statusantworten (HTTP 200) | 60 |
| Median | 2.597 ms |
| 95. Perzentil | 15.214 ms |
| Maximum | 17.872 ms |
| Login-Median | 242 ms, 7 Antworten |
| Abgewiesene Statusanfragen (HTTP 401, getrennt) | 28 |

Einzelne Abhängigkeiten wurden in Application Insights nicht mitgemessen. Die
Messung beweist die Verzögerung der gesamten Antwort; der Quellcode beweist die
serielle Abhängigkeit von optionalen Daten. Eine gemessene Einzelursache für jede
langsame Produktionsantwort oder ein bestimmter Mobilfunkanteil wird nicht behauptet.

## Umsetzung und Wirkung

| Änderung | Wirkung | Grenze / Absicherung |
|---|---|---|
| `GET /platzwart/status?view=live` | Gerät, Wasser, Belegung und Bedienregeln werden ohne neue Statistik-/Vereinsheimabfragen geliefert. | Gleiche Sitzungsprüfung, echte aktuelle Sicherheitslesekette und `Cache-Control: no-store`. |
| Nicht wartender Zugriff auf vorhandene Zusatzdaten-Caches | Bereits vorhandene Wochenstatistiken und die Ladeprognose bleiben unmittelbar verfügbar. | Kein Warten hinter einer Statistikabfrage; nach Ablauf unbekannt/nachladen. Keine neuen Fristen. |
| Zusatzdaten getrennt nachladen | Die erste Anzeige und das Ende des Aktualisieren-Spinners warten nur auf den aktuellen Status. | Der vollständige alte Endpunkt bleibt kompatibel. Im Hintergrund entstehen nur bei fehlenden/abgelaufenen Zusatzdaten zusätzliche vollständige Leseanfragen. |
| Begrenzte Übernahme später Antworten | Statistik und Vereinsheim können nach der Zustandskarte erscheinen. | Keine Übernahme von Bedienrechten, Belegung, Sperren, Aktionen oder Gerätezuständen. Aktuelle Gerätewerte bleiben erhalten. |
| Ladeende ergänzen | Auch ein erster kalter Aufruf kann nachträglich eine belastbare Schätzung zeigen. | Nur identische frische Lademeldung, Akkustand und Zeitstempel; höchstens 30 Sekunden Abstand. Anzeigeprüfungen bleiben wirksam. |
| Laufende Aktualisierung gemeinsam abwarten | Doppelte Aufrufe erzeugen keine doppelten Statusanfragen und liefern keine alte Scheinantwort. | Aktionsbestätigung prüft weiterhin die passende Anfragekennung. |
| Verdeckte Seite nicht regelmäßig abfragen | Weniger unnötige Abfragen und Mobilfunkverkehr. | Beim Zurückkehren wird unmittelbar aktualisiert; bestehende Aktionsnachverfolgung bleibt aktiv. |
| Begrenzte Wiederholung, getrennte Fehler | Statistikfehler lassen den aktuellen Zustand sichtbar. | 60 Sekunden Abstand nach Zusatzdatenfehler; 401 beendet die Anmeldung sofort. |
| Laufzeitmessung je Antwortvariante | Langsame aktuelle Statusdaten lassen sich von langsamen Zusatzdaten unterscheiden. | Nur Variante/Dauer im Log; keine Schlüssel, Nutzer, URLs oder Gerätepositionen. |

Keine Änderungen an Bewässerungszeiten, Trockenfrist, Trainings-/Spielpuffern,
Stoppsperren, Berechtigungen oder Gerätebefehlen. Die fünfminütigen serverseitigen
Zusatzdaten-Caches erhalten keine zusätzliche fünfminütige Browser-Cachefrist.
Eine fehlende Vereinsheimquelle ist ein abgeschlossener Zustand und löst keine
endlose Nachladefolge aus. Beschädigte Cache-Strukturen oder -Inhalte werden
verworfen; sie dürfen keine gültige aktuelle Statusantwort zerstören.

Ein kalter Aufruf hat bewusst einen zusätzlichen vollständigen Leseaufruf. Die
Sicherheitsdaten darin werden im Browser nicht übernommen. Das kann zusätzliche
Lesezugriffe erzeugen; eine gemessene Cloudkostenersparnis wird nicht behauptet.
Die Einsparung bei Hintergrundabfragen und ihr konkreter Kostenanteil hängen von
der tatsächlichen App-Nutzung ab. Eine Neuordnung von Geräteabfragen, längere
Telemetriefristen und CORS-Änderungen sind nicht Bestandteil dieser Korrektur.

## Tests und Review

- Vollständige Python- und Appack-Tests: Ergebnisse in den beigefügten Testdateien.
- Unveränderte Sicherheitsantworten bei frischem und 181 Sekunden altem Wasserstand;
  keine Vendor-Fallbackabfrage, keine Zustandsänderung durch Seitenabruf.
- Zwischenspeicher gesperrt, abgelaufen, Zeitrücksprung, falsches Konto, beschädigte
  Struktur und beschädigte innere Statistikwerte; Reparatur ohne Blockierung.
- Gleiche neue Antwort für parallele Aufrufer; langsames Nachladen, Zeitüberschreitung,
  Sitzungswechsel, 401, Hauptabfragefehler und verborgene Seite.
- Neue Sperren, aktueller Akkustand, Klingenlaufzeit, manuelle Sperre und neuere
  Gerätewerte bleiben bei späteren Zusatzdaten unverändert.
- Bestehende Ladeende-Tests prüfen nun auch den schnellen Aufruf gegen neue
  Akkumeldungen und eine unerwartete Batterieentwicklung.
- Luna untersuchte die Engpässe; Sol prüfte die Änderung unabhängig. Die Befunde
  zu fehlender Vereinsheimkonfiguration, 401 und beschädigten optionalen Caches
  sind korrigiert und mit Regressionstests abgesichert.

Der [Browservergleich](../../ui-2026-09-10/page-loading/browser-check.json)
verwendet dieselben synthetischen Antwortzeiten für Vorher/Nachher: **200 ms für
aktuelle Daten und 2.500 ms zusätzlich für Zusatzdaten**. In 320 und 390 Pixel
Breite erscheint die Zustandskarte nach etwa **0,3 statt 2,8 Sekunden**.
Die [erste Anzeige](../../ui-2026-09-10/page-loading/first-status-390.png)
zeigt bereits Belegung, geplante Startzeit und verfügbares Parken. Ausstehende
Statistiken heißen „Die Statistik wird geladen.“ Es gibt keine Überbreite oder
JavaScript-Fehler. Alle Netz- und Gerätezugriffe im Browsertest sind gesperrt.
Das ist ein reproduzierbarer Mechanismusnachweis, **keine Live-Ladezeitgarantie**.

## Einführung, Messung und Rückfall

Zuerst wird das geprüfte Backendpaket installiert und jedes installierte Paketfile
verglichen; Schutzschalter und 15 registrierte Funktionen müssen erhalten bleiben.
Erst danach wird die bereits gesicherte CMS-Vorlage `Platzpflege.tpl` ersetzt,
gespeichert und nach Neuladen vollständig mit dem Quelltext verglichen. Der
unveränderte alte `/status`-Aufruf erlaubt ältere installierte App-Vorlagen.

Vor der Einführung war das Paketmanifest
`8b17b7d232de8a32593c0c2aee55b3cd8527d29338c2be9626199ef72fac3d77`
installiert. Die vorherige CMS-Fassung hat nach LF-Normalisierung den SHA256
`bf0e8a755400df23a19a5b280e16db04e575c2938318d3cad06f061e2d630998`.
Beides wurde aktuell gelesen. Die Vorlage ist lokal in
`dist/page-loading-cms-before.tpl` gesichert; das vorherige Backendpaket liegt als
`dist/trainer-dashboard-full-failsafe.zip` vor.

`scripts/read_page_performance.py --minutes 60 --output dist/page-latency.json`
liefert HTTP-Status, Anzahl und Perzentile getrennt nach schneller und vollständiger
Antwort sowie die neu hinzugefügten Serverzeiten. Kein Gerätetest wird ausgelöst.
Für einen belastbaren Livevergleich sind mindestens 30 erfolgreiche Aufrufe und
drei Cacheabläufe sinnvoll. Bisherige sechs Stunden und eine kurze Einführungsprobe
dürfen nicht als identische Lastbedingungen ausgegeben werden.

Abbruch bei geänderten Schutzschaltern, fehlenden Funktionen, abweichenden
Paketbytes, wiederholt fehlendem aktuellen Status oder wiederhergestellten alten
Bedienfreigaben. Zuerst kann die CMS-Vorlage zurückgesetzt werden; der bisherige
Endpunkt funktioniert weiterhin. Falls das Backend zurückgesetzt werden muss,
vorherige geprüfte Paketbytes installieren und den laufenden Controller samt
persistierten Anfragen erneut prüfen. Ein Neustart kann die Beobachtungskette
unterbrechen. Bestehende Trocken-/Stoppsperren und unbestätigte Befehle dürfen
deshalb keinesfalls manuell gelöscht oder als erledigt angenommen werden.

Die rein lesende Vorprüfung sah bereits `MOWER_START_OUTCOME_UNCONFIRMED`, einen
als geparkt gemeldeten Mäher und keinen aktiven Bewässerungskreis. Dieser bestehende
Gerätevorgang wird durch die Ladezeitänderung weder aufgelöst noch neu angestoßen.
Die tatsächliche Geräteausführung ist kein Abnahmekriterium der Anzeigeoptimierung.

Offen bleibt die unabhängige Beobachtung im nativen Appgerät nach Veröffentlichung.
Der hier angemeldete CMS-Browser zeigt in der Vorschau die Geräteaktivierung;
eine zusätzliche Gerätesitzung wird zur Messung nicht angelegt.
