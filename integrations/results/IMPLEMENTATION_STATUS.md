# Ergebnisintegration: Veröffentlichungsnachweis

Stand: 24.09.2026. Fortsetzung von PR #97 auf der bestehenden Implementierung.

## Getrennte Lieferzustände

| Bereich | Nachweis |
| --- | --- |
| Code | Blueprint, Cache/API/Timer, Parser, Appack-Vorlage und vollständige Paketierung implementiert und getestet. |
| Azure | Korrigiertes Gesamtpaket installiert: Host Running, 18 Funktionen, alle 81 Paketdateien bytegleich verifiziert. |
| Datenaktualisierung | Aktiviert, stündlich zur Minute 17, kein Startabruf. Gezielter Aufruf ausschließlich des Ergebnis-Timers in Azure erfolgreich; beide Caches aktualisiert. Ein späterer automatisch ausgelöster Stundenlauf wurde noch nicht beobachtet. |
| Appack | Bestehende Ergebnisse-/Tabellenseite gespeichert; öffentliche Auslieferung HTTP 200. JavaScript und Inline-Styles entsprechen der geprüften Vorlage. |
| Geräteprüfung | Mobile Chromium-Prüfungen und öffentliche Browseransicht geprüft. Echte Android-/iOS-App-WebViews noch nicht abgenommen. |

## Öffentliche Ansicht und Daten

- [Ergebnisse und Tabellen](https://appack.de/rest-api/drender/69328f3157d8e5e1cbb4ca25)
- [Handball-Cache](https://func-ssv53platzpflege-prod-q7kbw54s.azurewebsites.net/api/ssv-results?sport=handball)
- [Volleyball-Cache](https://func-ssv53platzpflege-prod-q7kbw54s.azurewebsites.net/api/ssv-results?sport=volleyball)

Erfolgreiche Aktualisierung in Azure am 24.09.2026:

- Handball: 18:21:09 UTC / 20:21:09 Europe/Berlin, sechs Mannschafts-/Wettbewerbseinträge.
- Volleyball: 18:21:32 UTC / 20:21:32 Europe/Berlin, eine Staffel, zehn SSV-Ansetzungen.
- Beide Antworten HTTP 200, stale=false; ETag/304, OPTIONS/204 und CORS für
  https://appack.de geprüft. Unzulässige Sportart ergibt HTTP 400.
- GET liest nur Cache. Fußball bleibt ausschließlich ein externer FUSSBALL.DE-Link.

## Quellen und Freigabe

Die tatsächlichen nuLiga-/HVB- und KSV-Quellen wurden begrenzt und robots-konform
geprüft, ohne Zugriffssperren zu umgehen. Saison 2026/27 und Mannschaftszuordnung
stammen aus den aktuellen Quellen. Keine Spielerlisten, privaten Kontaktdaten
oder Schiedsrichterberichte werden übernommen.

Der Auftraggeber hat am 24.09.2026 die telefonisch erhaltene Anbieterfreigabe
bestätigt und die Fortsetzung autorisiert. Dies bezieht sich auf die zuvor
abgefragte regelmäßige Ergebnisübernahme einschließlich der unveränderten
KSV-Tabellengrafik. Das ist eine Nutzerbestätigung, **kein hier vorliegender
schriftlicher Nachweis** und keine unabhängig bestätigte Lizenz.
Eine schriftliche Dokumentation durch den Verein bleibt empfohlen.

Handball: Männer, Frauen, männliche D, Männer-Pokal sowie Mini-Staffeltermine.
Die weibliche C wird als zurückgezogen gekennzeichnet. Mini-Termine behaupten
keine automatische SSV-Teilnahme. Fehlende Ergebnisse werden nicht als 0:0 ersetzt.
Die offizielle Reihenfolge der Handballtabelle bleibt erhalten.

Volleyball: Original-Tabellengrafik unverändert, keine OCR oder Neuberechnung.
Der veröffentlichte Tabellenstand 19.07.2026 ist vom technischen Abrufzeitpunkt
getrennt und in der Anzeige erkennbar.

## Schutz des bestehenden Betriebs und Paketierungsfehler

Vor Schreibzugriffen waren kein anderer aktiver Veröffentlichungstask und kein
paralleles Azure-Deployment erkennbar. Die Produktion wich vom Git-Ausgangsstand
ab. Deshalb wurde zunächst das passende vollständige Rückrollpaket gegen alle
76 installierten Dateien einschließlich Manifest verifiziert.

prepare_verified_release.py erweitert ausschließlich diese explizit gepinnte
Basis: 73 bestehende Quelldateien bytegleich, zwei gezielte additive Änderungen
an Registrierung/Provenance, fünf neue Ergebnisquellen sowie neues Manifest.
Keine Mäh-, Bewässerungs- oder Belegungslogik wurde durch einen alten Git-Stand
ersetzt. Keine Hardwarebefehle wurden zum Testen ausgeführt.

Der erste Bereitstellungsversuch verursachte einen vorübergehend nicht gesunden
Function-Host. Azure meldete fehlenden Lesezugriff auf host.json: der neue
ZIP-Builder hatte Dateimodus 0600 statt der ursprünglichen 100644 erzeugt.
Das Originalpaket wurde sofort zurückgespielt und mit Host Running, 16 Funktionen,
76 identischen Dateien und unveränderten Sicherheitsflags nachgewiesen.

Danach wurde der Builder korrigiert: bestehende ZIP-Metadaten bleiben erhalten,
neue Module erhalten reguläre lesbare Dateirechte, unlesbare Basispakete werden
abgelehnt. Regressionstests schützen diesen Fehler und die exakten zwei neuen
Funktionsnamen. Erst das korrigierte Paket wurde erfolgreich erneut bereitgestellt
und vollständig geprüft. Es wird **nicht** behauptet, dass es keine temporäre
Betriebsunterbrechung gab; tatsächliches Geräteverhalten wurde nicht getestet.

## Konfiguration und Rückrollweg

- Privater Container ssv53-results, kein öffentlicher Blob-Zugriff.
- Bestehende Managed Identity verwendet; keine zusätzlichen Rechte für sie benötigt.
- Für das angemeldete Verwaltungskonto nach ausdrücklicher Nutzerfreigabe
  Storage Blob Data Contributor ausschließlich auf diesem Container ergänzt.
- Nur vier neue SSV_RESULTS-Einstellungen hinzugefügt. Sämtliche ursprünglichen
  Einstellungen einschließlich Slot-Markierungen vor/nachher verglichen:
  unverändert. Globale CORS-Regeln wurden nicht geändert.
- Originalpaket, Prüfnachweise und Original-Appack-Vorlage liegen lokal vor.
  Vollständige vorherige Einstellungen sind lokal mit Windows-DPAPI verschlüsselt
  gesichert; keine Sicherungen, Schlüssel oder Diagnoselogs im Git.
- Bei Regression zunächst nur den Ergebnis-Timer deaktivieren, eigene Ergebnisseite
  aus der gesicherten Vorlage zurücknehmen und das verifizierte Originalpaket
  zurückspielen. Vorher auf fremde neuere Änderungen prüfen. Kein pauschales
  Zurücksetzen von Steuerungsmodi oder Hardwarefreigaben.

## Prüfungen

- Repository: **1467 Tests und 498 Subtests bestanden**.
- Eigenständiges Ergebnismodul: **58 Tests bestanden**.
- Bestehende Appack-Regeln: **163 JavaScript-Tests bestanden**.
- Mobile Chromium: **23 Checks**, 320 bis 1440 px, einschließlich Auswahl,
  fehlender Ergebnisse, Offline-Rückfall, Grafikzoom und Fokus.
- Netzgesperrter Paketimport: ursprüngliche 16 plus exakt zwei Ergebnisfunktionen;
  vollständige Provenance und ZIP-Dateirechte geprüft.
- Produktiver Cache: echte Azure-Managed-Identity-Lese- und Schreibzugriffe,
  Quellenabruf und erfolgreiche neue Zeitstempel für beide Sportarten.
- Browserprüfung mit produktiven Appack-Styles und echten Azure-Daten:
  Mannschaftsauswahl, Spiele/Tabelle, Originalgrafik und Zoom, zurückgezogene
  Mannschaft, Mini-Staffelhinweise, Europe/Berlin-Zeitangaben, externer Fußball-Link.
- Veröffentlichte Seite: Speichern bestätigt, öffentlich HTTP 200,
  ausgelieferte Skripte und Inline-Styles entsprechen der getesteten Vorlage.

## Noch offen

1. Echte Android-/iOS-WebView-Abnahme durch den Verein.
2. Nächsten automatisch ausgelösten Stundenlauf kontrollieren; der gezielt
   ausgelöste Azure-Lauf ist bereits nachgewiesen.
3. Telefonische Quellenfreigabe vereinsintern schriftlich dokumentieren.
4. PR-Review und spätere Zusammenführung mit aktueller Produktionsbasis;
   PR #97 wurde nicht automatisch gemergt.
