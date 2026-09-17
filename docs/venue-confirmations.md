# Bestätigte Platzzuordnung bei fehlender Quellangabe

`config.json` kann unter `confirmed_venue_assignments` ausdrücklich bestätigte
Spielstätten enthalten. Schlüssel ist die offizielle Spiel-ID. Die Freigabe ist
an Anstoßzeit sowie beide Mannschafts-IDs gebunden; ein verlegter Termin oder
geänderter Gegner benötigt eine neue Prüfung.

Die Zuordnung ergänzt ausschließlich eine **fehlende** Spielstätte. Jede später
von FUSSBALL.DE veröffentlichte Spielstätte hat Vorrang, auch ein auswärtiger
Platz. Eine unklare lokale Quellangabe bleibt prüfpflichtig. Absagen bleiben
ausgeschlossen. Die normale Platzprüfung und der Rücknahmeschutz bleiben aktiv.

`venue` muss zu einer bestehenden Include-Regel und dem angegebenen `calendar`
passen. `confirmed_on` und `reason` dokumentieren die Freigabe. Die Rohdatensätze
bewahren unter `venue_assignment` die ursprüngliche fehlende Quellangabe und den
Freigabenachweis. Der App-Feed kennzeichnet die Herkunft mit
`locationSource: club-confirmation`; die unveränderten Quellantworten bleiben
im Diagnoseartefakt erhalten.

## Anlass vom 17.09.2026

Der Importlauf 35227796125 (Artefakt `dfbnet-diagnose-283`) wurde korrekt
blockiert: Für das D-Junioren-Freundschaftsspiel Schönwalder SV gegen
SC Oberhavel Velten I (U13), Spiel-ID `0323TKOBKG000000VS5489BTVT7QHFUC`,
am 23.09.2026 um 18:00 Uhr war keine Spielstätte vorhanden.

Am 17.09.2026 bestätigte der Nutzer im Projektchat ausdrücklich **Rasen**.
Dafür wurde eine termin- und mannschaftsgebundene Freigabe hinterlegt.
Die bestehenden Zeitregeln ergeben eine Belegung von **17:00 bis 20:15 Uhr**.

Die unveränderten vier Quellantworten des fehlgeschlagenen Laufs wurden offline
erneut verarbeitet: 115 Termine, davon 40 lokale Spiele (35 Rasen, 5 Kunstrasen),
75 ausgeschlossen, keine offene Platzprüfung. Das ist ein reproduzierbarer
Test, kein neuer Liveabruf; für die Veröffentlichung muss der reguläre Import
die Quelle frisch abrufen.

Bei künftigen Platzprüfungen enthält das Fehlerprotokoll nun Termin,
Mannschaften, Ursache, vorhandene Spielstätte und Link zur Spielseite.
