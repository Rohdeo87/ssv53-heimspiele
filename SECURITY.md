# Sicherheit

## Zugangsdaten

Zugangsdaten, API-Schlüssel, Tokens und lokale Azure-Einstellungen dürfen
niemals in dieses öffentliche Repository committed werden.

- GitHub-Laufzeitwerte gehören in GitHub Secrets oder Variables.
- Husqvarna- und Hydrawise-Zugangsdaten gehören nach der Migration in
  Azure Key Vault.
- `local.settings.json`, `.env` und erzeugte ZIP-Dateien sind über
  `.gitignore` ausgeschlossen.

## Sicherheitsrelevante Änderungen

Änderungen an Mähersteuerung, Beregnungslogik, GitHub-OIDC, Azure-Rollen
oder Key-Vault-Verweisen werden zuerst auf einem Branch getestet. Echte
Gerätebefehle bleiben bis zu einer gesonderten Freigabe deaktiviert.

Sicherheitsprobleme bitte nicht mit Zugangsdaten in einem öffentlichen
Issue dokumentieren.

## Datenschutz und Aufbewahrung

Der öffentliche Belegungsfeed enthält bei manuell angelegten oder verlegten
Terminen ausschließlich stabile Profilreferenz, Anzeigename und Rolle. Kopien
von Telefonnummern, E-Mail-Adressen, Chat-IDs, Profilbildern, Social-Links und
freiem Kontakt-HTML werden weder neu in Azure Table Storage gespeichert noch
öffentlich ausgegeben. Kontaktdaten werden bei Bedarf aus der autorisierten
Appack-/Ansprechpartnerquelle aufgelöst.

Die Function App führt täglich um 11:23 UTC eine vom Mäher-, Beregnungs- und
Mailversand vollständig getrennte Bereinigung aus. Standardfristen:

- abgelaufene Sonderbelegungen und Trainingsabsagen: 90 Tage;
- Audit- und Befehlsmetadaten: 180 Tage;
- fehlgeschlagene Platzwart-Anmeldungen und abgelaufene Sperren: 7 Tage;
- pseudonymisierte Bestellmail-Claims: 180 Tage;
- Kollisionsmail-Zustellclaims: 180 Tage.

Die Fristen können mit `SSV53_SPECIAL_EVENT_RETENTION_DAYS`,
`SSV53_TRAINING_CANCELLATION_RETENTION_DAYS`,
`SSV53_PRIVACY_AUDIT_RETENTION_DAYS`,
`SSV53_PLATZWART_LOGIN_RETENTION_DAYS`,
`SSV53_OCCUPANCY_COLLISION_RETENTION_DAYS` und
`SSV53_ORDER_MAIL_RETENTION_DAYS` ausschließlich verkürzt werden. Die oben
genannten Standardfristen sind zugleich technische Höchstfristen. Eine
Verlängerung erfordert vorab eine dokumentierte Zweckprüfung und eine
entsprechende Aktualisierung von Code und Datenschutzerklärung.
Unklare Legacy-Datensätze werden nicht gelöscht. Azure-ETags verhindern, dass
die Bereinigung eine gleichzeitig aktualisierte Belegung oder Sperre
überschreibt. Gerätefreigaben und Aktivierungs-Claims werden nicht automatisch
gelöscht, weil sie für Widerrufsschutz und Einmalverwendung erforderlich sind.
