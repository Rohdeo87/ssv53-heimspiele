# Gemeinsame Planung: Simulation, Nachweise und Grenzen

Stand 09.09.2026. Dieser Baustein ist **umgesetzt und offline getestet**. Dieses
Simulationsmodell ist kein Laufzeit- oder Geräteadapter; es wurde weder im
produktiven Parallelbetrieb beobachtet noch am Gerät nachgewiesen. Die beiden
separat korrigierten Fehler in
`adaptive_planner.py` betreffen den vorhandenen Schattenvorschlag, keine
Geräteentscheidung oder verbindliche Sicherheitsregel.

## Dateien und Reproduktion

- [Unabhängiger Ausgangsreview](planning-review.md)
- [Neue Offline-Bibliothek](../../mower/coordination_simulation.py)
- [29 Tests für Koordination und Fehlerabläufe](../../tests/test_coordination_simulation.py)
- [13 Tests des vorhandenen Schattenplaners](../../tests/test_adaptive_planner.py)
- [Reproduzierbares Auswerteskript](../../scripts/simulate_coordination.py)
- [Vollständige Ergebnisse und Quellcode-Hashes](coordination-comparison.json)
- [Prüfbare gemeinsame Zeitleiste](coordination-comparison.html)

Aus dem Repository mit Python und installierter Zeitzonendatenbank:

```text
python -m unittest tests.test_coordination_simulation tests.test_adaptive_planner -v
python -m scripts.simulate_coordination
```

Das Skript benötigt keine Geheimnisse und ruft keine externe API auf. Es erzeugt
JSON und eine lokale HTML-Ansicht. Das Journal wird nur in temporären
Testverzeichnissen angelegt. Der eingeschaltete Modus im Skript aktiviert
ausschließlich Berechnung; `Settings.execution_enabled=True` wird abgewiesen.
Der Standardwert `Settings.enabled` ist `False`. Der Test der vollständigen
Tagesplanung verbietet sogar das Öffnen eines Netzwerksockets.

## Vergleich unter identischen Bedingungen

Referenz ist ein **modellierter Ablauf mit dem ursprünglichen Wassertermin**.
Sie ist kein historischer Replay der installierten FULL_FAILSAFE-Steuerung.
Alle drei Varianten verwenden denselben vollständigen Akku zu Tagesbeginn,
denselben Wasserbedarf, dieselben Sportzeiten und dieselben Energieparameter.
Die vorhandenen Sicherheitsvorläufe werden durch diesen Vergleich nicht
abgesenkt; eine geräteseitige Unterdrückung autonomer Starts und des Ursprungslaufs
wird für alle Varianten bereits als bestätigt angenommen.

Annahmen für Dienstag, 15.09.2026, Europe/Berlin/MESZ:

- Ganzer Tag 00:00–24:00, entsprechend dem Repository-Zeitfenster; produktive
  Nachtfahrten sind eine Modellannahme, kein nachgewiesener Gerätebetrieb.
- 180 produktive Mähminuten pro voller Ladung, 120 Minuten vollständiges Laden,
  proportionale Teilnachladung und vier Minuten Heimfahrt. Diese Akkudaten sind
  **keine** Aussage zur Spezifikation oder zum realen Zustand des Automower 580.
- Zwei getrennte Dockbeobachtungen um 03:29 und 03:30; erst die zweite reicht
  für die simulierte Stationsbestätigung. Die Simulation setzt voraus, dass
  die Unterdrückung des Ursprungstermins bereits um 03:05 bestätigt ist.
- Ein notwendiger Lauf mit fünf Zonen à 20 und zwei Zonen à 30 Minuten: 160
  Bewässerungsminuten. Das sind Beispielwerte, keine Wasserbedarfsermittlung.
- Nutzerregel: Start frühestens 03:30, alle Zonen bis 08:00; ursprünglicher
  Start 04:30. Der native 160-Minuten-Lauf 04:30–07:10 ergibt 05:20 als
  reinen spätesten Start. Jeder Lauf behält 150 Minuten Trocknung nach dem
  letzten Zonenende.
- Unverschiebbare Sportbelegung 16:30–20:30; Puffer sind bereits enthalten.
- Ein neues Mähfenster muss mindestens 30 produktive Minuten ermöglichen.
  Vorziehen ist auf 120 Minuten begrenzt; mindestens zehn zusätzliche produktive
  Modellminuten sind für einen Vorschlag erforderlich. Das sind neue konservative
  **Offline-Einstellungen**, keine aktivierten Betriebsregeln.

| Kennzahl | Ursprungstermin | Einfache Laderegel | Vorausschauender Vergleich |
|---|---:|---:|---:|
| Wasserstart | 04:30 | 03:30 | 03:30 |
| Letztes Zonenende | 07:10 | 06:10 | 06:10 |
| Früheste modellierte Trockenfreigabe | 09:40 | 08:40 | 08:40 |
| Produktive Mähminuten | 642 | 702 | 702 |
| Gewinn zur modellierten Referenz | 0 | 60 | 60 |
| Heimfahrt | 16 | 16 | 16 |
| Tatsächlicher Zustand „Laden“ im Modell | 334 | 374 | 374 |
| Tatsächlicher Zustand „Geparkt“ im Modell | 448 | 348 | 348 |
| Nicht produktive Minuten, vereinigt | 798 | 738 | 738 |
| Wasserlaufzeit | 160 | 160 | 160 |
| Trocknungszeit | 150 | 150 | 150 |
| Sportbelegung mit Puffer | 240 | 240 | 240 |
| Minutensumme ohne Platzsperre/Wasser/Trocknung | 890 | 890 | 890 |
| Produktive Nutzung dieser 890 Minuten | 72,13 % | 78,88 % | 78,88 % |
| Modellierte Restenergie am Tagesende | 21,6667 % | 21,6667 % | 21,6667 % |
| Erfüllte / verpasste Wasserbedarfe | 1 / 0 | 1 / 0 | 1 / 0 |
| Im Modell festgestellte Sicherheitsverletzungen | 0 | 0 | 0 |

Die höhere Ladezeit in der optimierten Variante ist folgerichtig: 60 zusätzliche
Mähminuten verbrauchen Energie und benötigen im linearen Modell 40 zusätzliche
Lademinuten. Die günstigere Überlappung mit bestehenden Sperren senkt trotzdem
die vereinigte nicht produktive Zeit um 60 Minuten. Gleiche Restenergie am Ende
verhindert, dass der Tagesgewinn nur durch zusätzlichen Akkuverbrauch entsteht.

Die frühere Trockenfreigabe ist ein Sperrzeitpunkt; produktive Minuten werden
unabhängig davon aus dem vollständigen Energiemodell gezählt. Dieses ergibt 60
Minuten Tagesgewinn. Die
vorausschauende Suche verbessert diesen Wert nicht. Daher ist die einfache
Laderegel die begründete Wahl für einen späteren begrenzten Schattenvergleich.

### Konkrete produktive Tagesfenster

| Ablauf | Produktive Mähfenster in MESZ | Summe |
|---|---|---:|
| Ursprungstermin | 00:00–03:00; 09:40–12:40; 14:44–16:26; 20:30–23:30 | 642 min |
| Einfache Laderegel / Vorschau | 00:00–03:00; 08:40–11:40; 13:44–16:26; 20:30–23:30 | 702 min |

Der erste Ladevorgang bleibt in beiden Abläufen 03:04–05:04. In der Referenz
liegen nur 34 Minuten Wasserlauf darin, bei der Laderegel 94 Minuten. Wasser,
Trocknung, weitere Ladungen und unveränderte Belegung sind in der HTML-Ansicht
als getrennte Spuren sichtbar. Der Zeitregler zeigt Hauptzustand und alle
gleichzeitigen Sperren; „Geparkt“ während Trocknung wird nicht mit Laden verwechselt.

### Sensitivität der unbekannten Ladezeit

| Angenommene volle Ladezeit | Referenz produktiv | Bündelung produktiv | Gewinn |
|---|---:|---:|---:|
| 60 Minuten | 702 | 720 | 18 Minuten |
| 90 Minuten | 672 | 720 | 48 Minuten |
| 120 Minuten | 642 | 702 | 60 Minuten |
| 180 Minuten | 582 | 642 | 60 Minuten |

Dies ist eine deterministische Sensitivität, kein statistisches Konfidenzintervall
und keine Prognose von 18–60 Minuten im echten Betrieb. Mähbudget und alle übrigen
Annahmen bleiben dabei gleich; die Restenergie bleibt in jeder Vergleichspaarung
identisch. Ohne durchgehende Betriebsdaten ist die Sicherheit einer quantitativen
Liveaussage niedrig. Weitere Einflüsse wie Regelbedarf, Regen, Flächenleistung,
Ladealterung und zusätzliche Belegungen wurden nicht in präzise Ersatzwerte übersetzt.

## Sicherheits- und Persistenzvertrag des Offline-Modells

1. Ein Ladeereignis erzeugt keinen Wasserbedarf. Eine stabile ID und ein explizit
   bestätigter notwendiger Lauf samt freigegebenem Startfenster sind Pflicht.
2. Frische Mäher-, Bewässerungs- und Belegungsdaten, zwei getrennte Dockbeobachtungen,
   eigener bestätigter Parkzustand, Verbindung sowie Station-/Wegsicherheit müssen
   vorliegen. Eine Heimfahrt ist kein Docknachweis.
3. Die Belegungsqualität deckt Start bis Trocknungsende vollständig ab. Auch ein
   leeres Sperrenarray ersetzt diesen Nachweis nicht. Vor dem simulierten Start
   wird die aktuelle Belegung erneut geprüft. Kein Sporttermin wird verschoben.
4. Ein Vorschlag ist keine Ausführungsfreigabe. Unmittelbar vor der simulierten
   Absicht müssen die Mäher-Wiederanlaufsperre und die Unterdrückung des
   ursprünglichen Geräteprogramms bis über den relevanten Sperrzeitraum bestätigt sein.
5. SQLite speichert stabile Bedarfs-ID, Bedarf, unveränderlichen verschobenen
   Termin, Revision und Zustandsphase. `BEGIN IMMEDIATE` und Revisionsvergleich
   lassen nur eine simulierte Absicht zu. Es gibt keinen Lockablauf, der einen
   zweiten Versuch gestatten würde.
6. Restart, Neuplanung und der Ursprungstimer können die Bedarfs-ID nicht ein
   zweites Mal ausführen. Geänderte Bedarfseigenschaften werden zur Überprüfung
   abgewiesen. „Antwort verloren“ bleibt persistent unbekannt; ein neuer Status
   kann denselben Versuch bestätigen, löst aber keinen neuen Versuch aus.
7. Eine persistente Stoppsperre bleibt nach Restart/Neuplanung wirksam. Es gibt
   keine automatische Methode zu ihrer Aufhebung.
8. Jede Zone benötigt eigene Endnachweise. Verzögerungen verschieben `dry_until`
   ab dem letzten tatsächlichen Ende. Eine verkürzte Zone wird als unvollständig
   erfüllt markiert; ein fehlendes Ende bleibt unbekannt. Nachholen ist eine
   fachlich zu bewertende Restbedarfsentscheidung, keine automatische Vollwiederholung.
9. Physische Trocknungsfrist und Telemetrieunsicherheit sind getrennte Werte.
   Ein Abfragefehler verändert die bekannte Frist nicht. Ein aktueller Aus-Status
   nach langer Lücke beseitigt eine mögliche unbemerkte Bewässerung nicht.
   Auflösung benötigt vollständige Ereignishistorie, einen belastbaren Beweis
   unmöglicher Bewässerung oder eine explizite konservative Frist ab dem letzten
   möglichen Ende. Solange ein verspäteter Befehl möglich ist, ist auch dieses
   letzte mögliche Ende noch nicht begrenzbar.

Alle Modellzeiten werden als UTC-Zeitpunkte verarbeitet. Nicht existierende
lokale Uhrzeiten werden abgewiesen; bei doppelten Zeiten muss `fold=0/1`
angegeben werden. Sommerzeit-Tage haben in der Intervallrechnung 1.380 bzw.
1.500 echte Minuten. 150 Minuten Trocknung bleiben auch über beide Zeitwechsel
150 verstrichene Minuten.

## Verbleibende Risiken und Risikominderung

| Risiko | Wahrscheinlichkeit / Schaden vor Liveprüfung | Erkennung | Prävention | Wiederherstellung | Restrisiko |
|---|---|---|---|---|---|
| Angenommener Bedarf oder Zeitkorridor passt fachlich nicht. | Unbekannt / Rasenschaden oder Sportkonflikt. | Platzwartprüfung, Wasser-/Zonenprotokoll. | Ohne bestätigten Bedarf und Korridor keine Vorziehung; Laufzeit unverändert. | Referenzplan nach Geräteabgleich beibehalten. | Fehlende Sensorik erlaubt keine präzise Wasserbilanz. |
| Station oder Heimfahrweg wird von Zone getroffen. | Unbekannt / Geräteschaden, blockierte Heimfahrt. | Prüfung jeder aktiven Zone vor Ort. | Räumliche Bestätigung als harte Voraussetzung. | Wasser stoppen, Mäher sicher prüfen; kein automatischer Neustart. | EPOS-/Ventilfehler bleiben möglich. |
| Ursprungslauf wird trotz Vorziehung ausgelöst. | Unbekannt / Doppelbewässerung und Mähkonflikt. | Nachweis jeder Relais-Suspendierung und Ereignisjournal. | Persistente Bedarfs-ID plus tatsächliche geräteseitige Unterdrückung. | Sichere Parkierung; Zone und Restbedarf manuell abgleichen. | Offline-ID unterdrückt kein reales Hydrawise-Programm. |
| Antwort geht verloren; verspäteter Befehl trifft nach Ablauf einer Sperre ein. | Möglich / Wasser-Mäher-Konflikt. | Ungeklärte persistente Absicht und Gerätebeobachtung. | Nicht erneut senden; keine Freigabe allein nach Leaseablauf; Gerätewirkung zeitlich begrenzen, soweit API erlaubt. | Absicht mit tatsächlichem Zustand aufklären; Gerätepläne überprüfen. | Geräte-API ohne echtes Fencing/Abbruchnachweis kann Zentralfehler nicht völlig schließen. |
| Tatsächliches Zonenende liegt nach dem prognostizierten Ende. | Möglich / zu frühe Freigabe. | Ende jeder Zone erfassen. | 150 Minuten ab letztem tatsächlichem Ende; nicht nur ab Vorschlag. | Haltesperre verlängern und Rückkehr zu Automatik erneut prüfen. | Zeitlich endliche Gerätehaltesperre kann bei Zentral-/Netzausfall vorzeitig enden. |
| Modellierter Gewinn lässt sich nicht reproduzieren. | Erhöht / unnötige Komplexität, kein Nutzgewinn. | Reale produktive Minuten, Restenergie, Wasserbedarf und Sperrvereinigung vergleichen. | Einfache Regel zuerst, Mindestgewinn, stabile Reservation. | Optimierung deaktivieren; Referenzzeitplan nach Geräteabgleich. | Ein Beispieltagesgewinn ist keine Langzeiteinsparung. |

## Stufen und Abnahme dieses Bausteins

| Stufe | Enthalten / ausgeschlossen | Voraussetzung und Abnahme | Ereignisabdeckung und Abbruch | Rückfall / Freigabe |
|---|---|---|---|---|
| 1 Bestand | Quellcode und synthetische Ausgangsbilanz / kein angeblicher Log-Replay. | Installierten Zeitplan, Gerätescheduler und durchgehende Ereignisdaten nachweisen. | Vollständiger Bedarf und mindestens je ein Ladevorgang, Heimfahrt, Belegung, Wasserlauf. Fehlender Nachweis bleibt offen. | Referenz unverändert; keine Livefreigabe nötig. |
| 2 Fehler und Diagnose | Generator-/Herkunftsfehler im Schattenplan korrigiert; getrennte Ursachenbilanz / keine kürzeren Sperren. | 13 adaptive Regressionstests; identische Sperren bei fehlender Herkunft. | Generator, gemischte Sperren, unvollständige Herkunft und fail-closed-Eintrag. Jede entfernte echte Belegung ist Abbruch. | Schattenplan deaktivieren; Gerätesteuerung bleibt erhalten. |
| 3 Zustände und Anzeige | Gemeinsame Offline-Zeitleiste und persistente Intent-Phasen / keine installierte App-Seite. | 29 Offline-Tests; jede Minute bilanziert; unbekannte Zustände bleiben unbekannt. | Restart, Stoppsperre, API-Lücke, mehrere Scheduler, unbekannte Antwort. | Artefakt/Modul entfernen; keine geplanten Geräteaktionen existieren. |
| 4 Simulation | Referenz, eine Laderegel und vorausschauender Vergleich / keine Bewässerungsreduktion. | Gleicher Bedarf, gleiche Belegung/Energie; 642/702/702 Minuten reproduzierbar; gleiche Restenergie. | Beiderlei DST-Wechsel, Mitternacht, Teilbewässerung, spätes Ende, neue Belegung. Sicherheitsverletzung oder verpasster Bedarf beendet den Versuch. | Nur neue Offline-Einstellungen deaktivieren. |
| 5 Produktiver Schattenvergleich | Später echte Daten read-only auswerten / weiterhin keine Gerätebefehle. | Fachlich bestätigte Zeitkorridore, Standortprüfung, stabile IDs, gemeinsame Rohbelegung und Monitoring. | Mindestens 14 Tage **und** zehn relevante Lade-/Bewässerungsgelegenheiten einschließlich Ausfall-/Änderungsabdeckung; länger, wenn Ereignisse fehlen. Instabile Reservierungen oder unsichere Daten stoppen Empfehlungen. | Schattenvorschläge deaktivieren; Freigabe der Dateneinbindung/Beobachtung erforderlich. |
| 6 Begrenzter Pilot | Ein genehmigter Bedarf auf geprüften Zonen unter Aufsicht / kein Flächenrollout. | Separate geprüfte Live-Anbindung mit bestätigter Originalterminunterdrückung, Haltesperre, Notfallweg und physischem Endnachweis. | Mindestens drei beaufsichtigte vollständige Abläufe plus gezielte Wiederherstellungsübung ohne riskante Gerätefehlinjektion. Jede unklare Wirkung, Doppelbewässerung oder Platzkollision bricht ab. | Geräte sofort sicherstellen, offene Befehle klären, lokale Programme und Referenzbedarf abgleichen; ausdrückliche menschliche Livefreigabe nötig. |
| 7 Erweiterung | Später bestätigte Bedarfe schrittweise aufnehmen / keine selbsttätige Änderung verbindlicher Regeln. | Beobachtete Vorteile ohne verschlechterte Versorgung und ohne ungeklärte sicherheitskritische Ereignisse. | Mindestens vier Wochen sowie Regen-/Belegungsänderung, Neustart und Ausfallwiederkehr abdecken; Saisonabhängigkeit separat bewerten. | Programm- und Intent-Abgleich vor Rückkehr zum Basistakt; verantwortliche Freigabe pro Erweiterung. |

Der nächste konkrete Schritt ist die fachliche Bestätigung der realen Bedarfs-
und Zeitkorridore sowie der Stations-/Wegsicherheit und das Nachweisen der
Originalterminunterdrückung. Erst danach ist eine getrennte read-only
Schattenintegration sinnvoll. Ein Livepilot erfordert zusätzliche Entwicklung
und eine ausdrückliche Freigabe; diese Offline-Bibliothek enthält dafür bewusst
keinen Schalter und keinen Geräteadapter.
