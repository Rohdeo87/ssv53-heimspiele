# Ergebnisintegration: Nachweis und verbleibende Gates

Stand: 24.09.2026. Grundlage: PR #97, Ausgangscommit
`b92f14eaac915d67ef18a8ac105703b753e00739`.

## Tatsächlich ausgeführt

- Übergabekommentar 5818536495, PR-Beschreibung und README vollständig gelesen.
- Lokale Aufgabenliste: kein weiterer aktiver Veröffentlichungstask erkennbar;
  PR-Head bei Übernahme unverändert. Azure zeigte kein laufendes Deployment.
- Azure-CLI und angemeldetes Appack-CMS in dieser lokalen Sitzung nutzbar.
- Ergebnis-Blueprint im bestehenden Einstieg registriert. Vier vollständige
  Paket-Builder enthalten die fünf Ergebnisquellen an identischen Git-/ZIP-Pfaden.
  Keine Quellkopien und keine Änderung der bestehenden Steuerungslogik.
- Provenance-Prüfung kontrolliert auch Ergebnisquellen; Cache-/HTTP-/Timertests
  prüfen Cache-only-Lesen, ETag, Fehlerbehandlung, ausgeschalteten Timer und Backoff.
- Begrenzter robots-geprüfter Live-Abruf: Handball erfolgreich am
  `2026-09-24T17:09:45Z`, Volleyball am `2026-09-24T17:11:48Z`.
  Ein erster Volleyball-Versuch war nicht erfolgreich; nachfolgende begrenzte
  Prüfung erfolgreich. Keine Schutzumgehung oder Parser-Sonderregel eingeführt.
- Privater Container `ssv53-results` im vorhandenen Konto
  `ssv53platzpflegeprodq7kb` angelegt; `publicAccess=null` bestätigt.
  Die vorhandene Function-Identity besitzt bereits geerbte Blob-Owner-Rechte.
  Keine neue Rollenzuweisung, keine Schlüssel-Authentifizierung eingeschaltet.

## Quellenbefund

Handball: Saison 2026/27, Männer (12 Spiele), Frauen (12), männliche D (20),
Mini-Staffeltermine (5, kein SSV-Teilnahmenachweis), Männer-Pokal (2).
Weibliche C laut offizieller Tabelle zurückgezogen: entsprechend gekennzeichnet,
keine erfundenen Spiele oder Resultate. Nur Mannschaftsdaten, keine Personenlisten.

Volleyball: 2. Kreisklasse Mixed 2026/27, zehn SSV-Ansetzungen.
Originalgrafik mit Quellenstand 19.07.2026 unverändert; das ist nicht der Zeitpunkt
des technischen Abrufs. Fehlende Resultate bleiben fehlend, nicht 0:0.

Quellen:
- https://hvbrandenburg-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/clubTeams?club=33547
- https://hvbrandenburg-handball.liga.nu/static/impressum.htm
- https://ksv-volleyball-oberhavel.de/mixed_2.html
- https://ksv-volleyball-oberhavel.de/impressum.html

Robots-konformer technischer Abruf ist keine Veröffentlichungslizenz.
Das KSV-Impressum enthält einen Zustimmungsvorbehalt zur Verwertung außerhalb
gesetzlicher Grenzen; eine schriftliche Erlaubnis zur regelmäßigen Übernahme und
Weiterveröffentlichung der Grafik liegt hier nicht vor. Auch für die regelmäßige
nuLiga-Übernahme ist keine ausdrückliche Anbieterfreigabe nachgewiesen.
Dieses Dokument behauptet weder ein pauschales gesetzliches Verbot noch eine Lizenz.
Gemäß Übergabe bleibt die regelmäßige Übernahme bis zur Klärung gesperrt.

## Nachgewiesene Veröffentlichungsblocker

1. **Produktionsbasis weicht ab.** Laufende Function App
   `func-ssv53platzpflege-prod-q7kbw54s`: Host Running, 16 registrierte Funktionen.
   Letztes aktives Deployment laut Kudu: 20.09.2026, ID
   `666ec340-43d6-411d-82a0-9f86263a5c8c`.
   Installiertes Manifest SHA-256:
   `d35286219a2c77637a20a00016223b541cae12547b28fe19fe82f5960c70bc9f`.
   Alle 75 manifestierten Dateien wurden lesend gegen ihre Hashes geprüft.
   21 weichen vom PR-Ausgangsstand ab, darunter `function_app.py`,
   `daily_safety_report.py`, `platzwart_console.py` und Steuerungsdateien unter
   `mower/` (u.a. `full_failsafe.py`, `device_send_guard.py`,
   `irrigation_recovery.py`, `automatic_takeover.py`). Der aktuelle PR-Zielbranch
   steht weiterhin auf `daa86dbda717721adb448beec95701945102128c` und löst diese
   Abweichung nicht. Kein altes Gesamtpaket über die neuere Produktion deployen.
   Zuerst den dazugehörigen produktiven Quellcommit/rückspielbaren vollständigen
   Paketstand nachweisen und die additive Integration darauf übertragen.
2. **Cache-Initialisierung verweigert.** Containeranlage erfolgreich, aber beide
   Blob-Uploads mit angemeldetem Benutzer werden von Azure mangels Datenrechten
   abgewiesen. Kein Wechsel auf Kontoschlüssel oder andere Umgehung vorgenommen.
   Container daher noch ohne erfolgreich initialisierten Ergebniscache.
   Die geerbten Rechte der Function-Identity sind kein erfolgreicher Laufzeitnachweis.
3. **Quellenfreigabe offen**, insbesondere KSV-Originalgrafik (siehe oben).

## Lokale Prüfungen

- Vollständige Repository-Regression: **1460 Tests und 490 Subtests bestanden**.
- Eigenständiges Ergebnismodul: **58 Tests bestanden**.
- Appack-Anzeige-/Bedienregeln: **163 JavaScript-Tests bestanden**.
- Mobile Chromium-Regressionssuite: **23 Checks bestanden**, 320 bis 1440 px,
  einschließlich Auswahl, fehlender Ergebnisse, Offline-Rückfall, Zoom und Fokus.
- Platzpflege-Design/CMS-Kopierfassung weiterhin synchron.
- Exaktes Gesamtpaket aus Commit `63351dd3e29058c4c638fb3a5b41c276d5100a81`:
  74 kanonische Git-Dateien bytegleich geprüft, 18 Funktionen im vollständig
  netzwerkgesperrten Import registriert, Manifestprüfung erfolgreich.
  Paket-SHA-256: `caa4403e6fcc60e7cafcfe157e88addb2c9c5a377228f09a517c3880fe9773b8`.
  Das ist ausdrücklich **kein** Nachweis für ein produktives Deployment.
- Diese Browserprüfungen arbeiten mit synthetischen Daten und ersetzen weder
  produktives Appack-Rendering noch echte Android-/iOS-Geräteprüfung.

## Unveränderte Appack-Veröffentlichung

Authentifiziert verifiziert: bestehende `Ergebnisse_Tabellen.tpl`, Workbook
`Abteilungen_db`, öffentliche Vorschau
https://appack.de/rest-api/drender/69328f3157d8e5e1cbb4ca25.
Aktuelle Seite enthält weiterhin Fußball-, Handball- und Volleyball-Links.
Nicht bearbeitet, gespeichert oder ersetzt; kein Rückrollen erforderlich.

Keine Function-App-Einstellungen, CORS-Regeln, Timer, Hardware-Freigaben,
Zeitpläne oder Steuerungsmodi verändert. Keine Gerätebefehle ausgeführt.
`SSV_RESULTS_*` waren vor Beginn nicht gesetzt; kein Ergebnisendpunkt installiert.
Kein öffentlicher Cache-Nachweis, kein Timerlauf, keine Android-/iOS-WebView-Abnahme.
Der einzige Azure-Schreibschritt ist der private, derzeit leere Ergebniscontainer.

## Fortsetzung ohne Neuimplementierung

1. Produktive Deployment-Basis samt rückspielbarem Paket eindeutig sichern;
   unmittelbar vor Veröffentlichung parallele Deployments erneut prüfen.
2. Quellenfreigaben dokumentieren und Cache-Initialisierung über einen berechtigten
   Weg ermöglichen, ohne Zugriffsgrenzen zu umgehen.
3. Integration auf der aktuellen Basis vollständig testen und über den freigegebenen
   Deploymentweg bereitstellen; bestehende Flags unverändert lassen.
4. Nur neue Ergebnis-Konfiguration setzen, zunächst deaktiviert; Cache, beide
   öffentlichen API-Antworten und endpoint-spezifisches CORS nachweisen.
5. Erst danach Timer aktivieren und genau die gesicherte Appack-Ergebnisseite
   aktualisieren. Öffentliche Auslieferung und echte Geräte separat abnehmen.
