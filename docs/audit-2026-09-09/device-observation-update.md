# A01 – Gerätebestätigungen aus Hydrawise-Quellbeobachtungen

Stand 09.09.2026: Die Gerätepfade speichern für Planänderungen,
Suspendierungs-Revalidierungen und Zonenenden zusätzlich die letzte verwendete
Hydrawise-Quellzeit. Eine positive Bestätigung benötigt einen späteren,
gültigen Quellzeitpunkt. Derselbe Zeitpunkt darf weder eine Kandidatenfrist
verlängern noch eine Beobachtungszahl erhöhen. Rückwärts laufende, fehlende oder
mehr als 30 Sekunden zukünftige Quellen bleiben gesperrt.

`details.hydrawise.safety.observed_at_utc` ist in jedem Modus die einzige
Quellidentität. Ein fehlender, alter, rückwärts laufender oder zukünftiger Wert
sperrt positive Bestätigungen; ein Steuerzyklus darf ihn nie durch seine
eigene Uhrzeit ersetzen. `details.hydrawise.cache` ist der Übergabevertrag für
den gemeinsamen Cache:
`source_observed_at_utc` muss genau dem unveränderten
`details.hydrawise.safety.observed_at_utc` entsprechen. `fetched_at_utc` bleibt
Diagnosezeit und wird nicht als Quellbeweis verwendet. `new_observation` ist
absichtlich keine Voraussetzung: ein anderer Leser darf einen späteren,
persistierten Quellstand geliefert haben. Auch ohne Cache-Map gilt derselbe
Quellzeitvertrag für einen direkten Herstellerabruf.

Die vorhandenen 150 Minuten Trocknung werden nicht verkürzt. Ein wiederholter
Cacheeintrag innerhalb des Gültigkeitsfensters erhält eine bestehende
Datenvertrauenskette, verlängert aber nicht ihr belegtes Ende. Zu alte oder
rückwärts laufende Daten können nicht bestätigen. Physische Trocknung und
Schutzparkierung bleiben erhalten. Die Suspendierungsrevalidierung behält
ihr 90-Sekunden-Maximum; der Wert wurde nicht erhöht.

Für die Anbindung durch `dry_run.py` muss ein Cachemodus immer die vollständige
`CachedStatusRead.metadata()` unter `details.hydrawise.cache` ausgeben. Ohne
diesen Beleg dürfen Geräte-Runner den Cachemodus weiterhin nicht freigeben.

Die Bestätigungen prüfen zusätzlich Zeitgrenzen vor Start, Stopp und
Suspendierung. Die 3-Minuten-Grenze unterbricht Plan-/Endnachweise bei größeren
Quelllücken; ältere Kandidaten ohne Beobachtungs-ID beginnen konservativ neu.
Die Cache-Gates bleiben erhalten, da ein 120-Sekunden-Abrufabstand nicht zur
unveränderten 90-Sekunden-Revalidierung passt. [Integration und Nachweise](integration-update.md).
