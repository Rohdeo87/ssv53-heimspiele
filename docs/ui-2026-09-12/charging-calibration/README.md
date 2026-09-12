# Ladeende: Herstelleranzeige und vorausschauende Kalibrierung

## Auftrag und belegte Ursache

Der Nutzer beauftragt am 12.09.2026: zunächst Husqvarna übernehmen, Daten sammeln und erst bei sehr hoher nachgewiesener Genauigkeit automatisch auf eine eigene Ladeprognose umstellen. Diese Änderung betrifft die **Anzeige**, nicht Start-, Park-, Trockenzeit- oder Bewässerungsfreigaben.

Die lesende Auswertung erfasst 9.730 bestehende Steuerungsbeobachtungen, davon 582 mit `CHARGING` und 130 mit positiver Herstellerrestzeit. Die frühere, strengere Sieben-Tage-Auswertung hatte drei vollständige Referenzladungen. Das genügt nicht für die geforderte Zuverlässigkeit. [Ausgangsmessung](baseline.json)

Ein Sprung um **36,3 Minuten** ist bereits in den Herstellerwerten belegt: am 12.09. um 17:04 Uhr Ortszeit 9 % Akku und 600 Sekunden Restzeit; um 17:05 Uhr 14 % und 2.712 Sekunden. `statusTimestamp + remainingChargingTime` verschiebt sich entsprechend. Das ist kein Nachweis eines neuen Ladevorgangs. Husqvarna liefert eine Schätzung, die sich ändern kann. Eine echte Korrektur wird nicht versteckt oder durch Festhalten an einer inzwischen falschen Uhrzeit ersetzt.

Zusätzlich enthielt die App zwei verschiedene Ladezeitpfade: die Anzeigeprognose bevorzugte Herstellerdaten, konnte aber auf nur zwei historische Ladungen zurückfallen; die nachgeladene Grundansicht verwendete die andere Planungsschätzung. Nun nutzen beide sichtbaren Ladezeitdarstellungen ausschließlich `chargingDisplayEstimate`. Zusatzdaten können die bereits gelieferte aktuelle Anzeigequelle nicht austauschen. Ohne gültige Herstellerangabe oder qualifizierte eigene Prognose bleibt die Zeit unbekannt. Die separate Planungsschätzung und sämtliche Geräteprüfungen bleiben unverändert.

Die vorhandene [Hersteller-Spezifikation](../irrigation-reliability/swagger.yml) beschreibt `battery.remainingChargingTime` als Sekunden bis zum vollen Akku; 0 bedeutet außerhalb einer Ladung bzw. fehlende Modellunterstützung. Herstellerportal: <https://developer.husqvarnagroup.cloud/apis/automower-connect-api>. Eine Ladeendeprognose ist keine Zusage einer Abfahrt.

## Sammlung und Modell

- Der bestehende Minutentimer speichert nach der Steuerungsentscheidung einen getrennten Datensatz in der bereits vorhandenen Azure-Tabelle: Partition `ssv53-charging-calibration-v1`, Mäherkennung als gehashter Zeilenschlüssel. Es gibt keinen neuen Scheduler und keine neue Infrastruktur. Zusätzliche kleine Tabellenzugriffe benötigen vorhandenen Speicher und dessen normale Transaktionen.
- Gesammelt werden Gerätemeldungszeit, Akkustand, Ladeaktivität, Herstellerrestzeit, tatsächliche erste Vollmeldung und die **vorher** berechnete eigene Prognose. Wiederholte Meldungen zählen nicht als neue Herstellerbeobachtung; eine Ladung zählt höchstens einmal als Prüffall.
- Ein Start muss durch eine vorherige frische Fahrt-/Heimfahrtmeldung belegt sein. Einstieg mitten in eine Ladung, Datenlücken, Fehler, Rücksprünge im Akku und abgebrochene Ladungen liefern keine Trainingsreferenz. Sie bleiben als nicht auswertbare Fälle im Bewertungsfenster. Nullwerte ohne belastbaren Akkunachweis werden verworfen.
- `PARKED_IN_CS` kann weiteres Laden bedeuten und teilt eine Ladung unter 100 % nicht auf. STOP/PAUSE machen den Abschnitt ungeeignet; Wiederaufnahme erzeugt keine zweite unabhängige Referenz. Ein Abschnitt endet spätestens nach sechs Stunden. Eine erste Vollmeldung wird erst mit passender Abschlussaktivität als tatsächliches Ende bestätigt; späteres Warten im Dock verlängert die Referenzzeit nicht.
- Ein eigener Kandidat benötigt mindestens fünf früher abgeschlossene, vergleichbare Ladungen an mindestens drei Tagen. Die höchstens zehn letzten Referenzkurven müssen den anfänglichen Akkustand abdecken. Es wird innerhalb gemessener Kurven interpoliert, nicht mit einer erfundenen Prozent-pro-Minute-Rate extrapoliert. Die Referenz-Restzeiten dürfen höchstens zehn Minuten auseinanderliegen.
- Aus deren Median entsteht einmalig am Ladebeginn eine auf Minuten gerundete Prognose. Sie bleibt für diesen Ladevorgang eingefroren. Eine bereits beim ersten Empfang abgelaufene Prognose wird verworfen. Spätere Messwerte, Neustarts und Nachladen von Seitendaten erzeugen keinen verschobenen eigenen Zielzeitpunkt.

Prognose und Auswahl werden gespeichert, **bevor** das spätere Ladeende bekannt ist. Die festgelegte Modellversion lernt schrittweise nur aus früheren Ladungen. Eine neue Modellversion verwirft die bisherigen Bewertungen. Historische Offline-Ergebnisse werden nicht als erfolgreich absolvierte Live-Prüfladungen eingespielt.

## Automatische Freigabe und Rückfall

Die Umschaltung erfolgt frühestens beim nächsten Ladebeginn, wenn für dessen Start-Akkubereich (1–24 / 25–49 / 50–74 / 75–99 %) alle Kriterien erfüllt sind:

1. Die letzten **60 unabhängigen Prüfladungen** dieses Bereichs sind vollständig und mit beiden Prognosen bewertbar; sie umfassen mindestens 14 unterschiedliche Tage und mindestens 14 volle Tage Zeitspanne. Fehlende Prognosen und abgebrochene Versuche werden nicht aus diesem Fenster herausgefiltert.
2. Die einseitige statistische 95-%-Untergrenze dafür, innerhalb von fünf Minuten zu liegen, beträgt mindestens 95 % (Wilson-Verfahren). Bei 60 Fällen müssen dafür alle 60 innerhalb von fünf Minuten liegen. Das ist eine Modellprüfung, keine Garantie für zukünftige Ladungen.
3. Die eigene mittlere Abweichung ist mindestens 20 % und mindestens 30 Sekunden kleiner als bei Husqvarna. Beide Prognosen werden am identischen ersten Beobachtungszeitpunkt verglichen. Zusätzlich muss dieser Vorteil auch gegenüber den im weiteren Ladeverlauf aktualisierten Herstellerprognosen bestehen, mit gleichem Gewicht je Ladung.
4. Die einseitige 95-%-Untergrenze für einen Vorteil von mehr als 30 Sekunden im direkten Vergleich liegt über 50 %. Der letzte Bewertungsabschluss ist höchstens sieben Tage alt.

Die Anzeige übernimmt dann die qualifizierte eigene Zeit. Bei ungültigen aktuellen Daten, einem überschrittenen Zielzeitpunkt oder fehlendem Datenspeicher fällt sie auf die aktuell gültige Herstellerzeit bzw. unbekannt zurück. Ein neuer Fehlversuch verhindert die erneute Freigabe für folgende Ladungen, bis das Bewertungsfenster wieder vollständig besteht. Es gibt keine manuelle Freigabe automatisch aus einer erreichten Uhrzeit.

Referenzen und höchstens 120 Ergebnisdatensätze werden rollierend maximal 90 Tage vorgehalten; je Eigenschaft wird die tatsächliche UTF-16-Größe gegen die Tabellenbegrenzung geprüft. Vergleichende Schreibbedingungen verhindern das Überschreiben einer konkurrierenden Aktualisierung. Fehler beim Sammeln werden protokolliert und führen niemals zur Wiederholung eines Gerätebefehls. Abschalten von `CHARGING_LEARNER_ENABLED` auf `false` beendet Nutzung und Sammlung der eigenen Prognose, die Herstelleranzeige bleibt bestehen.

## Prüfung und Grenzen

[Offline-Wiedergabe](replay.json): acht geeignete abgeschlossene Referenzen, nur drei eigene bewertbare Prognosen; keine Freigabe. Das ist kein Live-Parallelbetrieb. Das unabhängige Review mit gpt-5.6-luna prüfte insbesondere Datentrennung, unabhängige Ladeabschnitte, vergleichbare Prognosezeitpunkte, Speichergrenzen und Steuerungsisolation. Daraus gefundene Probleme wurden mit Regressionstests behoben.

Die Tests decken unter anderem Herstellerkorrekturen, keine unbelegte Anzeige aus historischen Ersatzwerten, kalten Prozessstart, doppelte Meldungen, unterbrochene/geparkte Ladung, STOP, ungültige Daten, erste Vollmeldung, ausbleibende Daten, fehlende Vergleichsprognosen, Umschaltung erst nach später beobachteten Ergebnissen, Zurückfallen, Akku-Bereiche, Modell-/Mäherwechsel, abgelaufene Prognosen, konkurrierendes Speichern und einen Ausfall des Journals nach bereits erfolgter Steuerungsentscheidung ab. Ausgaben: [Backend](backend-tests.txt), [App](appack-tests.txt).

Verbleibend: Erst künftige Ladungen können die geforderte prospektive Datenmenge liefern. Temperatur-, Akkuverschleiß- und Firmwareänderungen werden nicht als zusätzliche Messmerkmale erfasst; sie können die Ladecharakteristik verändern und erfordern erneute Bewährung anhand der tatsächlichen Ergebnisse. Die aktuelle Anzeige kann weiterhin springen, wenn Husqvarna selbst seine Schätzung korrigiert. Bei fehlenden Daten wird keine scheinbar präzise Uhrzeit erfunden.

Rückfall der Software: Hersteller-only-Anzeige beibehalten; vorheriges Azure-Archiv `dist/automatic-takeover-release.zip`, SHA-256 `4a0fd69a343429be1f60f7f82cb484d828f508b721d64ad94c81ee19f1fdd266`. Die neue Lernpartition ist von sämtlichen Geräteaktionen und Steuerungszuständen getrennt; ein Rückfall setzt keine Park-/Start-/Bewässerungsaktion zurück. Die alte App-Vorlage enthält jedoch den unzuverlässigen Ersatzpfad und sollte nicht unbesehen wieder veröffentlicht werden.
