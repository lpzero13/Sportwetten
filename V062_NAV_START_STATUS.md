# V0.6.2 – Navigation, bedarfsgerechtes Laden und Batch-Start

Stand: 06.09.2026. Lokal umgesetzt; noch nicht auf GitHub veröffentlicht.

## Navigation und Performance

- Stabile Reiterreihenfolge mit expliziter Auswahl für Quoten, Analyse und
  FotMob; kein Umsortieren mehr, um den angeklickten Einstieg zu simulieren.
- Nur die sichtbare Unteransicht wird ausgeführt. Quoten laden weder Analyse
  noch FotMob, Historie oder Debug im Hintergrund.
- FotMob öffnet anhand des angeklickten Events ohne vorgeschalteten Tipico-
  Detailrequest. Die Auswahl bleibt auch bei einem verschwundenen Feed-Eintrag
  verfügbar; fehlende Daten werden weiterhin ausdrücklich angezeigt.
- Keine zusätzliche Übersicht-Abfrage und kein Übersicht-Aktualisierungstimer
  in der Detailansicht. Tipico- und FotMob-Aktualisierung gelten nur für ihre
  jeweilige Ansicht.
- Bei nutzbarem FotMob-Livepanel keine einzelnen Link-Abfragen je Spiel in der
  Übersicht. Zuordnung erst für das gewählte Spiel.
- Ältere Streamlit-Versionen ohne zustandsbehaftete Reiter bekommen eine
  horizontale Auswahl; auch dort werden keine versteckten Ansichten geladen.

Der erneute Quoten-Erstklick ließ sich in einer frischen lokalen Browsersitzung
vor dieser Umstellung nicht als Totalausfall reproduzieren. Fragile Reiter-
Umsortierung und unnötige versteckte Aufrufe waren dagegen im Code nachweisbar.
Es wird keine pauschale prozentuale Beschleunigung behauptet.

## Startkorrektur

Zuvor wartete das Öffnen des Browsers auf vollständige Worker-Bereitschaft.
Bei einer langsamen ersten Collector-Runde konnte trotz erreichbarer Oberfläche
das 45-Sekunden-Limit ablaufen, ohne den Browser zu öffnen.

Jetzt öffnet der Browser bereits bei erreichbarer Oberfläche. Noch ausstehende
Worker-Meldungen werden ausdrücklich als Initialisierung behandelt, nicht als
vollständige Bereitschaft. `ready` bleibt an echte aktuelle Meldungen gebunden.
Die Überwachung und der Schutz vor doppelten Prozessen bleiben erhalten.

Die Batch zeigt sofort eine Startmeldung, reicht Parameter wie `-NoBrowser`
weiter und erhält den Fehlercode des Startskripts. Scheitert nur das Öffnen des
Standardbrowsers, wird die lokale URL ausgegeben; die Dienste werden nicht beendet.

## Prüfungen

- Vollständige Suite: **206 passed, 1 skipped**, 71,62 Sekunden.
- Gezielte Navigation: alle drei Erstklicks, erneutes Öffnen desselben Spiels,
  Zurück, Aktualisieren, alte Streamlit-Variante und Aufrufzähler für versteckte
  Ansichten. FotMob-Einstieg ohne Tipico-Request nachgewiesen.
- Starttests: UI bereit / Worker noch ohne Meldung, vollständige Bereitschaft,
  NoBrowser und fehlgeschlagener Browserstart. Keine Änderung des Ready-Flags.
- Echter Kaltstart nach regulärem Stop: Aufruf der Batch über `cmd.exe` aus
  einem anderen Arbeitsordner. Exitcode 0, **9,44 Sekunden** vom Batch-Aufruf
  bis zur Rückkehr; kein First-Install-Benchmark. Drei Dienste danach bereit.
- Browser: Leichhardt Tigers – NWS Spirit, Quoten mit 28 Märkten und 86
  Auswahlen beim Prüfabschnitt; Wechsel Quoten → FotMob → Analyse, Zurück sowie
  direkter FotMob-Einstieg nach Neustart erfolgreich.
- Für dieses australische Spiel keine bestätigte FotMob-Zuordnung: Das Panel
  zeigt diesen Zustand verständlich. Keine Statistikabdeckung erfunden.

Die lokale Anwendung läuft. Keine Änderungen an Portfolio-Regeln, keine
Datenbanklöschung, kein Containerzugriff und kein GitHub-Push.
