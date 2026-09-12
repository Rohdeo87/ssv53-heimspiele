# Automatische Übernahme eines manuellen Starts

Der Nutzer hat am 12.09.2026 bestätigt: Konfliktfreie Starts in App, am Gerät und über Husqvarna werden nach bestätigter Abfahrt ohne weitere Bestätigung in die normale Automatik übernommen. Explizite Ausnahmen bleiben bis zur nächsten Ladefahrt gültig. Manuelle Parksperren und physischer STOP dürfen dabei nicht aufgehoben werden. Umsetzung und Veröffentlichung sind ausdrücklich beauftragt.

## Verhalten

| Situation | Verhalten |
|---|---|
| Frische, fehlerfreie Fahrt auf freiem Platz; Wasser aus und Trockenfreigabe vorhanden | Bestehenden Lauf in die zentrale Planung übernehmen; kein zweiter START |
| START aus App wurde angenommen, aber Abfahrt ist unbestätigt | Vorhandenes Befehlsjournal und Bestätigung abwarten; keine Übernahme als Ersatz für den Sendebeleg |
| Bereits bestätigte Belegungs-, Trockenzeit- oder Wasser-Ausnahme | Sitzung und genaue Ausnahme unverändert bis zur nächsten bestätigten Ladefahrt; danach normale Regeln |
| Neue Belegung, laufendes/unklares Wasser oder unvollständige Importdaten | Keine Übernahme; bisherige Schutzpfade bleiben erreichbar |
| Manuelle PARK-Sitzung | Bleibt bis ausdrücklicher Freigabe bestehen, auch wenn der Mäher dazwischen fährt |
| STOP bzw. neuer externer Parkbefehl nach beobachteter Übernahme | Besitz vor frühen Sicherheits-Rückgaben widerrufen; persistierte Sperre verhindert Wiederanlauf allein durch einen späteren Idle-Status |
| Neue frische Abfahrt nach Gerätefreigabe | Darf nach allen normalen Prüfungen übernommen werden; alte Meldungen vor der Sperre genügen nicht |
| Heimfahrt zum Laden nach beobachteter Übernahme | Wird nicht durch einen weiteren START unterbrochen; späterer Start im Dock läuft durch alle normalen Prüfungen |
| Gespeicherte Rückkehrfrist erreicht | Vorhandenen protokollierten PARK-Pfad benutzen; geänderter Kalender verlängert diese bereits gespeicherte Frist nicht stillschweigend |

Der neue Schalter `MOWER_AUTOMATIC_TAKEOVER_ENABLED` ist im Code standardmäßig aus. Er verändert weder Zeitpuffer noch Trockenzeiten oder automatische Bewässerungszeiten.

## Umsetzung

`mower/automatic_takeover.py` prüft den passenden Mäher, vollständige normale Freigaben, ein eindeutiges aktives Zielgebiet, tatsächliche aktuelle Fahrt, zulässige Herstellerzustände, eindeutiges Befehlsjournal und das Fehlen manueller Ausnahmen. Die bestehende Bestätigungslogik wird für diese Prüfung wiederverwendet. Bekannte STOP-/PARK-Zustände, unbekannte Werte und ungeklärte Sendungen sind keine Grundlage zur Übernahme.

Die Übernahme erfolgt im vollständigen Controller hinter den Schutzpfaden und vor der abschließenden Behandlung des laufenden Mähauftrags. Sie wird mit der bestehenden bedingten Zustandsspeicherung persistiert; eine konkurrierende Änderung darf nicht überschrieben werden. Eine aktive Sitzung ohne Ausnahmen wird dabei als beendet archiviert. Eine normale Übernahme erzeugt keinen Geräte-Sendebeleg und verändert keinen historischen Startzeitpunkt.

Eine fremd gestartete Fahrt besitzt **keinen durch die Übernahme installierten geräteinternen Endzeitpunkt**. Dafür werden `continuous_mowing_observed_takeover` und die separate geplante Rückkehrfrist gespeichert. Der Controller überwacht diese Frist und parkt bei Fälligkeit; der normale geräteseitig begrenzte Startpfad wird nach dem Laden wieder verwendet. Die Anzeige nennt auf der Übersicht „Mäher mäht“, bewahrt Fortschritt und nächste wichtige Uhrzeit und zeigt eine gültige Ausnahme ohne unnötige Warnfarbe. Echte Konflikte behalten ihren Vorrang.

## Grenzen und Risiken

- Cloud- oder Geräteausfall kann die Ausführung eines PARK verzögern oder verhindern. Die Übernahme kann keinen bereits extern gestarteten Lauf nachträglich ohne Gerätebefehl mit einem nativen Endzeitpunkt versehen. Der Bericht und die Telemetrie unterscheiden deshalb eine geplante Rückkehrfrist ausdrücklich vom geräteintern installierten Timer. Die bereits vorhandene Bewässerungsverriegelung bleibt bestehen.
- Ein direkt am Gerät oder bei Husqvarna ausgelöster Start wird erst nach seiner Rückmeldung erkannt. Diese Änderung ist keine Verriegelung vor einer externen Abfahrt.
- Aus einem späteren Idle-Status allein wird eine beobachtete manuelle Sperre nicht aufgehoben. Eine ausdrückliche neue App-Startanforderung oder eine frischere tatsächliche Abfahrt muss erneut alle Freigaben durchlaufen.
- Die Husqvarna-[API-Dokumentation](https://developer.husqvarnagroup.cloud/apis/automower-connect-api) ist JavaScript-basiert. Die vorhandene Hersteller-Spezifikation in der lokalen Referenz nennt `NOT_ACTIVE`, `FORCE_PARK` und `FORCE_MOW`; ein Force-Mow-Override ist zeitlich begrenzt und kein Beleg zentraler Befehlsherkunft. Es werden keine neuen Herstellerendpunkte verwendet.

## Abnahme und Rückfall

Die Tests verwenden Ersatzsender und gesperrte echte Netzwerkbefehle. Relevante Fälle umfassen beide App-/Husqvarna-Wege, tatsächliche externe Abfahrt, Frische/Fremdgerät/Fehler, Ausnahmen, Wasser- und Belegungskonflikte, fehlende Importdaten, STOP, externe Parksperren, Neustart, Rückkehrfrist, Ladefahrt und konkurrierende Speicherung. Veröffentlichung, aktivierte Einstellung und beobachtete Liveübernahme werden separat dokumentiert.

Entwicklungsabnahme: **335 Backendtests und 41 Unterfälle**, **228 App-Tests** erfolgreich; Ausgaben stehen in `backend-tests.txt` und `appack-tests.txt`. Ein unabhängiges Review mit gpt-5.6-luna prüfte insbesondere Wiederanlauf, Ausnahmeende, STOP/PARK und Ablauf der Rückkehrfrist. Die gefundene kurzzeitig widersprüchliche Sitzungsrückmeldung wurde korrigiert: Persistierter Zustand und Antwort melden bereits im Übernahmezyklus `ENDED`; beide App-/Husqvarna-Fälle prüfen dies. Ein erster Aufruf mit einem nicht vorhandenen Testpfad wurde durch den vollständigen erfolgreichen Lauf mit `test_mower_state.py` ersetzt. Ein Temp-Verzeichnisproblem im separaten Review war mit explizitem Testverzeichnis nicht reproduzierbar.

Die lesende Ausgangsprüfung bestätigt 72 installierte Dateien des bisherigen Pakets und 16 registrierte Funktionen (`installed-before.json`). Um 16:26 Uhr Ortszeit meldete der Mäher `MOWING`, Fehler 0, Wasser in allen sieben Zonen aus; der bisherige Controller behandelte die Fahrt als fremd gestartet und sendete keinen Befehl (`cycles-before.json`). Diese Ausgangsmessung ist noch kein Nachweis der neuen Übernahme.

Rückfall: zuerst den neuen Schalter ausschalten, bestehende übernommene Aufträge und ihre Rückkehrfristen abgleichen und erforderliche Rückfahrt sicher abschließen. Ausschalten hebt weder laufende Wasseraufträge noch Parksperren auf. Bevor auf das ältere Paket zurückgestellt wird, darf kein offener beobachteter Übernahmeauftrag mehr vorliegen: Der alte Code kennt dessen separate Frist nicht. Ausgangsarchiv ist `dist/onsite-dock-clock-release.zip`, SHA-256 `c5bb19a88021b0276166038725ca95e24368df3d612e9ca6e6ba833ac9519d79`; bisherige App-Vorlage entspricht `23d42ac:appack-platzwart-dashboard.html`, LF-Hash `5d562aaf7644e1aac3c15e0dd76e453c02a91e247465c0605b7d4d5347b48e31`. Geräte-/Auftragszustand, Host, Dateien und Einstellungen nach jedem Rückfall erneut prüfen.
