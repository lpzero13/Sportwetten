# Storage Migration Report V0.4.2

- Zeitpunkt: `2026-08-30T06:20:12.441779+00:00`
- Root: `C:\Users\chris\Documents\Codex\2026-08-29\es-x20`
- Zielarchiv: `C:\Users\chris\Documents\Codex\2026-08-29\es-x20\data\archive\tipico\snapshots`
- Ablauf: `EXPORT → VERIFY → REPORT`
- SQLite-History wurde in diesem Lauf nicht gelöscht.

## Größen

- DB-Größe vorher (inkl. WAL/SHM): **12.126 MB**
- DB-Größe nach Export: **12.126 MB**
- Parquet-Größe dieses Archivs: **0.000 MB**
- Parquet-Dateien: **0**
- Erwartete DB-Größe nach optionalem Cleanup: wird erst nach Backup und explizitem Cleanup gemessen.

## Snapshot-Export

- Exportierte Rows: **0**
- Validierungsstatus: **PASS**

## Rows vor Migration

| Tabelle | Rows |
|---|---:|
| `events` | 519 |
| `event_states` | 13907 |
| `current_event_state` | 0 |
| `markets` | 557 |
| `outcomes` | 1748 |
| `odds_history` | 5390 |
| `competitions` | 163 |
| `snapshots` | 0 |
| `snapshot_outbox` | 0 |
| `match_results` | 0 |
| `market_presence` | 0 |
| `canonical_outcomes` | 5257 |
| `current_canonical_outcomes` | 0 |
| `strategy_evaluations` | 34 |
| `current_strategy_evaluations` | 0 |
| `paper_portfolios` | 0 |
| `paper_trades` | 0 |
| `paper_bankroll_transactions` | 0 |
| `paper_signal_log` | 0 |
| `paper_worker_runs` | 1 |

## Potenziell redundante alte Historie

Diese Rows sind nur Kandidaten für einen späteren, ausdrücklich freigegebenen Cleanup. Paper-Trades, Match-Results und Ledger bleiben erhalten.

| Tabelle | potenziell prüfbare Rows |
|---|---:|
| `event_states` | 13907 |
| `odds_history` | 5390 |
| `market_presence` | 0 |
| `canonical_outcomes` | 5257 |
| `strategy_evaluations` | 34 |

## Validierung

- Row Count, Schlüssel, Schema-Version und Stichprobenquoten sind konsistent.
