# V0.5.8 – Production Collector Hardening, Performance & Smart Live Universe

Stand: 2026-09-02
Arbeitskopie: `C:\Users\chris\Documents\Codex\2026-08-29\es-x20`
Produktionsziel: CT110 `/opt/wetten/app` – in dieser Umgebung nicht per SSH erreichbar

```text
V058_STATUS = PARTIAL
```

`PARTIAL` ist bewusst gewählt: Die fachliche Implementierung, lokale Regressionen,
Query-Plan-Prüfungen und die additive Datenbankmigration sind verifiziert. Ein
Produktiv-Deployment, der laufende CT110-Service und ein Vorher/Nachher-
Benchmark unter realer Containerlast konnten ohne CT110-Zugriff nicht bestätigt
werden.

## Statusfelder

```text
DATABASE_RECONCILIATION = PASS
STARTUP_RECONCILIATION = PASS
FEED_PLAUSIBILITY_GATE = PASS
ATOMIC_EVENT_TRANSITION = PASS
RECONCILIATION_IDEMPOTENCE = PASS

TERMINAL_ODDS_CLOSE = PASS
ODDS_HISTORY_PRESERVED = PASS
STALE_PREMATCH = PASS
RESCHEDULE_RECOVERY = PASS
NO_LONGER_LIVE_RECOVERY = PASS

COLLECTION_METRICS_CACHE = PASS
DATE_RANGE_SQL = PASS
QUERY_PLAN_OPTIMIZATION = PASS
EVENT_BATCH_PERSISTENCE = PASS
STARTUP_QUERY_OPTIMIZATION = PASS

QUEUE_DIAGNOSTICS = PASS
QUEUE_BACKLOG = UNKNOWN

V03_PERSISTENCE_FIX = PASS

SMART_LIVE_UNIVERSE = PASS
COMPETITION_POLICY = PASS
FOTMOB_COVERAGE_CATALOG = PASS
FOTMOB_DISCOVERY_MODE = PASS
TIPICO_MARKET_CAPABILITY = PASS
SELECTED_MATCH_PRIORITY = PASS

NO_RESEARCH_REGRESSION = PASS
NO_FOTMOB_LIVE_PERSISTENCE = PASS

FULL_TEST_SUITE = PASS
READ_ONLY_REVIEW = PASS
CT110_DEPLOYMENT = UNKNOWN
DATABASE_QUICK_CHECK = PARTIAL
RUNTIME_COLLECTOR_PROGRESS = UNKNOWN
```

## Umgesetzte Änderungen

### Konsistenz und Reconciliation

- `current_event_state` ist die operative Quelle für aktive/terminale Events;
  `events` bleibt die dauerhafte Metadatenebene und `event_states` append-only.
- Der erste valide Livefeed eines frisch gestarteten `EventService` lädt die
  persistierten aktiven IDs genau einmal und reconciliiert fehlende Events als
  `NO_LONGER_LIVE`.
- HTTP-/Parser-/Struktur-/Providerfehler und verdächtig leere Feeds dürfen keine
  globale Reconciliation auslösen. Der nächste valide Poll versucht erneut.
- Feedpersistierung, History, Current-State, Event-Reparatur und das Schließen
  offener aktueller Quoten laufen in einer `BEGIN IMMEDIATE`-Transaktion.
- Rollback, fehlende `events`-Zeile, FINISHED-Sperre, idempotentes
  `NO_LONGER_LIVE`, glaubwürdige Live-Recovery und echte zukünftige
  Reschedules sind abgedeckt.
- Terminale aktuelle Quoten werden auf `stopped`, `available = 0` und
  `odds = NULL` gesetzt. `odds_history`, Snapshots, Outbox, States, Raw-Daten
  und Parquet-Archive werden nicht gelöscht.
- Der bekannte V0.3-Fehler `27 values for 26 columns` ist durch die korrigierte
  Placeholder-Anzahl behoben und durch einen Persistenz-Regresstest geschützt.

### Performance und Betrieb

- `collection_metrics_for_date()` nutzt UTC-Halbintervalle statt
  `substr(..., 1, 10)`-Prädikaten und aggregiert Snapshot-Kennzahlen.
- Der Collector cached Coverage-Metriken standardmäßig 30 Sekunden; relevante
  Snapshot-/Exportänderungen invalidieren gezielt. `force_refresh=True` bleibt
  verfügbar.
- Neue Laufzeitmetriken trennen Netzwerk, Parsing, Persistierung,
  Reconciliation, Statuslaufzeit sowie optionale SQL-/Commit-Zähler.
- Queue-Diagnostik enthält `queue_due`, `queue_future`,
  `oldest_due_age_seconds`, Snapshot-Typen, Duplikate, Pending-Key-Anzahl und
  den nächsten Fälligkeitszeitpunkt.
- Es gibt keine globale Poll-Verlangsamung, keine Worker-Erhöhung ohne Messung
  und keine Abschwächung der SQLite-Durability. WAL bleibt aktiv.

### Smart Live Universe

- Der vollständige Tipico-Livefeed bleibt das Radar für alle Events.
- Die RAM-basierte Capability-Entscheidung klassifiziert `P0_SELECTED`,
  `P1_STRATEGY_ELIGIBLE`, `P2_DISCOVERY`, `P3_MINIMAL` und `P4_IGNORE`.
- Jugend-, Reserve- und Freundschaftswettbewerbe werden für teure
  Hintergrundpfade ausgeschlossen; Frauenfußball wird nicht pauschal
  ausgeschlossen.
- `fotmob_coverage_catalog` wird aus dem vorhandenen historischen Index nach
  Liga und Saison abgeleitet und kennt `FULL`, `DISCOVERY` und `NO_DATA`.
- `tipico_market_capability` misst die Verfügbarkeit der Strategie-Märkte pro
  Wettbewerb. Neue Ligen/Saisons bleiben im kontrollierten Discovery-Modus.
- P2-Probes sind pro Wettbewerb TTL-begrenzt. P3/P4 überspringen routinemäßige
  Detailpfade, während Feed und Current-State vollständig erhalten bleiben.
- Das V0.5.7-Auswahlmatch kann weiterhin P0 übersteuern; FotMob-Live-Daten
  bleiben RAM-only und werden nicht in SQLite/Parquet persistiert.

Die Migration der beiden neuen Capability-Tabellen ist additiv und entfernt
keine bestehenden historischen Tabellen oder Daten.

## Performance-Tabelle

Die Produktionswerte aus der Spec sind die CT110-Baseline. Nachher-Werte für
CPU, RSS, logische Reads, Syscalls und den laufenden Collector sind ohne CT110
nicht messbar und werden deshalb nicht geschätzt.

| Metrik | Vorher (CT110) | Nachher | Veränderung |
|---|---:|---:|---:|
| Collector CPU/Core | ca. 48,6 % | nicht gemessen | — |
| Collector RSS | ca. 281 MiB | nicht gemessen | — |
| logical reads / 30 s | ca. 759 MB | nicht gemessen | — |
| read syscalls / 30 s | ca. 185.000 | nicht gemessen | — |
| SQL statements / Coverage-Aufruf | 13 Einzelabfragen | 7, isoliert lokal gemessen | 6 weniger pro Coverage-Aufruf |
| commits / Live-Poll | nicht separat erfasst | 1 Batch-Commit im lokalen Batch-Test | Produktionsvergleich offen |
| status runtime | nicht separat erfasst | 0,788 ms initial / 0,049 ms cached, isoliert lokal | Produktionsvergleich offen |
| collection metrics runtime | nicht separat erfasst | 0,366 ms initial / 0,090 ms forced, isoliert lokal | Produktionsvergleich offen |
| persistence runtime | nicht separat erfasst | 0,858 ms für 1 Fixture-Event, isoliert lokal | Produktionsvergleich offen |

Die `13 → 7`-Angabe ist ein reproduzierbarer Codepfad-/Trace-Vergleich der
Coverage-Funktion: Der alte Stand führte 13 SQL-Ausführungen inklusive Outbox-
und Exportzeitpunkt-Abfragen aus; der neue Stand führt 7 Ausführungen aus.
Das ist kein behaupteter CT110-Durchsatzwert.

Zusätzlicher lokaler SQL-Trace des neuen Batchpfads:

```text
empty validated batch: 3 statements, 1 transaction, 1 commit, 0 rollbacks
one fixture event: 12 statements, 1 transaction, 1 commit, 0 rollbacks
```

Lokaler Status-/Timing-Smoke-Test auf einer temporären Datenbank:

```text
status() initial: 0.788 ms
status() cached: 0.049 ms
collection_metrics initial: 0.366 ms
collection_metrics forced: 0.090 ms
one-event batch persistence: 0.858 ms
```

Diese Werte sind reale isolierte Windows-Testwerte, keine CT110-
Produktionsmessung und kein Ersatz für den noch offenen Lastbenchmark.

## Query-Plan-Review

| Hotpath | Vorher | Nachher |
|---|---|---|
| Events/Competitions nach Datum | Funktionsprädikat und Full Scan | Halbintervalle mit `first_seen_at`/`last_seen_at`-Indizes |
| aktive Current-State-IDs | Full Scan | Status-/Perioden-Indizes, Multi-Index-Plan |
| stale Pre-Match | Full Scan-Risiko | Status-/Perioden-Indizes plus Event-PK-Lookup |
| `match_results.finished_at` | kein gezielter Datumsindex | `idx_match_results_finished_at` |
| `paper_trades.created_at` | nicht zielgerichteter Plan | `idx_paper_trades_created_at` |
| Snapshot-Outbox | bestehender Pending-Index | bestehender Covering-Pending-Index bleibt aktiv |
| Current Canonical Outcomes | bestehender Event/Typ-Index | bestehender Event/Typ-Index bleibt aktiv |

Der zunächst geprüfte, aber nicht hilfreiche zusätzliche Kickoff-Index wurde
nicht behalten. Es wurden keine pauschalen Indexmengen oder
Durability-Abschwächungen eingeführt.

## Queue-Status

Lokaler Diagnostiktest mit einer fälligen und einer zukünftigen Aufgabe:

```text
queue_depth = 2
queue_due = 1
queue_future = 1
oldest_due_age_seconds >= 0
queue_by_snapshot_type = {HALFTIME: 1, MINUTE_60: 1}
```

Eine echte Bewertung `NORMAL SCHEDULED QUEUE` versus `REAL BACKLOG` ist erst
nach einem Lauf unter CT110-Last möglich. Deshalb:

```text
QUEUE_BACKLOG = UNKNOWN
```

## Storage und Datenintegrität

### Lokale Arbeitskopie

Read-only geprüft am 2026-09-02:

```text
SQLite tipico.db       = 265,637,888 bytes
SQLite WAL             = 420,800,352 bytes
SQLite SHM             =     819,200 bytes
PRAGMA quick_check     = ok
journal_mode           = wal
synchronous            = 2 (NORMAL)
events                 = 680
event_states           = 13,907
current_event_state    = 161
snapshots              = 0
snapshot_outbox        = 0
strategy_evaluations   = 34
paper_trades           = 0
```

Der lokale Bestand enthält noch keine V0.5.8-Catalogtabellen, weil die
Prüfung read-only gegen die alte DB-Datei erfolgte. Der normale schreibbare
`Database`-Start legt `fotmob_coverage_catalog` und
`tipico_market_capability` per `CREATE TABLE IF NOT EXISTS` additiv an; dies
ist durch die V0.5.8-Tests auf temporären Datenbanken verifiziert.

Die bestehende lokale FotMob-Archivmessung vor V0.5.8 lag bei ungefähr
180.304 Dateien und 3.890.440.625 Bytes. Es wurden keine Dateien gelöscht oder
umstrukturiert.

### CT110

```text
root total/used/free     = UNKNOWN (kein Zugriff)
FotMob archive           = Spec-Baseline ca. 6,5 GiB
SQLite/data              = Spec-Baseline ca. 870 MiB
gesamt                   = Spec-Baseline ca. 7,4 GiB
```

Empfehlung: `KEEP` für den aktuellen Bestand während der Validierung, danach
kontrolliert ein separates Archive-Mount oder eine Root-Vergrößerung prüfen.
Keine automatische Compaction, kein Löschen und keine Mountänderung wurden
durchgeführt.

## Tests und Review

Ausgeführt nach der letzten Codeänderung:

```text
python -m py_compile config.py services/event_service.py services/collector.py storage/database.py tests/test_collector.py
git diff --check
python -m pytest -q
```

Ergebnis:

```text
105 passed in 45.65s
```

Zusätzliche lokale V0.5.8-Prüfung nach dem letzten Randfallfix:

```text
python -m pytest -q tests/test_v058.py
10 passed in 0.64s
```

Der read-only Diff-Review deckte Transaktionsgrenzen, Rollback, fehlende
Metadatenzeilen, stale Reopen, Status-Cache, Query-Pläne, Queue-Diagnostik,
Capability-False-Negatives und den V0.5.7-FotMob-Livepfad ab. Dabei wurde ein
Randfall in der Rekonstruktion aus einer vorhandenen `events`-Zeile ohne
Current-State korrigiert; die Tests wurden danach erneut ausgeführt.

Die bestehende lokale DB wurde zusätzlich schreibgeschützt in eine temporäre
Kopie gesichert. Darauf liefen die additive V0.5.8-Schemainitialisierung, die
neuen Tabellenprüfungen und `PRAGMA quick_check` erfolgreich durch; die
Originaldatei wurde dabei nicht verändert.

## Git-Zustand

```text
branch                 = main
initial/current commit = b4cac0d981b20706e8df32be31e8823a7956992a
V0.5.8 commit          = keiner; Arbeitsbaum enthält die Änderungen
remote                 = https://github.com/lpzero13/Sportwetten.git
push                   = nicht ausgeführt
```

Der Arbeitsbaum enthielt bereits uncommittete V0.5.7-Änderungen. Diese wurden
bewahrt und nicht zurückgesetzt. Die V0.5.8-Änderungen sind darin enthalten;
ein Commit oder GitHub-Push erfolgt erst auf ausdrücklichen Wunsch.

## Offene Produktionsschritte

1. Arbeitsbaum sichern und produktive DB `/var/lib/wetten/data/tipico.db`
   sichern.
2. Nur eine Collector-Instanz sicherstellen.
3. Deployment auf CT110 und kontrollierten Neustart durchführen.
4. `collector_status.json` mit 15–30 Sekunden Abstand prüfen.
5. `PRAGMA quick_check`, terminale offene Quoten, Logs und V0.3-Warnung prüfen.
6. CPU, RSS, Reads, Syscalls, Queue und Smart-Universe-Verteilung unter
   vergleichbarer Last messen.

Bis diese Schritte erfolgt sind, bleibt der Gesamtstatus korrekt `PARTIAL`.
