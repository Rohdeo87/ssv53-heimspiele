# Aktivierungsstand: T-Shirt-Vormerkungen

Stand: **02.11.2026**. Serverversand und beide Appack-Ergänzungen sind live geprüft.

## Automatischer Mailversand ist live

Aktiviert am 02.11.2026 um **15:19:14 Uhr Europe/Berlin**
(`2026-11-02T14:19:14.892621+00:00`). Neue gespeicherte Vormerkungen erhalten:

- eine Eingangsbestätigung an die in der Vormerkung gespeicherte Kundenadresse;
- eine getrennte Meldung an **info@ssv53.de**, mit Artikeln und Verwaltungslink.

Der Timer prüft minütlich. Die vorhandene Abhol-Mail bleibt eine getrennte
Verwaltungsaktion; ihr Code und ihre Aktivierung wurden nicht geändert.

**Beide bisherigen Vormerkungen werden nicht nachträglich angeschrieben.**
Die Statusroute wurde für beide live geprüft: `enabled=true`, `applicable=false`.

Erfolgreiche Veröffentlichung:
https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/41460651610

Maschinenlesbarer Nachweis: [LIVE-NACHWEIS-2026-11-02.json](LIVE-NACHWEIS-2026-11-02.json).

## Geprüft

- 43 gezielte Python-Tests und 18 Untertests erfolgreich, einschließlich
  Empfänger, Teilfehler, Doppelversand, Altvorgänge und Erhalt fremder Einstellungen.
- Produktiver SMTP-Check mit Authentifizierung und NOOP erfolgreich;
  keine Testmail und keine Testvormerkung erzeugt.
- Neue Statusroute und Timer im laufenden Azure-Dienst registriert.
- Automatischer, aktivierter Timerlauf am 02.11.2026 um 15:20 Uhr nachgewiesen:
  keine Scan-, Datensatz- oder Versandfehler; keine E-Mails für alte Vorgänge.
- Alle nicht betroffenen Paketdateien bytegleich erhalten; `order_mail.py` unverändert.
- Die vom Nutzer um 14:52 Uhr vergebene Rolle „Storage Blob Data Reader“ funktioniert.
  Die reine Diagnoseidentität wurde nicht verändert.

Der Bereitstellungshelfer verwendet die Flex-Site-APIs, ermittelt versionierte
Pakete über die aktive Bereitstellung und setzt für die zwei ergänzten/geänderten
Quelldateien lesbare Linux-Dateirechte. Frühere Fehlversuche wurden vor Aktivierung
gestoppt beziehungsweise auf das vorherige Paket zurückgerollt.

## Appack-Anzeigen ebenfalls live

Beide Vorlagen wurden am 02.11.2026 über die bestätigte Appack-Anmeldung
bearbeitet, gespeichert, nach vollständigem CMS-Neuladen zurückgelesen und am
separaten Render-Endpunkt geprüft:

- `Shop.tpl`: Die einzelne Bestätigungszeile kündigt die Eingangsbestätigung
  und die automatische Information der Geschäftsstelle an. Der sonstige
  Quelltext bleibt unverändert.
- `Bestellverwaltung.tpl`: Das zusätzliche Script aus
  [reservation-status.js](reservation-status.js) zeigt die Versandstände für
  Besteller und Geschäftsstelle getrennt. Der bestehende Quelltext einschließlich
  Abholaktionen und Zugangskonfiguration bleibt bytegleich erhalten; nur die
  neuen Scriptzeilen wurden vom CMS-Editor auf CRLF-Zeilenenden normalisiert.

Liveprüfung mit beiden vorhandenen Vorgängen: zwei Bestellkarten, zwei
Versandanzeigen und jeweils der Hinweis, dass kein automatischer Nachversand
erfolgt. „Versandstatus aktualisieren“ erzeugt genau eine zusätzliche GET-Abfrage
(HTTP 200); kein Aufruf des Mail-Endpunkts. Die getrennten Kanalzustände wurden
zusätzlich in einer isolierten Browseransicht mit synthetischen Daten geprüft,
ohne eine Vormerkung anzulegen oder eine Mail zu versenden.

Nachweis mit Quelltext-Hashes, ohne Vorlageninhalt, Zugangsdaten oder Cookies:
[APPACK-LIVE-NACHWEIS-2026-11-02.json](APPACK-LIVE-NACHWEIS-2026-11-02.json).

Es wurde keine neue reale Bestellung zur Versandprobe aufgegeben. `sent` bezeichnet
weiterhin die Annahme durch SMTP; der tatsächliche Postfacheingang ist damit nicht
belegt. Einzelheiten zu Wiederholungen und unklaren SMTP-Ergebnissen stehen in
[README.md](README.md).
