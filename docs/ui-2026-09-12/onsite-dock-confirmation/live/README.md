# Veröffentlichung am 12.09.2026

Die ausdrückliche Anweisung des Nutzers lautete: „Bitte live setzen“. Veröffentlicht wurden die zusätzliche Stationsbestätigung für manuelle Bewässerung bei geeignetem STOP-Zustand sowie die Bereinigung nachweislich abgeschlossener Bewässerungsaufträge. Die konkrete Mäherfehlermeldung war bereits zuvor veröffentlicht und bleibt enthalten.

## Lieferumfang und Versionsnachweis

| Ebene | Nachweis |
|---|---|
| Repository | Branch `fix/stopped-dock-irrigation-20260912`, [PR 85](https://github.com/Rohdeo87/ssv53-heimspiele/pull/85) |
| Endgültiger Steuerungsquellstand | [`4fe90d7532bde36c6bd00864e6cdc351bffc1442`](https://github.com/Rohdeo87/ssv53-heimspiele/commit/4fe90d7532bde36c6bd00864e6cdc351bffc1442) |
| Ausgangsinstallation | [installed-before.json](installed-before.json): Running, 16 Funktionen, alle 71 Dateien des bisherigen Pakets bytegenau bestätigt |
| Erstveröffentlichung | [package.json](package.json), [installed-flags-off.json](installed-flags-off.json), [installed-active.json](installed-active.json): 72 Dateien bytegenau, neues Modul enthalten |
| Endgültiges Paket | [package-clock-fix.json](package-clock-fix.json): ZIP `c5bb19a88021b0276166038725ca95e24368df3d612e9ca6e6ba833ac9519d79`, Manifest `88e418f5c967991d6f842ad732e26ab223ecfd6be24709140816e5cd77d26264` |
| Endgültige Installation | [installed-final.json](installed-final.json): Running, 16 Funktionen, alle 72 Dateien bytegenau bestätigt, bestehende Schutzkonfiguration unverändert |
| Appack | `Platzpflege.tpl`, Template-ID `6a86ab6c4b3c829dd60de9b7`, gespeichert am 12.09.2026 um 15:24 Uhr; LF-normalisierter Quellhash `5d562aaf7644e1aac3c15e0dd76e453c02a91e247465c0605b7d4d5347b48e31` |
| Öffentliche Auslieferung | [Aktivierungsnachweis](activation.json): geänderte Funktionen, kompletter Bestätigungs-Handler und Checkbox gegen den [öffentlichen Renderer](https://appack.de/rest-api/drender/6a86ab6c4b3c829dd60de9b7) abgeglichen |
| Aktivierung | 15:26 Uhr: ausschließlich `IRRIGATION_ONSITE_DOCK_CONFIRMATION_ENABLED=true` und `IRRIGATION_TERMINAL_CLEANUP_ENABLED=true`; alle anderen Einstellungen einschließlich Slotbindung unverändert |
| Abschlusskontrolle | [final-flags-and-appack.json](final-flags-and-appack.json): beide Optionen weiterhin aktiv, keine weitere Einstellungsänderung, Appack-Inhalte nochmals bestätigt |

Das Paket wurde auf dem tatsächlich installierten Archiv aufgebaut. Gegenüber diesem Ausgangsstand ändern sich nur `mower/full_failsafe.py`, `mower/state.py`, `platzwart_console.py`, das zusätzliche `mower/onsite_dock_proof.py` und das Manifest. Beim Import des ausgepackten Pakets waren Netzwerkzugriffe gesperrt; alle 16 Funktionen wurden erfolgreich importiert und die Paketdateien verifiziert. Die spätere Zeitkorrektur ändert gegenüber der Erstveröffentlichung nur `mower/full_failsafe.py` und das Manifest. ZIP-Dateien liegen lokal in `dist/`; sie werden nicht als Binärdateien in Git abgelegt. Der Paketbericht beschreibt den Bauzeitpunkt (`deployed=false`); den späteren Rollout belegen die getrennten Installationsdateien.

Die bestehende Stapelstruktur der PRs wurde beibehalten. Es erfolgte kein Merge fremder bzw. vorausgehender Branches und kein vollständiger Rollout unbeteiligter Komponenten.

## Bei der Nachkontrolle gefundener Zeitvergleich

[cleanup-observations.json](cleanup-observations.json) zeigt beispielsweise:

- Zyklusbeginn: `13:29:00.007147Z`.
- Erfolgreiche Hydrawise-Beobachtung: `13:29:01Z`.
- Speicherung: `13:29:03.324823Z`, Zustand weiterhin `COMPLETE_HOLD`.

Die Bereinigung verglich die frisch gelesene Quelle mit dem früheren Zyklusbeginn. Die Quelle erschien dadurch rund eine Sekunde zukünftig, obwohl sie aus der gerade abgeschlossenen Abfrage stammte. Die Bereinigung wurde zu Recht für ihren falschen Zeitbezug abgelehnt.

Die Korrektur liest die injizierbare Uhr unmittelbar vor dieser Bereinigungsprüfung. Sie akzeptiert nur eine Uhrzeit mit Zeitzone zwischen Zyklusbeginn und Ablauf der vorhandenen maximalen Datenaltergrenze. Ungültige Uhren, Ausnahmen, verspätete Zyklen und wirklich zukünftige Quellen verhindern die Bereinigung weiterhin. Die globale Planungszeit, Trocknungsfrist, STOP-Behandlung und Geräte-Sendegrenzen werden nicht verändert. Der Schutz-PARK bleibt im selben Zyklus erreichbar.

Die präzise zeitliche Zuordnung ist wichtig: Die Erstversion bereinigte den Auftrag schließlich bereits um 15:32 Uhr in einem zufällig ausreichend schnellen Zyklus, in dem die Hydrawise-Sekunde noch vor dem Zyklusbeginn lag. Das [Bereinigungsereignis](cleanup-events.json) belegt `persisted=true`, `phase_after=null`, erhaltene Trocknungsdaten und `command_sent=false`. Es handelte sich somit um eine von der Abfragedauer abhängige Verzögerung, keine unabänderliche Sperre. Die ergänzende Zeitkorrektur beseitigt diese Abhängigkeit für künftige Abschlüsse; ein weiterer realer Abschluss mit dieser Ergänzung wurde noch nicht beobachtet.

Die [Zyklen um 15:35 und 15:36 Uhr](cycles-final.json) laufen mit dem endgültigen Manifest. Beide zeigen keinen verbleibenden Bewässerungsauftrag, den Mäher `MOWING` ohne Fehler und alle sieben Zonen frisch und vollständig ausgeschaltet. Beide melden `command_sent=false`. Der Entscheidungsgrund `MANUAL_OR_ERROR_HOLD` bezeichnet hier die unveränderte Behandlung eines externen/manuellen Betriebszustands; er ist kein Nachweis eines neuen Gerätefehlers. Die Prüfung dieser Veröffentlichung hat den bestehenden manuellen Modus nicht zurückgesetzt.

## Tests und unabhängige Prüfung

- Vor Veröffentlichung: 338 Backend-/Pakettests und 81 Subtests sowie 227 Appack-Tests bestanden; siehe [Entwicklungsbericht](../README.md).
- Nach Zeitkorrektur: [215 Tests und 35 Subtests](cleanup-clock-tests.txt) für Abschlussbereinigung, vollständigen Fail-safe-Ablauf, Stationsnachweis und Befehlsjournal bestanden.
- Zusätzliche Fälle: echte Abfragedauer, Quelle nach tatsächlichem Prüfzeitpunkt, überlanger Zyklus, rückwärts laufende/zeitzonenlose/fehlende Uhr und Uhr-Ausnahme. Bestehende Tests halten unbestätigte Sendungen, aktive Zonen und die volle Trockenfrist weiterhin gesperrt.
- Unabhängiges Review der Zeitkorrektur: kein Blocker im geprüften Umfang. Als kleine verbleibende Testlücke wurden der exakte obere Zeitgrenzwert und eine gültige Uhr mit anderer UTC-Verschiebung benannt; die Implementierung vergleicht zeitzonenbewusste Zeitpunkte.

## Bedienung und verbleibende Abnahme

Bei geeignetem, fehlerfreiem STOP-Zustand kann ein berechtigter Nutzer die Bewässerung mit der zusätzlichen Bestätigung „Ich sehe den Mäher in der Station“ anfordern. Die Bestätigung gilt für genau diesen Auftrag. Ohne die passende Backendfreigabe erscheint diese Möglichkeit nicht; ein Mäherfehler bleibt ein Sperrgrund. Der Mäher wird für die Bewässerung nicht aus seinem STOP freigegeben.

Kein Geräte-Testlauf, kein automatisierter Klick auf einen Wasserstart und keine erfundene Stationssichtung wurden zur Veröffentlichung ausgeführt. Der geschützte Statusaufruf wurde ohne angemeldete Platzwart-Sitzung nicht umgangen. Die ausgelieferten Funktionen, Konfiguration und Automatikzyklen werden getrennt von einer erfolgreichen Bedienung auf dem Nutzerhandy und einem physischen Wasserlauf bewertet.

Die physische Abnahme steht noch aus: geeigneten STOP im Dock vor Ort sehen, genau einen gewünschten Bewässerungsauftrag bestätigen, Wasserstart und -ende beobachten und STOP-Erhalt kontrollieren. Eine kontrollierte Prüfung des Schutzstopps benötigt eine gesondert betreute Situation. Ein Start direkt am Gerät oder in Husqvarna wird erst nach einer Rückmeldung erkannt; vor einem solchen Start muss Wasser beendet sein.

## Rückfall

Zunächst neue manuelle Bewässerungsanforderungen beenden und mögliche laufende bzw. angenommene Sendungen samt geräteinternen Zeitplänen abgleichen. Das Ausschalten der beiden neuen Optionen allein beendet keinen Wasserlauf. Bei aktivem oder unklarem Auftrag ist zuerst das Wasserende nachzuweisen; Folgeaufträge sperren und Trockenfristen erhalten. Keine alte Stationsbestätigung rekonstruieren.

Nach sicherem Abschluss kann das zuvor verifizierte `dist/manual-start-release.zip` (SHA-256 `f16fd90869b4a79564dc6ab74ee5607554992eba35e592a5a660bb9eebb8a627`, Manifest `cc36ebb779787ee97d0c499da61410cf9810505fb384400e1379c3262760a89c`) wiederhergestellt werden. Die lokale Appack-Sicherung ist `appack-before.tpl`, alternativ der kanonische Stand `b09094a:appack-platzwart-dashboard.html`, LF-Hash `c4d50cda346fbfb10aa08543de57dbba066420cc3ebeb2b63b6d1c72bac7d35a`. Der vorige Zustand der beiden neuen Einstellungen war jeweils nicht gesetzt. Übrige produktive Konfiguration erhalten. Auch nach Rückfall Dateien, Hostzustand, persistierte Aufträge und tatsächliches Geräteverhalten erneut abgleichen.
