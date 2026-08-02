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
