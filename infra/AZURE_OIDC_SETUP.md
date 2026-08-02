# Azure OIDC und What-if einrichten

Diese Anleitung wird erst nach Aktivierung des Microsoft-Nonprofit-Azure-
Guthabens ausgeführt. Der vorbereitete GitHub-Workflow führt ausschließlich
`validate` und `what-if` aus. Er enthält keinen Bereitstellungsbefehl.

## 1. Leere Ressourcengruppe erstellen

Im Azure-Portal eine Ressourcengruppe anlegen:

- Name: `rg-ssv53-platzpflege-prod`
- Region: `Germany West Central`

Das What-if benötigt eine bereits vorhandene Ressourcengruppe. Innerhalb
dieser Gruppe werden beim What-if keine Ressourcen erstellt.

## 2. OIDC-Anwendung für GitHub anlegen

In Microsoft Entra ID eine App-Registrierung beziehungsweise einen
Service Principal anlegen, zum Beispiel:

`sp-ssv53-github-azure-what-if`

Eine föderierte GitHub-Anmeldeinformation mit diesen Werten anlegen:

- Organisation/Benutzer: `Rohdeo87`
- Repository: `ssv53-heimspiele`
- Entitätstyp: Branch
- Branch: `feature/azure-mower-migration`

Der resultierende OIDC-Subject lautet:

`repo:Rohdeo87/ssv53-heimspiele:ref:refs/heads/feature/azure-mower-migration`

Es wird kein Client Secret erstellt.

## 3. Berechtigung für What-if bewusst festlegen

Eine reine `Reader`-Rolle ist nicht in jeder Azure-Konstellation
ausreichend, weil auch die Deployment-Validierung und die What-if-
Operation selbst autorisiert werden müssen.

Vor dem ersten Lauf wird deshalb eine gesonderte Identität verwendet,
deren Rechte auf die Ressourcengruppe und ausschließlich auf die für
Validierung und What-if erforderlichen Deployment-Operationen begrenzt
sind. Sie erhält keine allgemeine Berechtigung zum Erstellen, Ändern oder
Löschen der Zielressourcen.

Die konkrete Rollendefinition wird erst nach Aktivierung des
Sponsorship-Abonnements anhand der dort verfügbaren Azure-Rollen geprüft.
Bis dahin wird der What-if-Workflow nicht gestartet. Für eine spätere
echte Bereitstellung wird eine getrennte, ausdrücklich freizugebende
Berechtigung eingerichtet.

## 4. GitHub-Variablen anlegen

Repository → Settings → Secrets and variables → Actions → Variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Diese drei IDs sind keine Gerätezugangsdaten. Husqvarna- und Hydrawise-
Zugangsdaten bleiben weiterhin außerhalb GitHubs und kommen später in
Azure Key Vault.

## 5. What-if starten

Im Branch `feature/azure-mower-migration` unter Actions den Workflow
`Azure-Infrastruktur What-if` manuell starten.

Sichere feste Werte:

- `CONTROL_MODE=DRY_RUN`
- `ENABLE_LIVE_READS=false`
- Timer: einmal pro Minute

Der Workflow darf nur eine Vorschau der erwarteten Azure-Ressourcen
anzeigen. Er darf keine Ressourcen anlegen, ändern oder löschen.
