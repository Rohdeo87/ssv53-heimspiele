# Noch erforderliche Azure-Freigabe

Stand: 01.11.2026. Die beiden automatischen Benachrichtigungen sind implementiert
und isoliert getestet, aber noch nicht produktiv aktiviert. Auch die vorbereiteten
Appack-Texte und die neue Versandanzeige sind noch nicht veröffentlicht.

## Blockade

Der bestehende GitHub-Bereitstellungszugang darf das aktive Azure-Paket nicht lesen.
Azure hat auch die Vergabe eines auf den Paketcontainer beschränkten Leserechts mit
`AuthorizationFailed` abgelehnt. Es wurde kein neues Zugriffsrecht angelegt.
Die Bereitstellung hat vor Änderungen an produktivem Code oder Einstellungen gestoppt.

Erforderlich ist **Storage Blob Data Reader** für genau diesen Paketcontainer,
zugewiesen an die vorhandene Bereitstellungsidentität:

- Objekt-ID: `ddd70731-d7e9-4674-83f9-a92270a9c28d`
- Storagekonto: `ssv53platzpflegeprodq7kb`
- Container: `app-package-q7kbw54s`
- Ressourcengruppe: `rg-ssv53-platzpflege-prod`

Ein Azure-Administrator mit Berechtigung zur Rollenzuweisung kann in Azure Cloud
Shell (Bash) folgenden Befehl ausführen. Er enthält keine Zugangsdaten und erteilt
nur Lesezugriff auf den genannten Paketcontainer:

```bash
az role assignment create \
  --assignee-object-id ddd70731-d7e9-4674-83f9-a92270a9c28d \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Reader" \
  --scope "/subscriptions/e0ebcbdf-04e4-4096-8355-bae1164563da/resourceGroups/rg-ssv53-platzpflege-prod/providers/Microsoft.Storage/storageAccounts/ssv53platzpflegeprodq7kb/blobServices/default/containers/app-package-q7kbw54s"
```

Die Diagnoseidentität dieser Chat-Umgebung benötigt diese Rolle nicht und wird
nicht geändert. Auch ein privates SMTP-Passwort wird hierfür nicht benötigt.

## Danach noch auszuführen

1. Bereitstellung auf `feature/azure-mower-migration` erneut starten:
   Workflow `SSV53_Order_Mail_GoLive_V1.yml`, `confirmation=SSV53-RESERVATION-NOTIFICATIONS`,
   `appack_page_installed=SERVER-FIRST`.
2. Erfolgreichen SMTP-Check ohne Mailversand und Ausschluss alter Vormerkungen prüfen.
   Erst danach aktiviert der Helfer die neuen Benachrichtigungen mit aktuellem Stichtag.
3. In Appack `ui/shop/shop-confirmation.patch` auf `Shop.tpl` anwenden und
   `ui/shop/reservation-status.js` als zusätzliches Script am Ende von
   `Bestellverwaltung.tpl` einfügen. Vorhandenen Inhalt und Zugangskonfiguration erhalten.
4. Live prüfen, dass Besteller- und Geschäftsstellenstatus getrennt erscheinen.

## Bereits geprüft

- 30 Python-Tests und 14 Untertests erfolgreich; simuliertes SMTP, keine echten Mails.
- Beide bestehenden Appack-Vormerkungen passen zum serverseitigen Datenformat.
- Getrennte Statusanzeigen, Teilfehler, Aktualisierung nur per GET und erneutes
  Rendern der Bestellverwaltung im Browsertest erfolgreich.
- Beide HTML-Mailvorlagen bei 320 px ohne horizontalen Überlauf geprüft.
- Produktiver SMTP-Check: Anmeldung erfolgreich; vorhandene Abholmails aktiviert.
  Dieser Check nutzt nur Authentifizierung und NOOP, ohne eine Mail zu versenden.
- Neuer Live-Endpunkt liefert noch HTTP 404: nicht bereitgestellt.
- Appack-Verwaltungsquelltext unverändert; keine vorhandene Vormerkung angeschrieben.

Nachweis der Azure-Blockade:
https://github.com/Rohdeo87/ssv53-heimspiele/actions/runs/41349397316
