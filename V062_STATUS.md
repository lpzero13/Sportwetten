# V0.6.2 – Paper Trading Reliability & Market Foundation

Stand: 05.09.2026. Umsetzung des besprochenen ersten Roadmap-Schritts.

Nachtrag: Ein-Klick-Betrieb, neue Analyseoberfläche und aktuelle Tests (186 bestanden)
stehen in [V062_DESKTOP_UI_STATUS.md](V062_DESKTOP_UI_STATUS.md).

```text
IMPLEMENTATION = PASS
AUTOMATED_TESTS = PASS (170 passed, 1 skipped)
UI_INTERACTION_TEST = PASS
REAL_TIPICO_FEED_ACCESS = PASS
REAL_HALFTIME_ENTRY = PASS
REAL_MARKET_NO_BET = PASS
REAL_ENTRY_RAW_ARCHIVE = PASS
REAL_FINAL_SETTLEMENT_CANARY = PENDING
CT110_DEPLOYMENT = PENDING
CT110_EFFECTIVE_CONFIG = PENDING
FULL_PRODUCTION_ACCEPTANCE = PARTIAL
PROFITABILITY = NOT_PROVEN
```

## Umgesetzt

- Zwei klar getrennte Paper-Familien: MARKET_STRUCTURE und MARKET_ONLY. Fehlendes
  P1 blockiert nur die Familie, die eine P1-Prognose benötigt. Bestehende Regeln
  werden beim Update nicht automatisch gelockert.
- Aktive Halbzeit, bekannter HT-Spielstand, regulärer Scope, korrekte Totallinie,
  offene Quotes, identische Beobachtungszeit und explizite Provider-IDs sind
  Einstiegsvoraussetzungen. Kein numerisches Neuzuordnen von Quoten.
- Gegenprüfung des aktuellen Spielzustands und atomare Reservierung verhindern
  doppelte Einstiege durch zwei Worker. BET-Beleg, Einstieg und Ledger-Reservierung
  werden in derselben Datenbanktransaktion geschrieben.
- Regeländerungen erhalten eine neue Version. Konfiguration und Hash bleiben
  im Trade und in jeder Entscheidung eingefroren.
- BET/NO_BET inklusive Gründen und Marktbeobachtung. Ein Spiel kann mehrere
  Beobachtungen haben; das sind keine unabhängigen zusätzlichen Spiele.
- Originaldaten derselben Beobachtung werden bei einem Entry komprimiert archiviert,
  wenn RAW_PAPER_ENTRY aktiv ist. Keine zweite Netzwerkabfrage pro Strategie.
- P1 nach Normalisierung und zusätzlich Power-Methode; gleiche Quellen in
  Prognose und Kaufquoten werden als Überschneidung gekennzeichnet.
- Collector: HT-Abfrage alle zehn Sekunden innerhalb des konfigurierten
  Paper-Fensters, nur bei aktiven Portfolios und im bestehenden Smart Universe.
  Keine zusätzlichen dauerhaften Snapshot-Slots für diese Abfragen.
- Worker: fünf Sekunden Entry-Takt. Ergebnisabfragen laufen im Hintergrund,
  werden pro Spiel zusammengefasst und mit Wiederholungsabstand geprüft.
- Fehlendes oder unbestätigtes Ergebnis bleibt OPEN. Keine Auszahlung und keine
  Freigabe der Reservierung, bis ein eindeutiges Ergebnis oder bestätigtes VOID vorliegt.
- Unbekannte SQLite-Scope-Flags und spätere Verlängerung erzeugen kein geratenes
  HZ2-Ergebnis. Explizit bestätigte Regulation-Scores sind im Settlement unterstützt.
- Ein Abbruch/Unterbruch allein bedeutet nicht automatisch VOID. HT-Korrekturen,
  ungültige Einzelteam-Scores und fehlende Endstände werden sichtbar zurückgestellt.
- Ergebnisse werden auch den gespeicherten NO_BET-Beobachtungen zugeordnet.
- Oberfläche: effektiver Worker-Zustand, Familie, Einstiegsfenster, Regelversion,
  Entscheidungsgründe, Marktquellen, Szenario-P/L und Ergebnisbelege.
- CLI `scripts/run_paper.py --status` und wiederholbarer isolierter Live-Canary.
- App-/Deploy-Version 0.6.2. Research-Version bleibt 0.6.1.1.

## Echte Live-Beobachtung

Tipico-Feed am 05.09.2026 um 10:31:50 Münchner Zeit: HTTP 200, 18 Fußballspiele,
davon Shaanxi Union FC – Jiangxi Dingnan Utd. in der Halbzeit.

Entry um 10:32:57 Münchner Zeit, Event `722524610`, China League 1, China,
Halbzeitstand 0:0. Alle Zahlen stammen aus derselben Detailantwort:

| Kennzahl | Wert |
|---|---:|
| Quote 0 weitere Tore | 4,70 |
| Quote 2+ weitere Tore | 1,75 |
| P1-Break-even, vor Cent-Rundung | 21,5805 % |
| Markt-P1, Normalisierung | 28,5627 % |
| Markt-P1, Power | 33,3675 % |
| Markt-Edge | −6,9822 Prozentpunkte |
| Virtueller Gesamteinsatz | 10,00 € |
| Einsatz 0 / 2+ | 2,71 € / 7,29 € |
| Auszahlung 0 / 2+ | 12,74 € / 12,76 € |
| Quotenalter beim Entry | 0,09246 Sekunden |

MARKET_STRUCTURE: BET, ein virtueller Trade erstellt.
MARKET_ONLY: NO_BET, MINIMUM_P1_BUFFER_NOT_MET.
Keine Fehler bei der Verarbeitung. Der Original-Payload wurde als Zstandard-JSON
archiviert. Der reale Trade war zum Zeitpunkt des Berichts OPEN; sein späteres
Ergebnis wurde nicht erfunden oder durch Testdaten ersetzt.

Lokale Testdatenbank, absichtlich außerhalb der Produktionsdaten:
`work/v062-live-canary-20260905/data/tipico.db`.
Fortsetzung derselben echten Abrechnung:

```powershell
.\work\v01-venv\Scripts\python.exe scripts/paper_canary.py --root work/v062-live-canary-20260905 --phase settlement
```

## Verifizierung

- Vollständige Testsuite: **170 passed, 1 skipped**, 82,23 Sekunden.
- Separate Tests für alle drei HZ2-Ausgänge, Neustart nach ungeklärtem Ergebnis,
  doppelte Worker, unveränderte Entry-Fakten, ungültige Marktlinien, Scope,
  Quote Age, Unterbrechung, Strategieänderungen und Ergebnis-Request-Bündelung.
- Streamlit-AppTest: gespeicherte Entscheidung und Ablauf gerendert;
  globaler Paper-Schalter betätigt und persistierte Änderung überprüft.
- Bestehende Collector-/Storage-/Market-Tests bestanden.
- Compile-Prüfung und `git diff --check` bestanden.
- Zwei bereits deklarierte Abhängigkeiten fehlten im lokalen Test-Venv und wurden
  installiert: scikit-learn und PyYAML. Der Research-Code wurde nicht erweitert.

## Offen / nächste Schritte

- Installation und Live-End-to-End-Abnahme auf CT110 sind von hier nicht verifiziert.
  Collector und Paper-Unit müssen gemeinsam aktualisiert werden. Installer und
  Environment liefern Version 0.6.2, die Paper-Unit startet mit `--interval 5`.
- Reale Final-Abrechnung des obigen Spiels steht noch aus. Die drei Ausgänge sind
  automatisiert mit kontrollierten Daten getestet, das ersetzt keine Live-Abnahme.
- Bereits vom alten Worker erstattete UNRESOLVED-Trades bleiben unverändert und
  werden in der UI als Altbestand gezählt. Ihre rückwirkende Korrektur wäre eine
  gesonderte Ledger-Reconciliation.
- Die Regelbibliothek arbeitet mit mehreren Portfolios; automatische große
  Parameter-Grids, Auswahl-/Locked-Paper-Perioden, Leaderboards mit Unsicherheit
  und Vergleiche zu Einzelwetten sind die nächsten Roadmap-Schritte.
- Keine tausendfach validierte profitable Strategie, keine Live-ML-Aktivierung,
  kein Tennisadapter. Für eine tatsächlich bestätigte Regulation-Angabe bei
  Verlängerung muss der jeweilige Ergebnis-Resolver diesen Beleg liefern;
  die aktuelle Tipico-Abfrage rät keine fehlende 90-Minuten-Angabe.

Die Datenbankmigration ergänzt Tabellen und Spalten. Sie löscht keine bestehenden
Portfolios, Trades oder historischen Daten. Die bestehenden lokalen Löschungen
unter `outputs/` gehören nicht zu dieser Änderung.
