# Ladeende und Flächenfortschritt

Entwicklungsstand 11.09.2026. Veröffentlichungsnachweis folgt nach tatsächlicher
Installation. Keine Geräte-Testbefehle.

## Nachgewiesene Ursache

Lesender Abruf von 9.732 Produktionszyklen über sieben Tage. Die bisherige
Berechnung fand vier fehlerfreie abgeschlossene Ladeabschnitte. Nur zwei
deckten den heutigen Start bei 9 % ab (05.09.: etwa 51 Minuten; 09.09.: etwa
50 Minuten). Sie verlangte mindestens drei. Deshalb blieb die Uhrzeit auch
bei 94 % leer. Um 13:40 Uhr war eine Batteriemeldung 182 Sekunden alt; dadurch
wurde zusätzlich der gesamte aktuelle Ladeabschnitt dauerhaft ausgeschlossen.

Die [Wiederholung der heutigen Meldungen](replay.json) ergibt für 13:38 Uhr
(94 %) und 13:41 Uhr (99 %) jetzt jeweils **13:45 Uhr, voraussichtlich**.
Dies ist eine nachträgliche Wiederholung, keine damals ausgespielte Prognose.
Ein direkter lesender Herstellerabruf um 13:45:39 Uhr meldete 100 % und MOWING.

## Änderung und Grenzen

- Die [offizielle Husqvarna OpenAPI-Dokumentation](https://developer.husqvarnagroup.cloud/apis/automower-connect-api)
  beschreibt `battery.remainingChargingTime` als Restzeit in Sekunden. Null
  kann fehlende Modellunterstützung bedeuten. Dieses Feld wurde bislang beim
  Einlesen verworfen. Es wird nun übernommen und bei aktueller fehlerfreier
  Ladung als bevorzugte Anzeigeprognose verwendet. Anker ist der Zeitstempel
  der Meldung, nicht jeder neue Seitenabruf. Null wird nicht als Ladeende ausgegeben.
- Wenn keine positive Herstellerzeit vorliegt, verwendet ausschließlich die
  Anzeige zwei vergleichbare, vollständig bestätigte Ladeabschnitte an zwei
  Tagen. Es gibt keine pauschale Prozent-pro-Minute-Hochrechnung.
- Eine kurz verzögerte frühere Meldung bis 300 Sekunden verwirft nicht mehr
  die Anzeige für den gesamten Ladeabschnitt. Der ursprüngliche Ladestart
  bleibt erhalten. Fehlende Zyklen, Rückschritte, Fehler und längere Lücken
  verhindern die Prognose. Die aktuelle Meldung muss weiterhin höchstens
  180 Sekunden alt sein. Historische Vergleichsladungen bleiben streng geprüft.
- Die Uhrzeit aus dieser Anzeige wird nicht an die Bewässerungsplanung
  weitergegeben. Deren bestehende Berechnung mit drei Vergleichsladungen bleibt
  unverändert. Auch eine nächste Startzusage wird nicht aus der neuen Uhrzeit
  abgeleitet. Abgelaufene empirische Prognosen werden nicht nach hinten verschoben.
- Auf der Übersicht erscheint beim aktuellen Mähen „Fläche gemäht 19 %“ mit
  Flächensymbol und Fortschrittsbalken. Werte kommen direkt aus dem gemeldeten
  Arbeitsbereich, nicht aus einer alten Statistik. Bei Heimfahrt, Laden,
  veralteten Meldungen oder Gerätefehlern wird kein aktueller Mähfortschritt behauptet.

Der Herstellerabruf erfolgte nach Abschluss der Ladung und enthielt Restzeit 0.
Eine positive Hersteller-Restzeit dieses Geräts ist deshalb noch nicht live
nachgewiesen. Der vorhandene empirische Ersatz wurde mit den Betriebsdaten
reproduziert. Fehlende geeignete Daten bleiben als unbekannt erkennbar.

## Prüfung

- Neue Regressionen für Sekunden/Zeiteinheit, unveränderte Uhrzeit beim
  Aktualisieren, Null/fehlende/ungültige Werte, aktuelle und vergangene veraltete
  Meldungen, Lücken, Fehler, Unterbrechungen, Neustart und abgelaufene Prognose.
- Separate Anzeigeprognose darf weder bestehende Startzeit noch Freigaben ändern.
- 163 Appack-Tests; 24 Browserkombinationen (8 Zustände, 320/390/768 Pixel).
- Zwei begrenzte Aufgaben mit Luna: Flächenanzeige implementiert und anschließend
  Ladeprognose unabhängig geprüft. Ein Hinweis auf ungültige numerische
  Eingaben wurde behoben und abgesichert. Die absichtlich strengere aktuelle
  Datenprüfung bleibt erhalten.
- Python-Gesamtlauf und Installationsprüfung werden vor Veröffentlichung ergänzt.

Die Bilder verwenden **synthetische Daten**:

![Ladeprognose](charging-display-390.png)
![Aktueller Flächenfortschritt](mowing-390.png)

Reproduktion: `SSV53_UI_OUTPUT=docs/ui-2026-09-11/ladeprognose-fortschritt`
für `python -m scripts.build_wunschdesign_preview` und
`node scripts/capture_wunschdesign.cjs` setzen.

Rückfall: vorheriges Azure-Paket `dist/platzpflege-wunschdesign-release.zip`
und die vor Veröffentlichung gesicherte Appack-Vorlage. Keine Steuerungsparameter
ändern; vor einem Hostneustart tatsächliche Geräte-/Wasserzustände kontrollieren.
