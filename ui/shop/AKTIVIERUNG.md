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
- Bei der Erstaktivierung blieben alle nicht betroffenen Paketdateien bytegleich,
  einschließlich `order_mail.py`. Die spätere Mailvorlagenänderung ist unten getrennt belegt.
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

## Gemeinsame Kundenmail-Vorlagen live aktualisiert

Reservierungs- und Abholbestätigung verwenden denselben tablebasierten Aufbau
mit Wappen, Statusfeld, Produktfotos und Gesamtsumme. Die Anrede berücksichtigt
„Vorname Nachname“ und „Nachname, Vorname“. Der Hinweis zur Terminvereinbarung
verweist über beschriftete Kontakticons auf die Geschäftsstelle; E-Mail-Adresse,
Mobilnummer und WhatsApp wurden aus der bestehenden Geschäftsstellenseite
übernommen. Die Icons sind als MIME-CID-PNG eingebettet, ohne Icon-Schrift
oder neue externe Bildquelle. Die interne Meldung an die Geschäftsstelle behält
den vollständigen Namen als Kontaktdatum.

Der neue Bereitstellungsweg `SSV53-CUSTOMER-MAIL-TEMPLATES` /
`PRESERVE-ACTIVATION` aktualisiert nur die beiden Mailmodule und drei Icons
im aktiven Paket. Er prüft die bekannte Ausgangsversion, erhält alle anderen
Paketdateien bytegleich und schreibt keine Anwendungseinstellungen oder
Versandclaims. Die laufende neue Version und lesbaren PNG-Dateien wurden
über den SMTP-Check ohne Mailversand bestätigt. Der ursprüngliche Stichtag
und beide Mail-Schalter sind unverändert; eine historische Vormerkung bleibt
auch nach der Aktualisierung vom Nachversand ausgeschlossen.

56 gezielte Tests mit 35 Untertests bestanden. Browserprüfungen beider Vorlagen
bei 320, 390 und 768 Pixeln zeigen keinen horizontalen Überlauf. Die Vorschauen
verwenden ausschließlich synthetische Daten; keine Testmail oder Testvormerkung
wurde erzeugt. Bereits versendete Mails werden nicht nachträglich ersetzt.

[Veröffentlichung](https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/41475176836) ·
[Nachweis](CUSTOMER-MAIL-TEMPLATES-2026-11-02.json).

Feinanpassung am 02.11.2026 ebenfalls live: Der Kontaktbereich beider HTML-Mails
zeigt nur die drei klickbaren Buttons, ohne zusätzliche ausgeschriebene Adresse
oder Telefonnummer. [Release-Nachweis](CUSTOMER-MAIL-BUTTONS-2026-11-02.json).
