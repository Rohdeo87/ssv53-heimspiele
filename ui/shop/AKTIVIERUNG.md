# Aktivierungsstand: T-Shirt-Vormerkungen

Stand: **02.11.2026, 15:20 Uhr Europe/Berlin**.

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

## Appack-Anzeigen noch offen

Der serverseitige Versand funktioniert unabhängig von diesen Oberflächenänderungen.
Für ihre Veröffentlichung ist hier kein authentifizierter Appack-CMS-Zugang
konfiguriert. Der ausgelieferte Verwaltungsquelltext wurde lesend geprüft und
enthält die neue Versandanzeige noch nicht.

1. In `Shop.tpl` die einzelne Textänderung aus
   [shop-confirmation.patch](shop-confirmation.patch) übernehmen.
2. [reservation-status.js](reservation-status.js) in einem zusätzlichen
   `<script>…</script>`-Block nach dem bestehenden Verwaltungsscript in
   `Bestellverwaltung.tpl` einfügen. Vorhandenen Inhalt und Zugangskonfiguration erhalten.
3. Speichern/veröffentlichen und die getrennte Anzeige für Besteller und
   Geschäftsstelle prüfen. „Versandstatus aktualisieren“ löst nur eine Leseabfrage aus.

Es wurde keine neue reale Bestellung zur Versandprobe aufgegeben. `sent` bezeichnet
weiterhin die Annahme durch SMTP; der tatsächliche Postfacheingang ist damit nicht
belegt. Einzelheiten zu Wiederholungen und unklaren SMTP-Ergebnissen stehen in
[README.md](README.md).
