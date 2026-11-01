# T-Shirt-Vormerkungen: automatische Benachrichtigungen

`reservation_notifications.py` prüft minütlich die gespeicherten Vormerkungen
im bestehenden Appack-Workbook. Neue Vormerkungen erhalten zwei getrennte Mails:

- Besteller: Eingangsbestätigung mit Artikeln und Hinweis auf unverbindliche Vormerkung.
- Geschäftsstelle: neue Vormerkung mit Kontaktdaten, Artikeln und Verwaltungslink.

Die Geschäftsstelle wird serverseitig auf `info@ssv53.de` festgelegt. Browser und
Statusabfrage können keine Empfänger bestimmen und keinen Mailversand auslösen.
Die bestehende Abholbenachrichtigung bleibt ein eigener Verwaltungsschritt.

## Aktivierung und Grenzen

- `SSV53_RESERVATION_NOTIFICATIONS_ENABLED=true`
- `SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC`: Aktivierungszeitpunkt mit Zeitzone.
- `SSV53_RESERVATION_OFFICE_EMAIL=info@ssv53.de`

Maßgeblich ist der Appack-Serverzeitpunkt `_created`. Frühere Vormerkungen werden
nicht nachträglich angeschrieben. Der Timer bearbeitet höchstens sieben Tage alte
Vormerkungen und ignoriert Stornierungen. Bestehende SMTP-/Statusspeicher-Einstellungen
werden weiterverwendet; das Flag der bisherigen Abholmails wird nicht geändert.

Beide Zustellungen besitzen eigene atomare Claims im vorhandenen Statusspeicher.
Ein erfolgreich versendeter Kanal wird bei Teilfehlern nicht erneut verschickt.
Eindeutige SMTP-Fehler werden beim nächsten Lauf wieder versucht. Bei unklarem
Ergebnis nach der Übergabe an SMTP verhindert der Status `sending` einen möglichen
Doppelversand; solche Fälle müssen anhand des Mailservers geprüft werden.
`sent` bedeutet Annahme durch SMTP, nicht bestätigte Zustellung ins Postfach.
Die vorhandene Aufbewahrungsbereinigung umfasst auch die neuen Claims.

## Appack-Oberflächen

`reservation-status.js` nach dem vorhandenen Script in `Bestellverwaltung.tpl`
einbetten. Es fragt nur den GET-Endpunkt `reservation-notifications` ab und zeigt
den Versandstand für Besteller und Geschäftsstelle getrennt. „Versandstatus
aktualisieren“ ist eine reine Leseaktion. Vorhandene Abholaktionen bleiben erhalten.

Nach dem Speichern einer neuen Vormerkung erklärt `Shop.tpl`, dass eine
Eingangsbestätigung folgt und die Geschäftsstelle automatisch informiert wird.
Der Versand hängt weder von diesem Hinweis noch vom Besuch der Verwaltung ab.

## Prüfung und Bereitstellung

`tests/test_reservation_notifications.py` prüft beide Empfänger, alte Vorgänge,
Doppelversand, Teilfehler, unklare SMTP-Ergebnisse und Datenschutz der Statusantwort.
Die Tests verwenden einen simulierten Mailserver und versenden keine echten Mails.
Der Bereitstellungshelfer prüft SMTP mit Authentifizierung und NOOP ohne Mailversand,
bevor er aktiviert. Er ergänzt nur das neue Modul und dessen Registrierung im
aktiven Azure-Paket; alle übrigen Paketdateien bleiben bytegleich.
