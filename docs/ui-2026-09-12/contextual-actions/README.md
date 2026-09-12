# Zustandsabhängige Bedienung – 12. September 2026

## Ergebnis und Umfang

Die Vorlage unterscheidet jetzt eine Bedienberechtigung von einer im aktuellen Zustand sinnvollen Aktion. Übersicht, Mäher-Unterseite, Husqvarna-Vorbereitung und Bestätigungsdialog verwenden dieselbe Entscheidung. Kein Azure-Deployment, keine geänderten Sicherheitsgrenzen und keine Gerätebefehle bei Entwicklung oder Prüfung.

Ausgangspunkt: `b88dd74` / `fix/stable-manual-stop-20260912`. Aktive Quelle: `trainer-access-20260910/appack-platzwart-dashboard.html`, identische `.txt`-Kopie; Designquelle `ui/platzpflege/design.js`. Der vollständige bisherige CMS-Inhalt wurde vor dem Einsetzen mit dem Git-Ausgangsstand verglichen und lokal gesichert.

## Ursachen und korrigierte Regeln

| Zustand / Kombination | Passende Aktion und Beschriftung |
|---|---|
| STOP am Mäher / ausgeschaltet, auch bei älterer Meldung | Keine Start-, Park- oder Automatikfreigabe. Vor Ort freigeben; keine zweite Startzeitkarte. |
| Mäht / fährt auf den Platz | „Mäher parken“. Kein zweiter Start. |
| Fährt bereits zur Station | „In Station lassen“ setzt eine dauerhafte Parksperre; Dialog erklärt die Wirkung bis zur Freigabe. |
| Lädt / automatisch in Station | „Mäher starten“, sofern vom Backend zugelassen; „In Station lassen“ setzt eine manuelle Parksperre. |
| Bereits manuell geparkt | Kein erneutes Parken. Start bzw. „Automatik einschalten“ nach Backendfreigabe. |
| Neue Bewegung nach bestätigter manueller Parksperre | Schutz durch erneutes Parken bleibt erreichbar. |
| Start angefordert / unbestätigt / Husqvarna-Start vorbereitet | Kein Doppelstart. Schutz durch Parken bleibt erreichbar, soweit der Mäher verbunden und nicht physisch gestoppt ist. |
| Parkanfrage läuft bereits | Kein zweiter Parkauftrag. |
| Bewässerungskonflikt mit manueller Freigabe | „Mähen oder bewässern“ – dieselbe Beschriftung im Dialog. Pflichtbestätigungen bleiben erhalten. |
| Training, Spiel oder Trockenzeit | Keine selbst erfundene Freigabe: bestehende Backendberechtigung und Bestätigungen gelten unverändert. |
| Getrennte Verbindung / aktive Störung | Kein Start. Eine vom Backend zugelassene schützende Parkaktion wird durch eine Störung allein nicht entfernt. |
| Wasser läuft oder steht unmittelbar bevor | Kein neuer Gesamt- oder Zonenstart. Zonenmeldungen bleiben sichtbar. |
| Bewässerung vorbereitet, noch kein Wasser | „Bewässerung abbrechen“. |
| Genau eine Zone läuft, aktuelle Daten | „Direkt beenden“ oder „Nach dieser Zone“, jeweils mit eigener Berechtigung. |
| Zwischen Zonen / Start reserviert / Stoppen läuft | „Direkt beenden“, keine vermeintlich laufende Zone. |
| Trockenzeit / abgeschlossener Lauf / unbekannte Phase | Kein unpassender Bewässerungs-Stopp. Zulässige Phasen entsprechen exakt der Backendannahme. |
| Planänderung läuft | Keine doppelte Änderung; Bedienelemente erscheinen nach Abschluss wieder. Einzelberechtigungen werden getrennt ausgewertet. |
| Pause / Aussetzen / angepasster Lauf zurücknehmen | „Bewässerung fortsetzen“ / „Aussetzen zurücknehmen“ / „Änderung zurücknehmen“. |
| Schnitthöhe / Klingen | Keine unveränderte Schnitthöhe erneut speichern; bestehende Sperren und Bestätigung bleiben bestehen. Klingenreset nur bei Laufzeit, Berechtigung und ohne laufenden Reset. |

Hauptfehler: `manualControl.canPark` bedeutete eine technische Berechtigung, wurde aber unmittelbar als Anzeigeentscheidung verwendet. Weitere belegte Fehler: fehlende Prüfung auf aktives/unmittelbar bevorstehendes Wasser vor einem neuen Lauf, zu breite Stopp-Phasenprüfung, eine vermeintlich aktive Zone im Stopp-Dialog und gekoppelte Berechtigungen für Gesamt- und Zonenstarts. Der unabhängige Review bestätigte diese Befunde anhand der Backendannahme in `platzwart_console.py` und `mower/full_failsafe.py`.

## Nachweise

- [Tests](tests.txt): **215 bestanden**, keine fehlgeschlagen. Darin **18.480 Mäherkombinationen + 528 Bewässerungskombinationen = 19.008 synthetische Kombinationen**. Geprüft werden konkrete Invarianten, zusätzlich benannte Normalfälle und Zustandswechsel; dies ist keine Behauptung vollständiger Erfassung aller möglichen Eingaben.
- `tests/test_appack_action_combinations.js`: STOP/OFF, Verbindung, Bewegung, manuelle Sitzungen, Wasser, Training, Spiele, Trockenzeit, Rechte, lokale/serverseitige Anfragen, Rückkehr ausgeblendeter Aktionen und getrennte Fähigkeiten.
- `tests/test_appack_manual_control.js`: STOP während eines bereits geöffneten Dialogs sendet nichts; Bestätigungen, eingefrorener Kontext und bestehender Anfragevertrag bleiben geprüft.
- [Browserprüfung](browser-checks.json): echte Vorlage mit synthetischen Daten, 390 Pixel, kein seitlicher Überlauf in geprüften Ansichten. Laufende Zone / Zonenpause / Heimfahrt / Mäher-Unterseite geprüft. STOP zusätzlich als DOM und in 390-Pixel-Einbettung geprüft: keine Mäheraktionen, nur Meldung und Navigation.
- Reproduzierbare Vorschauen: `python -m scripts.build_action_combinations_preview`; danach `preview/index.html`. CSP blockiert das Netzwerk, der Testadapter lehnt POST ab. Kein echter Gerätezugriff.
- Separater Review mit kleinerem Modell: initiale Befunde integriert; abschließender Diff-Review ohne neuen schwerwiegenden Befund. Anschließend ergänzend defensive Anzeigeprüfungen für getrennte Verbindung und Fehlerzustände; gesamter Testlauf erneut grün.
- `scripts/sync_platzpflege_design.py --check`: Designblöcke und CMS-Kopie synchron.
- [Auslieferungsnachweis](publication.json): öffentlicher Abruf am **12.09.2026, 12:41:56 Uhr Berlin**, HTTP 200; 15 relevante Funktionsdefinitionen stimmen mit der geprüften Vorlage überein. Vollständiger Inhalt vor dem CMS-Speichern per Clipboardvergleich verifiziert. SHA-256 der LF-normalisierten Vorlage: `027b7c98c6f742b6f1c5ca3a190d618b5f7d06ac2e96d0fa710a920672da5509`.

## Grenzen und Betrieb

**Analysiert, umgesetzt, automatisiert getestet, im Browser simuliert und in Appack veröffentlicht.** Kein neuer Geräteablauf und keine native Telefonabnahme nach dieser Veröffentlichung nachgewiesen. Bildschirmaufnahme war in dieser Browsersitzung zuvor nicht verfügbar; hier sind reproduzierbare Ansichten und DOM-Nachweise dokumentiert, keine neuen Screenshots behauptet.

Die App kann einen vom Backend nicht unterstützten Befehl nicht anbieten. Insbesondere nimmt das bestehende Backend einen Bewässerungsstopp nur in `PLANNED`, `SUSPENDING`, `READY`, `START_RESERVED`, `RUNNING`, `STOPPING` an. Ein ausschließlich extern in Hydrawise gestarteter Lauf ohne zentralen Ablauf oder eine unbekannte Phase bleibt deshalb eine gesonderte Backend-/Betriebsaufgabe; bei diesem Fall muss vor Ort bzw. in Hydrawise beendet werden. Diese Änderung erweitert keine Gerätebefehle und stellt dafür keine Wirksamkeit in Aussicht.

Ein sichtbarer Button ist keine Zusage, dass ein Gerät den Befehl ausführt. Serverseitige erneute Prüfung, Pflichtbestätigung, Sperren und Rückmeldung bleiben maßgeblich. Die 180-Sekunden-Grenze für Sicherheitsentscheidungen wurde nicht verändert.

**Auf dem Telefon:** SSV-App vollständig schließen und neu öffnen. „Aktualisieren“ lädt Gerätedaten, aber nicht zwingend die neue HTML-/JavaScript-Vorlage. Abnahme: bei STOP kein Parkbutton; nach physischer Freigabe passende Aktionen; auf Heimfahrt korrekte Parksperre; bei laufendem Wasser passende Stopp-Auswahl. Dafür keine zusätzliche Geräteaktion allein als Test auslösen.

Rückfall: gesicherte bisherige `Platzpflege.tpl` einsetzen oder HTML aus `b88dd74` wiederherstellen. Dies ändert nur die Darstellung; bereits angeforderte Geräteaktionen sind davon unabhängig. Die neue Vorlage selbst hat keine Gerätebefehle versendet.
