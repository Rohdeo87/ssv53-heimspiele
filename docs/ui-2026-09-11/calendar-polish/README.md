# Bestehenden Platzbelegungsplan behutsam modernisieren

Der Nutzer behält ausdrücklich das vorhandene Zeitraster. Die zuvor diskutierten
alternativen Kalenderlayouts werden nicht umgesetzt.

## Änderung

- Flache, einheitliche Schaltflächen ohne zusätzliche goldene Auswahlkante.
- Ruhigere Kopfzeilen, feinere Zwischenlinien, dezente blaue Heute-Markierung.
- Einheitliche Datumsnavigation und größere Bedienelemente in Termindialogen.
- Platzspalten, sieben Wochentage, Zeitraster, horizontale Navigation, Zoom,
  Terminfarben, Sperrzeiten, Trainerrechte und Aktionen bleiben erhalten.

Die Saisonknöpfe blitzten auf, weil sie im HTML sichtbar waren und erst
`applySharedTrainingCalendar` nach der Datenantwort verborgen wurden.
Jetzt trägt ihre Gruppe bereits im HTML `hidden`. Die Steuerleiste reserviert
dafür auch keinen leeren Bereich. Nur wenn eine gültige Antwort den bisherigen
Betrieb mit Saisonwahl liefert, werden die Knöpfe und deren Platz im Layout
wieder eingeblendet. Fehler beim Start zeigen keine vorübergehende Saisonwahl.
Die Entscheidung über den verbindlichen Sommer-/Wintertrainingsplan wird nicht
verändert und bleibt im bestehenden Backend/Platzpflege-Ablauf.

## Prüfung

Die bisherige öffentliche Appack-Auslieferung wurde gegen den Ausgangsstand
verglichen: CSS und JavaScript stimmen überein; lediglich die profilabhängige
Zeile und die Workbook-Kennung wurden dafür normalisiert. Siehe
[before-publication.json](before-publication.json).

28 gezielte Kalender- und Ansprechpartner-Tests erfolgreich:
`node --test tests/test_appack_occupancy.js tests/test_appack_contacts.js`.
Die bestehende Saisonprüfung deckt nun zusätzlich das vor JavaScript verborgene
HTML und das Layout nach Aktivierung bzw. Deaktivierung der gemeinsamen Quelle ab.

Die Browserprüfung verwendet die tatsächliche Vorlage und die bereits verwendete
FullCalendar-Version 6.1.15, mit synthetischen Terminen und ersetzten Datenabrufen.
Externe Verbindungen und sämtliche Schreibanfragen sind blockiert. Geprüft:

- 320, 390 und 768 Pixel: keine horizontale Seitenüberbreite oder abgeschnittenen
  Auswahlknöpfe; die Woche enthält weiterhin 14 Platzspalten.
- Tagesansicht, Termindetails und Verlegen-Formular für einen Trainer geöffnet.
  Kein Termin gespeichert oder abgesagt.
- Verzögerter Start und fehlgeschlagener Abruf: Saisonwahl bleibt verborgen.
- Gültige bisherige Datenquelle ohne gemeinsamen Kalender: Saisonwahl verfügbar.

Details: [browser-check.json](browser-check.json),
[provenance.json](provenance.json). Bildschirmaufnahmen waren in der aktuellen
Browseroberfläche nicht verfügbar; die Prüfung basiert auf dem tatsächlichen DOM,
Layoutmaßen und Bedienabläufen. Keine Abnahme auf einem echten Handy.

Reproduzierbare isolierte Ansicht: `python scripts/prepare_calendar_polish_preview.py`.
`after.html` und `before.html` enthalten Beispieltermine. Ihre erzeugten Dateien
und die unveränderte Kalenderbibliothek sind lokale Prüfarbeitsdateien.

## Veröffentlichung

**Vorbereitet, noch nicht veröffentlicht.** Die Appack-Sitzung war abgelaufen und
eine erneute Anmeldung wurde angefordert. Ziel ist ausschließlich
`Belegungsplan_selfmade_2_optimiert.tpl`, ID `6a6242dfccdd23e7a9553567`.
Keine Azure-Installation oder Gerätebefehle erforderlich.

Nach Anmeldung: aktuellen Editor gegen den belegten Ausgangsstand prüfen,
`appack-platzbelegungsplan-azure.txt` übernehmen, speichern und mit
`python scripts/verify_calendar_polish_publication.py` die Auslieferung prüfen.
Vorlage aus dem Ausgangscommit `72ed7e6` ermöglicht einen Rückfall der Anzeige.
