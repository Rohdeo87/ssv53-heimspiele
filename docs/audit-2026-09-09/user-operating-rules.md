# Verbindliche Betriebsregeln aus der Nutzerentscheidung

Stand: 09.09.2026. Diese Regeln sind fachliche Eingaben für den Preflight. Sie
ersetzen keine Geräte-, Quellen- oder Aktivierungsnachweise.

## Feiertage und Training

- Gesetzliche Brandenburger Feiertage werden ausschließlich als **kein
  Training** behandelt. Spiele bleiben bestehen und werden nicht durch diese
  Regel entfernt.
- Gesetzliche Feiertage sind nicht mit Schulferien gleichzusetzen. Eine
  öffentliche Ferienangabe beweist keine Trainingsabsage.
- Grundlage für die zwölf gesetzlichen Feiertage einschließlich Oster- und
  Pfingstsonntag ist [Brandenburgisches Feiertagsgesetz, § 2 Abs. 1](https://bravors.brandenburg.de/gesetze/ftg_2015).
  Die Prüfung besonderer Feiertage folgt § 2 Abs. 3; die Landesübersicht ist
  [hier](https://brandenburg.de/de/31504) dokumentiert.
- Die Saison wird nicht aus geratenen Umschalttagen abgeleitet. Bis zur
  fachlichen Kalenderfreigabe bleiben die bisherigen Sperren erhalten.
- Der manuelle, persistente Winterumschalter ist umgesetzt und offline geprüft.
  Er wirkt zentral zum nächsten Berliner Mitternachtswechsel. App, Trainerpflege
  und Mähplanung verwenden dieselbe datierte Entscheidung. Die Historie erhält
  den vorherigen Plan für über Mitternacht laufende Trainings und deren Puffer.
  Fehlender Zustand ergibt keine Freigabe. Der heutige Plan und ein vorgemerkter
  Wechsel erscheinen getrennt in einfacher Sprache auf der Platzpflegeseite.

## Bewässerungsfenster

- Jede geplante Bewässerung startet frühestens um **03:30 Europe/Berlin**.
- Alle Zonen müssen spätestens um **08:00 Europe/Berlin** beendet sein.
- Native Dauer und Pausen werden nicht verkürzt; Wasserbedarf wird nicht
  reduziert und es werden keine zusätzlichen Bedarfe erzeugt.
- Das Beispiel eines nativen 160-Minuten-Laufs von 04:30 bis 07:10 ist
  beobachtete Ausgangsinformation. Daraus folgt ein spätester reiner Start um
  05:20. Eine frühere Freigabe benötigt eine ausdrücklich dokumentierte
  Kontrollmarge.
- 20-Minuten-Pausen zwischen Zonen sind im vorliegenden Nachweis ein
  synthetischer Testfall, kein Live-Nachweis.
- Feiertage verändern dieses Bewässerungsfenster nicht.

## Sicherheits- und Nachweisgrenzen

Native Hydrawise-Zeitpläne bleiben ein eigener Steuerpfad. Vor jeder
Einführung sind deren Admission-Check, mögliche Unterdrückung des nativen
Termins und die tatsächliche Gerätewirkung nachzuweisen. Ein einzelner
Quellabruf oder ein Export behauptet nicht, dass alle nativen Pläne erfasst
wurden. Der Trainingskalender darf Spiele, Sonderbelegungen und manuelle
Stopps nicht ausfiltern.

Die Implementierungen wurden von Root integriert und unabhängig geprüft.
Gesamttests, App-Vorschauen und Paketprüfung sind im
[Abschlussnachweis](final-preflight-delivery.json) gebunden. Diese Datei enthält
keine produktive Aktivierung und keinen Beleg für einen unbeaufsichtigten Vollbetrieb.
