# Mäherposition auf Luftbildkarte

Die Platzpflege erhält „Mäher auf der Karte“ auf der Übersicht, sobald eine Position geliefert wird. Unter **Sonstiges → Mähroboter → Karte** bleibt die Ansicht auch bei fehlenden Daten erreichbar. Große Schaltflächen vergrößern/verkleinern und führen zurück zum Mäher. Verschieben der Karte beendet das automatische Nachführen; „Mäher zeigen“ schaltet es wieder ein. Die vorhandene Zurück-Navigation einschließlich Browser-Verlauf wird wiederverwendet.

## Tatsächliche Datenwege

Husqvarna Automower Connect GET → bestehender `run_read_only_cycle` der authentifizierten Platzwart-Statusabfrage → `mower.position` → ausschließlich auf Wunsch geladene Leaflet-Karte. Es gibt keine zusätzliche Mäherabfrage, keinen neuen Endpunkt und keinen neuen Scheduler. Es wird nur das erste Element von `attributes.positions` verarbeitet. Ungültige erste Koordinaten führen zu „Position nicht verfügbar“, nicht zu einem älteren Ersatzpunkt. Keine Positionsabfrage des Handys.

Der tatsächliche 580 EPOS liefert `capabilities.position=true` und 50 Positionspunkte. [Lesender Hersteller- und Installationsabgleich](baseline.json). Der erste lokale Versuch hatte Key-Vault-Verweise als Zugangsdaten verwendet und schlug fehl. Nach regulärem Auflösen genau dieser vorhandenen Verweise über Azure Key Vault gelang die lesende Abfrage. Der produktive Abruf funktionierte auch davor. Keine Zugangsdaten geändert oder veröffentlicht; der tatsächlich beobachtete Einzelpunkt liegt nur in einer ignorierten lokalen Datei.

Die Hersteller-Spezifikation liefert **keine Uhrzeit pro Positionspunkt**. Die Anzeige nennt deshalb die zuletzt gemeldete Position, kennzeichnet eine mögliche Verzögerung und bezeichnet den daneben gezeigten Zeitstempel ausdrücklich als Gerätemeldung. Sie behauptet weder sekundengenaue Live-Ortung noch EPOS-Zentimetergenauigkeit. Die Karte dient nie als Nachweis einer sicheren Station oder als Start-/Bewässerungsfreigabe. Die GPS-Daten werden nicht in `MowerSnapshot` aufgenommen und nicht in regulären Steuerungs-/Ladejournalen gespeichert.

## Kartenanbieter und Ladeverhalten

- Öffentlicher [LGB-Dienst für Luftbilder](https://geobasis-bb.de/lgb/de/geodaten/luftbilder/luftbilder-aktuell/), WMS `https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms`, Layer `bebb_dop20c`. [Geprüfte WMS-Metadaten](wms-capabilities.xml). Genau dessen veröffentlichte äußere Grenzen begrenzen Anfragen. Die rechteckige Ausdehnung bedeutet nicht, dass außerhalb Brandenburg/Berlins jedes Bildpixel gefüllt ist.
- Namensnennung bleibt unter der Karte sichtbar: GeoBasis-DE/LGB, dl-de/by-2-0; Geoportal Berlin, dl-de/zero-2-0. Es sind amtliche Luftbilder, keine aktuell aufgenommenen Satellitenbilder. Kein kostenpflichtiges Kartenkonto und kein neuer Infrastrukturvertrag.
- [Leaflet 1.9.4](https://leafletjs.com/download.html) nach Herstellerempfehlung mit festgelegten Integritätsprüfsummen. JavaScript, CSS und Luftbilder werden erst beim Aufrufen der Karte geladen. Die übliche 30-Sekunden-Statusaktualisierung aktualisiert den Punkt. Gleiche Positionen lösen keine extra Geräteabfragen aus.
- Der externe Luftbilddienst erhält die angefragten Kartenausschnitte, keine App-Anmeldedaten und keine exakten GPS-Punkte als separate Tracking-Anfrage. Browser senden technisch die für Bildabrufe notwendigen Verbindungsdaten. Leaflet wird von unpkg geladen; Referrer-Unterdrückung ist gesetzt.
- Bei fehlender Position wird eine vorhandene Markierung entfernt. Anbieter-/Netzfehler haben eine eigene klare Meldung und blockieren keine andere Bedienung. „Aktualisieren“ lädt fehlgeschlagene Bilder erneut. Der örtliche Kartenausschnitt startet mit Zoom 18, damit mehr vom Platz sichtbar ist.

## Prüfung und Rückfall

Vollständige Backend-Suite: **1.749 Tests und 482 Unterfälle bestanden** ([Ausgabe](backend-tests.txt)). **234 App-Tests bestanden** ([Ausgabe](appack-tests.txt)). 320/390-Pixel-Browservorschau mit Beispieldaten: tatsächliche öffentliche Luftbilder geladen, Markierung, Zoom, Zurück/Vorwärts, Entfernung bei fehlendem GPS und kein horizontaler Überlauf ([UI-Prüfung](ui-checks.json)). Die gespeicherten Vorschauaufnahmen verwenden eine **Beispielposition am konfigurierten Kartenmittelpunkt**, keine gemessene Mäherposition. Kein Gerätetest und kein Nachweis der Genauigkeit des Herstellers.

Unabhängiges Read-only-Review mit gpt-5.6-luna: keine Änderung an Freigaben, keine zusätzliche Abfrage oder GPS-Speicherung, Navigation und Fehlerbehandlung geprüft. Die zu großzügige erste Kartenbegrenzung wurde auf die tatsächlichen WMS-Grenzen korrigiert.

Rückfall: vorige Appack-Vorlage lokal gesichert; backendseitig `dist/charging-calibration-release.zip` mit SHA-256 `62acc594a48cc4b5ac8edc94ad4c7598207da9ead2f57dbd6e9bc4b65dbd9638`. Nur drei vorhandene Backenddateien ändern sich im begrenzten Paket; alle anderen installierten Dateien bleiben bytegenau erhalten. Keine Geräteaktionen, Zeitpläne oder Freigabestufen werden durch diese Änderung angefasst.
