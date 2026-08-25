# Datenschutz- und Aufbewahrungskonzept der SSV53-App

Stand: 25. August 2026

Dieses Dokument beschreibt die im Repository technisch nachvollziehbaren
Datenflüsse der SSV53-App. Es ergänzt die für App-Nutzende bestimmte
Datenschutzerklärung. Es ersetzt weder die vereinsinterne Dokumentation aller
Appack-Module noch eine abschließende rechtliche Prüfung.

## Verantwortlicher

Schönwalder SV 1953 e.V.<br>
Kurmärkische Straße 2<br>
14621 Schönwalde-Glien<br>
E-Mail: info@ssv53.de

## Leitlinien

- Es werden nur Daten verarbeitet, die für die jeweilige Funktion erforderlich
  sind.
- Vollständige Appack-Profile und Kontaktkopien werden nicht in Azure
  gespeichert. Für Belegungen bleibt nur die erforderliche Minimalidentität aus
  technischer ID, Anzeigename und Rolle erhalten und wird im Belegungsfeed
  ausgegeben.
- Öffentliche Belegungsdaten enthalten bei Erstellenden und verschiebenden
  Personen nur eine technische ID, Anzeigename und Rolle. Kontaktdaten werden in
  der App aus der dafür vorgesehenen Ansprechpartnerquelle aufgelöst.
- Eigene Profildaten dürfen die Anzeige nur für die jeweils angemeldete Person
  ergänzen.
- Fehlerhafte oder nicht eindeutig datierbare Bestandsdaten werden bei einer
  automatischen Bereinigung übersprungen. Dadurch darf keine aktuelle
  Platzsperre und keine Mäher- oder Beregnungssicherung verloren gehen.
- Protokolle dürfen keine Zugangsdaten, PINs, Tokens, vollständigen
  E-Mail-Adressen oder Request-Bodys enthalten.

## Verarbeitungstätigkeiten und Aufbewahrung

| Bereich | Benötigte Daten | Zweck | Technische Aufbewahrung |
| --- | --- | --- | --- |
| Appack-Konto und Rollen | Profil-ID, Name, Rolle, gegebenenfalls Profilbild und freiwillige Kontaktdaten | Anmeldung, rollenabhängige Funktionen, Kontaktaufnahme | Nach den in Appack festgelegten Lösch- und Vertragsregeln; bei Ausscheiden oder Widerruf prüfen und löschen beziehungsweise sperren |
| Ansprechpartner | Name, Funktion/Mannschaft, freiwillig veröffentlichte Kontaktwege und Bild | Erreichbarkeit im Verein; die Daten sind für Personen sichtbar, die die Ansprechpartner- oder Platzbelegungsseite öffnen können | Nur solange die Aufgabe besteht beziehungsweise die Veröffentlichung gewünscht ist; mindestens halbjährliche Prüfung |
| Manuelle Platzbelegung | Termin, Platz, Bezeichnung, Mannschaft, technische Profil-ID, Anzeigename und Rolle; bei Verlegung dieselben Minimaldaten der ausführenden Person | Belegungsplan, Berechtigung zur Änderung, sichere Mäherplanung | Abgelaufene aktive Termine 90 Tage nach Terminende; gelöschte Termine 90 Tage nach Lösch-/Änderungszeitpunkt; Befehls- und Auditmetadaten 180 Tage |
| Trainingsabsagen | Termin-ID, Mannschaft, Datum, Uhrzeit, Platz, Absage-/Wiederherstellungsstatus | Freigabe der Mähzeit und nachvollziehbarer Belegungsplan | Status 90 Tage nach Termin; Auditmetadaten 180 Tage |
| Offizielle Spiele | Öffentliche Spielplanangaben wie Mannschaften, Anstoß, Platz, Wettbewerb und Spiel-ID | Belegungsplan und sichere Platzsperren | Im aktuellen veröffentlichten Spielplan und dessen betrieblicher Versionshistorie; keine Appack-Profildaten |
| Kollisionshinweise | Betroffene Termine, Platz und Zeiten | Interne Warnung an die festgelegten Vereinsadressen | E-Mail nach den vereinsinternen Postfachregeln; keine automatische Trainer-E-Mail und keine öffentliche Empfängerliste |
| Bestellbenachrichtigung | Empfängeradresse während des Mailversands; danach nur ein kryptografischer Vergleichswert, Bestell-ID und Versandstatus | Einmaliger, duplikatfreier Versand | Versandstatus und Vergleichswert 180 Tage; keine klare Empfängeradresse im Azure-Statusspeicher |
| Platzwart-Zugang | Zufällige pseudonyme Gerätekennung, gehashter Gerätetoken, kurzfristige Sitzung und gehashte Netz-/Gerätemerkmale bei Fehlversuchen | Sichere Gerätefreischaltung, Anmeldung und Missbrauchsschutz | Fehlversuche 7 Tage; Auditmetadaten 180 Tage; aktive Gerätefreigabe bis Widerruf. Pseudonyme Geräte- und Aktivierungsnachweise bleiben für Einmalverwendung und Widerrufsschutz erhalten, solange dieses Zugangssystem betrieben wird und der Nachweis erforderlich ist |
| Mäher- und Beregnungstelemetrie | Geräte- und Zonenstatus, Akku, Fehler, Befehlsstatus, Zeitpunkte | Sichere Automatik, Platzwartanzeige, Statistik und Sicherheitsbericht | Fachlich notwendiger Verlauf; Azure-Anwendungsprotokolle 30 Tage. Keine gewöhnlichen App-Nutzerkontaktdaten |
| Vereinsheim-Reservierungen | Buchungszeit, Inhalt und im geschützten Platzwartbereich buchende Person | Anzeige der nächsten Vereinsheimbelegungen | Appack ist führendes System; die Azure-Funktion hält nur einen kurzzeitigen Arbeitsspeicher-Cache |
| Feedback, Bedarfsmeldung, Funktionsvorschlag und Umfragen | Die im jeweiligen Formular eingegebenen Angaben, gegebenenfalls Profilbezug | Bearbeitung des Anliegens | In Appack beziehungsweise dem benannten Empfängerpostfach nur bis zur Erledigung plus notwendiger Nachweisfrist; konkrete Fristen organisatorisch festlegen |
| Reservierungen, Dateien und Bestellverwaltung | Modulabhängige Inhalts-, Buchungs- und Berechtigungsdaten | Bereitstellung der gewählten App-Funktion | Nach den dokumentierten Appack-Modulregeln; Berechtigungen und Altbestände mindestens halbjährlich prüfen |

Die Fristen für Azure-Daten sind als sichere Standardwerte implementiert und
können über eng begrenzte Anwendungseinstellungen angepasst werden. Eine
Verkürzung darf niemals aktuelle oder zukünftige Platzsperren löschen.

## Empfänger und Auftragsverarbeitung

Je nach Funktion werden insbesondere folgende Dienstleister eingesetzt:

- vmapit/Appack für App, Profile, Rollen und Appack-Module,
- Microsoft Azure für Funktionen, Tabellen, Protokolle und Key Vault,
- der konfigurierte Mailanbieter für ausgehende Vereins-E-Mails,
- Apple und Google für App-Verteilung und gegebenenfalls Push-Zustellung,
- Husqvarna und Hydrawise für Mäher- und Beregnungssteuerung.

Husqvarna und Hydrawise erhalten aus der im Repository implementierten
Automatik keine Kontakt- oder Profildaten gewöhnlicher App-Nutzender. Für jeden
Auftragsverarbeiter müssen ein aktueller Vertrag zur Auftragsverarbeitung, die
Unterauftragsverarbeiter und mögliche Drittlandzugriffe dokumentiert werden.

## Rechtsgrundlagen und Transparenz

Für Mitglieder- und Rollenfunktionen kommen insbesondere die Durchführung des
Mitgliedschaftsverhältnisses und berechtigte Vereinsinteressen in Betracht.
Freiwillig veröffentlichte Bilder und Kontaktwege benötigen eine nachweisbare,
jederzeit widerrufbare Einwilligung, soweit keine andere tragfähige Grundlage
dokumentiert ist. Sicherheitsprotokolle und die Platzpflegeautomatik beruhen auf
dem Interesse an einem sicheren, zuverlässigen Vereinsbetrieb. Die konkrete
Zuordnung ist im Verzeichnis der Verarbeitungstätigkeiten zu bestätigen.

## Technische Schutzmaßnahmen

- Azure Managed Identity und Key Vault statt Zugangsdaten im Quellcode.
- TLS, eng begrenzte Sitzungstokens, pseudonyme Gerätekennungen, gehashte Tokens und
  Brute-Force-Schutz für das Platzwart-Dashboard.
- Datenminimierte öffentliche Kalenderantworten.
- Fail-closed Sicherheitslogik für Mäher und Beregnung.
- Getrennte Aufbewahrungsbereinigung ohne Geräte- oder Mailbefehle.
- 30 Tage Aufbewahrung für Application-Insights-/Log-Analytics-Protokolle.
- Zielgerichtete Tests für Datenschutzfilter, Bereinigung und unveränderte
  Kalender-, Mäher- und Beregnungsfunktionen.

### Bekannte technische Grenze der Traineraktionen

Appack stellt in der derzeit genutzten, kostenfreien Einbindung keine vom
Azure-Backend kryptografisch prüfbare Nutzeridentität bereit. Der App-Client
blendet Anlegen, Verlegen und Absagen rollenbezogen ein; Azure erzwingt
zusätzlich Feldgrenzen, zulässige Plätze und Zeiträume, Überschneidungs-
bestätigungen sowie die unveränderlichen Mäherpuffer. Die Schreibschnittstellen
können die behauptete Appack-Rolle jedoch nicht unabhängig verifizieren. CORS
oder eine im App-Code hinterlegte Zeichenfolge würden diese Lücke nicht
schließen. Die Folgen werden durch strikte Datenminimierung und fehlende
Trainer-E-Mails begrenzt. Eine vollständige Behebung erfordert künftig eine
serverseitig verifizierbare Appack-Anmeldung oder einen gleichwertigen
vereinseigenen Anmeldeweg; bis dahin ist das Restrisiko ausdrücklich
dokumentiert.

## Organisatorische Pflichten vor und nach Veröffentlichung

1. Aktuelle Verträge zur Auftragsverarbeitung mit Appack/vmapit, Microsoft und
   dem Mailanbieter nachweisbar ablegen.
2. Unterauftragsverarbeiter und mögliche Drittlandübermittlungen prüfen und die
   Datenschutzerklärung bei Änderungen aktualisieren.
3. Veröffentlichungszustimmungen für Ansprechpartnerfotos und freiwillige
   Kontaktwege dokumentieren. Dabei ausdrücklich die Sichtbarkeit innerhalb
   der Ansprechpartner- und Platzbelegungsseite benennen; Widerrufe zeitnah in
   der Ansprechpartnerquelle umsetzen.
4. App-Store-Datenschutzangaben und Google-Play-Datensicherheit mit der
   veröffentlichten Erklärung abgleichen.
5. Einen festen Prozess für Auskunft, Berichtigung, Löschung, Einschränkung,
   Widerspruch und Datenübertragbarkeit bestimmen.
6. Mindestens halbjährlich Rollen, Gerätefreigaben, Ansprechpartner, alte
   Formulareinträge und Appack-Moduldaten prüfen.
7. Änderungen an neuen App-Funktionen vor Veröffentlichung auf zusätzliche
   Datenarten, Empfänger und Einwilligungserfordernisse prüfen.

## Quellenrahmen

Maßgeblich sind insbesondere Art. 5, 6, 13, 14, 28 und 32 DSGVO sowie § 25
TDDDG. Die Appack-Datenschutzhinweise weisen ausdrücklich darauf hin, dass die
bereitgestellte Vorlage an die konkrete App angepasst und ein Vertrag zur
Auftragsverarbeitung abgeschlossen werden muss.
