# V0.6.2 – Ein-Klick-Betrieb & neue Analyseoberfläche

Stand: 05.09.2026. Ergänzung zu V062_STATUS.md; lokal umgesetzt, noch nicht auf GitHub.

## Ergebnis

- Windows-Start startet Oberfläche, Collector und Paper-Worker gemeinsam.
- Vorhandene Oberfläche wird übernommen; fehlende Dienste werden ergänzt.
- Sequenzieller und gleichzeitiger Mehrfachstart: jeweils nur ein Dienst pro Rolle.
- Stop/Neustart mit echten Prozessen geprüft: Worker beendeten sich kooperativ;
  `forced_worker_pids` war leer. Danach alle drei Dienste wieder bereit.
- Seit dem Start kamen echte Halbzeitbeobachtungen in der lokalen Datenbank an.
  Um 11:16 Münchner Zeit waren vier Entscheidungen und vier virtuelle Trades
  im vorhandenen aktiven MARKET_STRUCTURE-Portfolio gespeichert.
- Keine echten Wetten, keine Änderung der Portfolio-Regeln, keine Datenbanklöschung.
- Die Anwendung bleibt nach der Prüfung lokal gestartet.

## Ein-Klick-Bedienung

| Datei | Aufgabe |
|---|---|
| START_TIPICO.bat | Startet/repariert die gemeinsame Laufzeit und öffnet den Browser |
| STOP_TIPICO.bat | Stoppt Oberfläche, Collector und Paper-Worker |
| STATUS_TIPICO.bat | Zeigt Prozesse, Datenbankpfad und letzte Worker-Meldungen |

Der Browser allein startet keine Sammlung und sein Schließen beendet sie nicht.
`START_TIPICO.bat` prüft die deklarierte Python-Umgebung und installiert fehlende
Abhängigkeiten. Python selbst muss installiert sein. Beim ersten vollständigen
Lauf wurde die bereits deklarierte, lokal fehlende psutil-Abhängigkeit installiert
und pyarrow auf den deklarierten Versionsbereich gebracht.

Die Prozessverwaltung ist pro Projektordner gesperrt. Sie erkennt ausschließlich
exakte ausgeführte Projekt-Entrypoints und beendet keine fremden Portbesitzer.
Worker erhalten eine kooperative Stop-Anforderung; nach dem Zeitlimit nötige
Abbrüche werden im Status protokolliert. Nach unerwartetem Prozessende wird mit
Wiederholungsabstand neu gestartet. Ein System-Autostart beim Windows-Login wurde
nicht eingerichtet.

Status und einzelne Logs: `logs/local-runtime.json`, `local-runtime.err.log`,
`collector.err.log`, `paper.err.log`, `ui.err.log` (jeweils im logs-Ordner).
Der lokale Browser-Endpunkt ist nur an 127.0.0.1 gebunden.

## Oberfläche

- Gemeinsames helles Layout mit dunkler Navigation, Petrol-Akzenten und gut
  unterscheidbaren Gewinn-/Verlustkarten. Keine externen Schrift-/Bilddienste.
- Auswahl eines Spiels öffnet eine fokussierte Detailansicht; Rückweg zur
  Übersicht und Reihenfolge der Tabs bleiben stabil.
- Analyse führt mit einer verständlichen Einschätzung und Quotenfrische.
- Gewinnfall-Rendite, modellierter Erwartungswert und P1-Puffer sind getrennt.
- Drei Szenarien: 0 Tore, genau 1 Tor, mindestens 2 Tore. Netto-P/L, gesamte
  Auszahlung, Teileinsatz, Quote und Markt-Schätzung stehen direkt zusammen.
- Quellen/Alternativen/Provider-IDs und Rechenweg/Datenqualität sind aufklappbar.
- Quoten-Szenarien nach der Halbzeit werden als Restspielzeit gekennzeichnet.
- Veraltete, unbekannte oder unvollständige Daten ergeben kein positives Label.
- Szenario-Einsatz ändert ausschließlich die Anzeige, weder Quotenzeitstempel
  noch Datenbank oder Portfolio-Einstellungen.
- Windows-Warnungen verweisen auf den Ein-Klick-Start, Container-Warnungen auf systemd.

Der Dashboard-Skill hat die Hierarchie beeinflusst: Zusammenfassung und
Risiken zuerst, vergleichbare Szenarien zusammen, technische Belege darunter.
Die Berechnungen verwenden weiterhin die vorhandenen normalisierten Tipico-Daten.

## Prüfung

- Gesamtsuite: **186 passed, 1 skipped**.
- Zusätzliche Tests: Prozesszuordnung, fremde Ports, Start-Sperren, Stop-Signal,
  lesender Status, Quotenalter, fehlende Daten, Formeln, HTML-Escaping und
  Streamlit-Bedienung mit geändertem Einsatz.
- Browser: echtes Live-Spiel CSC 1599 Selimbar – AFC Metalul Buzau,
  Änderung von 30 auf 100 Euro, Quellen-Tabelle, veralteter Datenzustand,
  Rückkehr zur Übersicht.
- Mobile Breite 390 px: Karten einspaltig, Dokumentbreite ebenfalls 390 px;
  kein seitlicher Seitenüberlauf. Desktop-Größe nach der Prüfung wiederhergestellt.
- Git-Diff-Whitespace-Prüfung bestanden.

## Noch nicht behauptet

Kein GitHub-Push in diesem Schritt. Kein Zugriff auf Container 110 und keine
Produktionsabnahme dort. Die vier neuen lokalen Trades belegen den automatischen
Einstieg, noch nicht ihre endgültige Abrechnung oder Profitabilität. Die vorherige
isolierte Final-Settlement-Canary bleibt ein eigener Prüfpunkt in V062_STATUS.md.

Bereits vorher vorhandene Änderungen/Löschungen unter outputs sowie V060_STATUS.md
wurden nicht angefasst. Laufzeitdaten und Datenbank bleiben unversioniert.
