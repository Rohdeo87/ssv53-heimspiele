# Ladeanzeige und Bedienflächen korrigieren

Stand 11.09.2026, 13:30 Uhr (Berlin): entwickelt, offline und unabhängig
geprüft, in Appack veröffentlicht und nach Neuladen vollständig zurückgelesen.
Auch die tatsächlich ausgelieferten Designblöcke stimmen mit der geprüften
Version überein. Die native App-Sitzung auf dem Nutzerhandy wurde nicht geprüft.

## Nachgewiesene Ursachen und Änderung

| Rückmeldung | Ursache | Änderung |
|---|---|---|
| 29 % geladen, Laden nicht erkennbar | Die allgemeine Entscheidung `MOWER_BATTERY_CHARGING` wurde vor der tatsächlichen Aktivität `CHARGING` angezeigt. Der Akkustand lag nach der Umgestaltung nur in einem Untermenü. | Aktuelle Ladung heißt „Mäher lädt“. Akkustand und Ladebalken stehen auf der Übersicht, auch zusätzlich zu einer wichtigeren Rasenpause oder Belegung. |
| Mehrere große „Noch offen“-Felder | Fehlende Ladeprognose wurde sowohl als unbekannter Start als auch als separates unbekanntes Ladeende betont. | Während bestätigter Ladung entfällt die große unbekannte Startkachel. Das Ladeende bleibt kompakt als unbekannt erkennbar; vorhandene Schätzungen und andere relevante Planungszustände bleiben sichtbar. |
| Verschiedene Bewässerungsbuttons | Allgemeines `.btn` entfernte bei Aktionskacheln die blaue Iconfläche. Navigation verwendete fetten Text, übernommene Buttons normalen Text. | Einheitliche Iconflächen, Schriftstärken und ausdrückliche Beschriftungselemente. |
| Beschriftung/Dialog nicht sauber zentriert | Text lag als anonymer Flex-Inhalt neben großen Icons; schmale Dialogspalten hatten keine ausreichende Begrenzung. | Eigene Text-Spans, zentrierte Kacheln, kleinere Dialogicons, begrenzte Spalten und passende Innenabstände. |
| Unruhiges Aktualisieren | Der Button wechselte zu „Daten werden geladen“ und änderte dadurch seine Breite. | „Aktualisieren“ bleibt stehen; kleiner reservierter Ladeindikator und `aria-busy`, keine Überlagerung und keine verdrängten Statusdaten. |
| Doppelter Header | Wappen und Seitentitel wurden zusätzlich zum nativen Appack-Header dargestellt. | Zusätzlichen Header aus der Vorlage entfernt. |
| Mischung aus X-Karten und Unterseiten | Statistiken und Zeitplan verwendeten weiterhin native Dialoge. | Alle vier Informationsansichten sind Unterseiten mit demselben Zurück-Button. Nur konkrete Bestätigungen und Stoppentscheidungen öffnen Dialoge. |
| Fehlende oder zweifarbige Icons | Rote Planaktion und blaue Iconfläche konkurrierten; Zurücksetzen/Öffnen der Historie überschrieb das dekorierte HTML. | Einheitliche weiße Plankacheln mit blauen Icons, Icons auch an Mäherwerten und Trainingsumschaltung, erneute Dekoration bei Textänderungen. |

Es werden ausschließlich die Darstellung und Browserprüfungen geändert.
Backendfreigaben, Akkugrenzen, Geräteaktionen, Bewässerungsfenster und die
Reihenfolge wichtigerer Konfliktmeldungen bleiben unverändert. Ein geparkter
Mäher gilt nicht allein wegen einer Akkusperre als ladend. Alte, fehlende oder
fehlerhafte Gerätemeldungen erzeugen keine aktuelle Ladeanzeige.

Eine Ladeuhrzeit wird weiterhin nur aus der vorhandenen belastbaren Schätzung
übernommen. Der Schätzer verlangt mehrere vergleichbare abgeschlossene
Ladeabschnitte und ausreichende aktuelle Messpunkte. Ein einzelner Akkustand
wie 29 % reicht für eine seriöse Uhrzeit nicht aus. Es wurde kein Endzeitpunkt
erfunden und kein Schutzkriterium des Schätzers gelockert.

## Prüfnachweise und Ansichten

- [160 Appack-Tests erfolgreich](node-tests.txt), einschließlich des exakten
  gemeldeten Falls mit 29 %, echter Akkusperre und fehlender Ladeprognose.
- [21 Browserkombinationen](browser-check.json): sieben Zustände bei 320,
  390 und 768 Pixel Breite. Kein Überlauf, keine JavaScript-Fehler.
- Zusätzliche Prüfung des verzögerten Aktualisierens: Beschriftung und Breite
  bleiben gleich, Ladestatus bleibt sichtbar, kein Dialog öffnet sich.
- Vier Bewässerungskacheln besitzen identische Schrift-/Iconstile;
  Bestätigungsbuttons halten Icon und Text innerhalb ihrer Begrenzung.
- Zeitplan/Statistiken öffnen ohne modalen Dialog; Zurück geht erst aus dem
  Bearbeitungsschritt zur Auswahl und dann zur Bewässerung. Eingegebene Uhrzeit
  und Zonendauer überstehen Aktualisieren. Abbrechen einer Bestätigung lässt
  die ursprüngliche Planseite sichtbar. Historienicons bleiben nach Klick erhalten.
- Zwei gezielte unabhängige Reviews mit dem kostengünstigen Luna-Modell:
  Ladeanzeige/Schutzpriorität sowie Unterseiten/Handler/Entwurfserhalt.
  Keine konkrete Regression gefunden; keine zusätzlichen Geräte- oder API-Aufrufe.
- HTML, CMS-Kopierfassung und Designquellen synchron; `git diff --check` bestanden.
- Netzwerk in den Browserprüfungen gesperrt, keine Gerätebefehle.

Die folgenden Bilder zeigen ausdrücklich **synthetische Daten**, keine
neuen Gerätemeldungen des realen Mähers:

![Laden mit 29 Prozent](charging-unknown-390.png)
![Einheitliche Bewässerungskacheln](water-menu-390.png)
![Zentrierte Bestätigungsbuttons](confirmation-390.png)
![Dezentes Aktualisieren](refresh-390.png)
![Bewässerungsplan als Unterseite](water-plan-390.png)
![Bewässerungsstatistiken als Unterseite](water-stats-390.png)
![Mäherwerte mit Icons](controls-390.png)

Reproduktion: `SSV53_UI_OUTPUT=docs/ui-2026-09-11/ladeanzeige-korrektur`
für `python -m scripts.build_wunschdesign_preview` und
`node scripts/capture_wunschdesign.cjs` setzen. Die vorhandenen bisherigen
Releaseaufnahmen werden dadurch nicht überschrieben.

## Veröffentlichung und Rückfall

- [PR 73](https://github.com/Rohdeo87/ssv53-heimspiele/pull/73) wurde nach
  erfolgreichen Prüfungen des exakten Quellstands
  `872254f06a69ef18742dfedbdff23b91786657d1` zusammengeführt.
  Merge: `053fd09964b9c39192a7a82216474e6b5c0d6f07`.
- [Code-Prüfung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34594179746)
  und [Paket-Prüfung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/34594179791)
  erfolgreich. Der zusammengeführte Quelltext entspricht dem geprüften Stand.
- Ausschließlich `Platzpflege.tpl`, ID `6a86ab6c4b3c829dd60de9b7`, ersetzt.
  Appack zeigt als letzte Änderung 11.09.26, 13:30 Uhr. Keine Azure-Veröffentlichung,
  keine Geräte-Testbefehle und keine Änderung der Steuerungsregeln.
- Nach vollständigem CMS-Neuladen den gesamten Editorinhalt erneut kopiert:
  exakt gleich, normalisierter SHA-256
  `79395caf9dd98b6ec910d15a49cc01fbf30c6cc310f9e6c70cc25c20a05b554d`.
- Den tatsächlichen Render-Endpunkt in einem neuen Browser-Tab geöffnet:
  CSS (14.998 Zeichen) und JavaScript (38.149 Zeichen) entsprechen der Vorlage.
  Vier Informationsansichten sind `SECTION`, nur Bestätigung und Stoppentscheidung
  bleiben `DIALOG`. Zusätzlicher Header fehlt; keine JavaScript-Konsolenfehler.
- Details: [maschinenlesbarer Veröffentlichungsnachweis](publication-proof.json).
  Im nicht aktivierten Browser war die Einrichtungsansicht sichtbar. Die
  Prüfungen mit Ladestatus, Unterseiten und Aktionen erfolgten mit synthetischen
  Daten; dies ist kein Nachweis einer neuen Gerätemeldung oder Geräteaktion.

Rückfall: die [vorherige gesicherte Vorlage](appack-before.tpl) im selben
Appack-Template wiederherstellen und gespeichert sowie ausgeliefert vergleichen.
Ihr normalisierter SHA-256 ist
`3bbb6f41276b3954909250e19d981eee115c69e243c5903bb26765fda68b143c`.
Für diese Darstellungsänderung ist kein Rücksetzen der Gerätesteuerung erforderlich.
