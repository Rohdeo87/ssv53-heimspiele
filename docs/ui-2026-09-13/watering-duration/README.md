# Korrektur der Bewässerungsdauer

## Ursache und Änderung

Die bisherige Statistik zählte aktive Kalenderminuten statt zusammenhängender Laufzeiten. Die erste Meldung einer gestarteten Zone kommt erst mit dem nächsten Timerzyklus. Zusätzlich konnten zwei unterschiedliche Zyklen durch Rundung auf dieselbe Minute zusammenfallen. Im synthetischen Vergleich von fünf 20-Minuten- und zwei 30-Minuten-Zonen fehlten dadurch Minuten.

`mower/irrigation_duration.py` wertet Zeitabschnitte aus. Eine vollständige Dauer benötigt einen akzeptierten Start, tatsächlich aktive Relais, frische und konsistente Hersteller-Restlaufzeiten, einen passenden Übergang auf aus und einen Abschlussnachweis der Zone. Maximal drei Sekunden Differenz durch die Zeitauflösung der Herstellerantwort sind erlaubt; das Segment endet spätestens mit der Freimeldung. Weder Sollplan noch Startantwort allein ergeben bestätigte Bewässerungsminuten. Die Darstellung rundet erst die Summe der Sekunden auf Minuten.

- Pausen zwischen Zonen zählen nicht mit. Gleichzeitig aktive Zonen zählen in der Gesamtzeit nur einmal.
- Fehlende Daten, vorzeitiges Ende, externe Starts ohne Startnachweis und widersprüchliche Restlaufzeiten werden aus begrenzten Beobachtungsabschnitten geschätzt. Die App zeigt dafür **ca.** vor der Dauer.
- Ein Abschluss ohne Laufzeitdaten zeigt keine erfundene Dauer von null Minuten.
- Beide Quellen verwenden die Ausführungszeit des Zyklus. Das Journal speichert Sekunden und Mikrosekunden im Schlüssel sowie Start- und Restlaufzeitdaten. Wiederholung desselben Zyklus bleibt idempotent; ältere Journalzeilen bleiben lesbar.
- Die Sieben-Tage-Summe wird am Berichtsfenster abgeschnitten. Zwei Stunden Vorlauf ermöglichen die Zuordnung einer über Mitternacht laufenden Zone; Vorlauf-Ereignisse zählen nicht als neue Planänderungen oder Abschlüsse. Die letzte vollständige Laufdauer umfasst den ganzen belegten Lauf.
- Änderungen des Restplans verlieren bereits abgeschlossene Zonen nicht. Manuelle Anforderungen mit dem produktiven Abschlussstatus werden mitgezählt.

## Prüfung

Die synthetischen Regressionstests umfassen den kompletten 160-Minuten-Lauf, doppelte und ungeordnete Quellen, zwei Zyklen in derselben Minute, Abbruch, fehlende Start-/End-/Zwischenmeldungen, fehlerhafte Startantwort, geänderte Restlaufzeit, alte Meldungen, erneuten Start derselben Zone, Parallelmeldungen, Berichtsgrenzen, Planwechsel und Journal-Roundtrip. Die Formatierung unterscheidet bestätigte, geschätzte und unbekannte Dauer. Ein unabhängiges lesendes Review ergänzte Fälle für Planwechsel, Vorlauf und produktiven Anforderungsstatus; diese wurden behoben und getestet.

Zusätzlich wurden vorhandene Betriebsnachweise lokal erneut ausgewertet. Rohdaten, genaue Betriebszeitpunkte, Gerätekennungen und Benutzerinformationen bleiben in ignorierten lokalen Dateien und werden nicht in das öffentliche Repository übernommen. Hersteller-/Relaisnachweise sind keine Durchflussmessung.

## Einführung und Rückfall

Das begrenzte Backendpaket ersetzt ausschließlich `daily_safety_report.py` und `mower/irrigation_journal.py` und ergänzt `mower/irrigation_duration.py`. Die übrigen installierten Quelldateien bleiben bytegleich. Die beiden Paketbauer berücksichtigen die neue Abhängigkeit. Die Steuerung und alle Geräte-/Sicherheitsregeln bleiben unverändert.

Installation **und Rückfall** erfordern `config-zip --build-remote true`, weil die Archive Quellpakete ohne Python-Bibliotheken sind. Rückfallpaket: `dist/mower-map-release.zip`, SHA-256 `8c66deec91852654d748a496507ec97f547ec51afbaaebda277ec7a2fe928eed`. Die vorherige Appack-Vorlage wird lokal gesichert. Es gibt keine neu geplanten Geräteaktionen zurückzunehmen. Auch bei Rückfall bleiben neue Journalzeilen mit dem bisherigen Leser lesbar; die alte Statistik kann wieder zu niedrig zählen.

Abnahme: Paketimport ohne Netzwerk erfolgreich, alle 16 Functions vorhanden, passender Manifest-Hash und regulärer Timerzyklus nach Installation. Appack-Vorlage nach Speichern zurücklesen und gegen die geprüfte Quelle vergleichen. Ein neuer realer Bewässerungszyklus wird nicht zu Testzwecken ausgelöst.

Veröffentlichungsstand und abschließende Testergebnisse: siehe `release-status.json` (wird nach Abschluss ergänzt).
