# Physischer Mäherstopp und falscher Bewässerungstext – 12.09.2026

Die App zeigte um 11:01 „Mäher pausiert“ mit der Aufforderung, Bewässerung
und Trockenzeit abzuwarten. Eine Minute später verdrängte eine Alterswarnung
den Stopp. Beide Texte waren für die vorliegenden Daten ungeeignet.

## Nachgewiesene Ursache

Die Betriebsprotokolle melden bis 10:24 Uhr Berlin `PARKED_IN_CS/RESTRICTED`
und ab dem Kontrollzyklus 10:25 Uhr `NOT_APPLICABLE/STOPPED`, jeweils im
Modus HOME. Das neue Geräteereignis stammt von 10:24:51 Uhr. In den
untersuchten Zyklen 10:23–10:47 wurde kein Gerätebefehl der Steuerung gesendet.
Der Nutzer bestätigte anschließend, dass offenbar die STOP-Taste gedrückt wurde.
HOME allein bedeutet bei STOPPED keine weiterhin bestätigte Stationsposition.

Die Bewässerung meldet sieben frische inaktive Zonen. Die physische Trockenfrist
endete am Vortag, auch die aktuelle Datenbestätigung ist erfüllt. Die sechs
zuvor reparierten Aussetzbelege bleiben abgeschlossen: null offene Aufträge.
Das installierte Manifest bleibt
`cc36ebb779787ee97d0c499da61410cf9810505fb384400e1379c3262760a89c`.
Ein erneuter Fehler dieser Belegbereinigung oder eine geänderte Konfiguration
ist durch diese Beobachtungen nicht belegt.

Die Anzeige hatte zwei reproduzierte Fehler:

1. `simpleStatus()` behandelte jeden nichtleeren Wert von `irrigationPhase`
   als laufende Bewässerung inklusive Wartezeit, auch das abgeschlossene
   `COMPLETE_HOLD`. Die Meldung leitete also aus einem internen Ablaufzustand
   einen falschen physischen Zustand ab.
2. Die Behandlung von `MOWER_TELEMETRY` verdeckte nach drei Minuten den bereits
   bekannten STOPPED-Zustand mit einer allgemeinen Alterswarnung. Die Zeitkarte
   wechselte entsprechend von „Automatik pausiert“ auf „Noch offen“.

## Korrektur und Grenzen

- STOPPED zeigt „Mäher ist gestoppt“ und die Aufforderung, die Meldung direkt
  am Mäher zu prüfen. Bei alter Meldung steht ausdrücklich „Zuletzt“ mit der
  letzten Meldezeit. Die Zeitkarte bleibt bei „Stopp am Mäher klären“.
- Ein abgeschlossener Wasserlauf erzeugt allein keine Warteaufforderung mehr.
  Tatsächliche aktive Zonen, ein noch laufender Ablauf, eine zukünftige
  Trockenfrist und eine fehlende Datenbestätigung bleiben getrennte Gründe.
- Laufendes Wasser, nicht bestätigte Starts, Verbindungs- und Gerätefehler
  behalten ihre erforderliche Priorität.
- Keine Änderung an Backend, Konfiguration, Startberechtigung oder Schutzregeln.
  Keine Gerätebefehle. Ein physischer STOP wird weder freigegeben noch als
  geparkter oder sicher erreichter Stationszustand behandelt.

[Husqvarna: Automower startet nicht oder stoppt](https://www.husqvarna.com/uk/support/husqvarna-self-service/automower-won-t-start-or-keeps-stopping-ka-01503/)
nennt nach gedrückter STOP-Taste eine notwendige Bedienung vor Ort. Das ist
von normalem Parken oder einer über die App gesetzten Pause zu unterscheiden.
Die eingetragene Spielbelegung 10:00–13:45 bleibt zu beachten; diese Änderung
ist keine Aufforderung, während einer Belegung am Gerät zu starten.

## Prüfung und Veröffentlichung

- [203 Appack-Tests bestanden](tests-all-appack.txt), davon fünf neue
  Regressionstests für den Stopp, das Altern der Meldung, Wasserpriorität,
  abgeschlossene Trockenzeit und Fehlerzustände.
- Unabhängiger Review des begrenzten Frontend-Diffs mit einem günstigeren
  Modell; keine konkrete Regression gefunden. Kein Backend-Code geändert.
- [Mobile Browserprüfung bei 390 Pixeln](browser-check.json): frische und
  ältere Stoppmeldung aus beobachteten Feldern mit synthetischen Rechten,
  sichtbare Handlungsanweisung, Startknopf verborgen, kein horizontaler Überlauf.
  Offline-Vorschau mit gesperrtem Netzwerk. Die Screenshot-Funktion des
  angeschlossenen Browsers war nicht verfügbar; keine Bildabnahme behauptet.
- Vor der Änderung entsprach die gesamte CMS-Vorlage exakt der lokalen
  vorherigen Version. Nach dem Einfügen wurde der vollständige Editorinhalt
  erneut mit der getesteten Vorlage abgeglichen.
- CMS-Speicherung am 12.09. um 11:17 Uhr. [Öffentliche Auslieferung](publication.json)
  um 11:18:27 Uhr bestätigt alle vier geänderten Anzeigefunktionen bytegenau.
- Eine Nutzerbedienung oder Wiederaufnahme des echten Mähers ist nicht geprüft.

Bei Rückfall nur `appack-platzwart-dashboard.html` aus dem Vorgängercommit
`84a0d0f` als CMS-Vorlage wiederherstellen. Der lokale Vorlagenabzug liegt als
`appack-before.tpl` vor. Der Gerätestopp und das Backend bleiben unverändert;
ein Vorlagenrückfall löst keinen Gerätebefehl aus.
