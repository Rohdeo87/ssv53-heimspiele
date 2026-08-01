# Installation und Betrieb der Mähplanberechnung

Die Datei `.github/workflows/SSV53_Handy_Installer_Maehplan_V5.yml` ist zugleich Installer und dauerhafter Dry-Run-Workflow.

1. Die V5-Datei einmal manuell über die GitHub-Weboberfläche in `.github/workflows/` hochladen.
2. Unter **Actions → SSV53 Mähplan installieren und berechnen V5** den Workflow manuell starten.
3. Der Workflow legt ausschließlich die Programm-, Konfigurations-, Dokumentations- und Testdateien an. Er versucht bewusst nicht, weitere Workflow-Dateien zu erzeugen.
4. Bereits vorhandene Mähplan-Dateien werden nicht überschrieben. Dadurch bleiben spätere Anpassungen an `mower/config.json` erhalten.
5. In GitHub unter **Settings → Secrets and variables → Actions** `HYDRAWISE_API_KEY` eintragen.
6. `HYDRAWISE_CONTROLLER_ID` nur eintragen, falls das Hydrawise-Konto mehrere Steuergeräte besitzt.
7. In der Actions-Zusammenfassung und im Artefakt `maehplan-dry-run-*` die berechneten Zeiten prüfen.

Der Workflow läuft danach automatisch täglich sowie nach einem erfolgreichen Heimspiel-Update. Er steuert den Husqvarna-Mäher noch nicht und verändert Hydrawise nicht.
