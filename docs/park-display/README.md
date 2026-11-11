# Stationsanzeige bei manueller Mähpause

Am 11.11.2026 zeigte die App weiterhin „Parken angefordert“, obwohl die
Steuerung die Stationsposition seit 10.11.2026, 18:10 Uhr (Europe/Berlin)
registrierte. Der Mäher meldete `PARKED_IN_CS`, `RESTRICTED`, Akku 100 % und
keinen Gerätefehler. Sein Gerätemodus lautete `MAIN_AREA`.

Die Anzeige verlangte zusätzlich `HOME` und eine frische Meldung. Die
Ergänzung in `ui/platzpflege/park-display.js` zeigt bei aktuellen Stationsdaten
„Mäher in Station“. Bei älteren Daten zeigt sie „Zuletzt in Station gemeldet“
mit dem Zeitpunkt der Meldung. Fehlende oder ungültige Zeitstempel werden
nicht zu einer aktuellen Bestätigung umgedeutet.

Die Ergänzung projiziert ausschließlich Titel und Beschreibung der Anzeige.
Alle Bedienfreigaben, Bestätigungstoken, Statuswerte und Steuerungsregeln
bleiben erhalten. Laufende oder unklare Anforderungen sowie vorrangige
Störungs- und Bewässerungshinweise behalten ihre Anzeige. Das Versandjournal
wird nicht verändert; eine gemeldete Stationsposition erteilt keine
Startfreigabe und quittiert keinen unklaren Gerätebefehl.

210 JavaScript-Tests bestanden, darunter acht neue Regressionsfälle.
Die tatsächliche Appack-Vorlage wurde mit simulierten Antworten bei 320 und
390 Pixel Breite geprüft: keine JavaScript-Fehler, kein Aktions-POST,
unveränderte Freigaben. Anschließend wurde ausschließlich der Anzeigeblock
in die bestehende Vorlage eingefügt. Die vorhandene Quelle wurde erhalten.
CMS-Rücklesen und öffentlicher Renderer (HTTP 200) bestätigen die Änderung.

`delivery-proof.json` enthält den Auslieferungsbeleg; `evidence.json` die
minimierten Zustandsdaten zur Diagnose. Der Mäher blieb manuell geparkt.
Es gab keine Azure-Bereitstellung, keine Änderung der Automatikfreigabe und
keinen Gerätebefehl zur Prüfung.
