# Ganzer Rasenplatz beim Öffnen der Karte

Live in Appack seit 13.09.2026, 11:14 Uhr (Berlin). Die bisherige feste Zoomstufe um die wechselnde Mäherposition wurde durch einen an die sichtbare Kartenfläche angepassten Ausschnitt des gesamten Rasenplatzes ersetzt. Es änderte sich ausschließlich die Appack-Oberfläche, keine Azure-Installation oder Gerätefreigabe.

## Verhalten

- Jedes Öffnen der Kartenunterseite, auch über Zurück/Vorwärts, zeigt den gesamten Rasenplatz mit kleinem Rand. Neue Positionsmeldungen verschieben diesen Ausschnitt nicht.
- Bildschirmgröße und Drehen des Handys werden berücksichtigt. Bei unterschiedlichen Seitenverhältnissen ist zwangsläufig etwas mehr Umgebung sichtbar; der Platz wird nicht abgeschnitten.
- Freies Zoomen/Verschieben bleibt erhalten und wird durch eine Statusaktualisierung nicht zurückgesetzt. „Platz zeigen“ stellt die Gesamtansicht wieder her, „Mäher zeigen“ zentriert die letzte Position und aktiviert deren Nachführung.
- Ohne gültige Position bleibt das Luftbild des Platzes sichtbar. Der Mäherpunkt und „Mäher zeigen“ werden entfernt; die Meldung erklärt die fehlende Position.

## Grundlagen und Grenzen

Die sichtbaren Rasenplatzränder wurden am öffentlich zugänglichen [LGB-WMS](https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetCapabilities) bestimmt. Der Ausschnitt umfasst den großen Naturrasenplatz; der südlich anschließende Kunstrasen ist nicht die Ziel-Fläche. Die geografischen Ansichtsgrenzen stehen als `pfPitchBounds` in der Vorlage und sind keine Sicherheits-, Geräte- oder Bewässerungsgrenzen. Weder aktuelle Mäherkoordinaten noch private Betriebsdaten wurden zur Bestimmung dieser Grenzen verwendet. Die Kartenlizenz und Quellenangabe bleiben erhalten.

Technisch verwendet die Oberfläche [Leaflet fitBounds](https://leafletjs.com/reference.html#map-fitbounds) mit 12 Pixeln Abstand und ohne das Abrunden auf ganze Zoomstufen. Nach Größenänderungen wird die Platzübersicht erneut eingepasst. Ein selbst gewählter Zoom bleibt bis „Platz zeigen“ oder erneutem Öffnen erhalten.

## Nachweise

- 234 App-Tests bestanden, einschließlich bestehender Zustands-/Bedienungsregeln und Skriptsyntax. Backend-Suite nicht erneut ausgeführt, da kein Backendcode geändert wurde.
- [Isolierter Browsertest](../../../scripts/check_pitch_map_ui.cjs) mit echten öffentlichen Luftbildern und ausschließlich synthetischen Mäherdaten: 320×568, 390×844, 430×932, 844×390 und 768×1024; jeweils zusätzlich gedreht. Getestet: volle Fläche innerhalb des Kartenrahmens, keine Verschiebung durch neue Position, manuelles Zoomen bleibt erhalten, „Platz zeigen“, „Mäher zeigen“, Wiederöffnen, fehlende Position, kein horizontaler Überlauf und keine JavaScript-Fehler. [Messprotokoll](ui-checks.json).
- [Vorschau 390 Pixel](pitch-390.png), [Querformat](pitch-844.png). Beide zeigen Beispielwerte; keine Geräteortungsabnahme.
- Produktive Ausgangsvorlage vor dem Speichern gegen den Hash der vorherigen Veröffentlichung geprüft und lokal gesichert. [Ausgelieferte Funktionen und neue Ansichtsgrenzen](published.json) nach dem Speichern exakt mit der getesteten Quelle abgeglichen. LF-SHA-256 der neuen Vorlage: `d325b1dcb3d92987481b9e26543ca907a7de41c706c4be9705b93d69a3bb36c1`.
- Keine direkte Prüfung auf allen realen Android-/iOS-Modellen. Ein bereits aktiviertes Handy kann die endgültige WebView-Darstellung bestätigen. Die Genauigkeit und Aktualität der Husqvarna-Position ist von dieser Ausschnittsanpassung unabhängig.

Rückfall: vorherige Appack-Vorlage aus der ignorierten lokalen Datei `appack-before-local.tpl` zurückspeichern (LF-SHA-256 `6574c9ce46abd248d3bc82ff699172bcfe1f185555bafb92791b5400ed1ef6a9`) und die ausgelieferte Quelle prüfen. Es wurden für diese Änderung keine Geräteaktionen oder Zeitpläne angelegt.

Reproduktion: `python scripts/build_mower_map_preview.py --output docs/ui-2026-09-13/pitch-map/preview`, danach `node scripts/check_pitch_map_ui.cjs` mit verfügbarer Playwright-Bibliothek. Der Testbrowser sperrt andere Netzwerkziele und nutzt keine Produktions-Anmeldedaten.
