# Tipico Storage V0.4.2

`TIPICO_STORAGE_V042_STATUS = PARTIAL`

## Umgesetzt

- UI-/API-Refreshes aktualisieren Current State und erzeugen keine historische
  Snapshot-, Odds- oder Canonical-History-Row.
- Der Collector verwendet die zehn fachlichen Slots
  `PRE_KICKOFF`, `HALFTIME`, `HT_STABLE`, `MINUTE_60`, `MINUTE_70`,
  `MINUTE_80`, `FIRST_H2_GOAL_REOPEN`, `MINUTE_85`, `MINUTE_90` und `FINAL`.
- Snapshot-Slots sind idempotent; SQLite schützt neue Standard-Slots zusätzlich
  mit Unique Index bzw. Trigger.
- `FINAL` schreibt den expliziten `match_results`-Datensatz inklusive
  `second_half_goals` und `0`/`1`/`2_PLUS`.
- Die SQLite-Outbox exportiert flache, schema-versionierte Rows in zstd-
  komprimierte, dateipartitionierte Parquet-Batches mit temporärer Datei,
  fsync und Wiederaufnahme nach einem Crash.
- Raw wird nur für Paper-Entry, Parser-/Mapping-Debug und optional HT dauerhaft
  geschrieben; Debug-Raw ist über den täglichen Cleanup auf sieben Tage begrenzt.
- Paper-Entry-Raw, Trades, Settlements, Ledger und Match Results werden vom
  Cleanup nicht entfernt.
- `scripts/migrate_v042_storage.py` führt EXPORT → VERIFY → REPORT ohne
  automatische Löschung aus. Der Lauf über den aktuellen Projektbestand ist in
  [STORAGE_MIGRATION_REPORT.md](STORAGE_MIGRATION_REPORT.md) dokumentiert.
- Data / Debug enthält Storage Overview mit SQLite-, Parquet-, Raw-, Outbox-,
  Tageswachstums- und Snapshot-pro-Finished-Match-Kennzahlen.
- Der Proxmox-Installer legt `/var/lib/wetten/archive` an und aktiviert den
  täglichen `wetten-cleanup.timer`.

## Verifizierung

- Automatisierte Tests: **33 passed**.
- Enthalten sind 100 Current-State-Refreshes ohne Historienwachstum,
  Snapshot-Idempotenz, Parquet-Roundtrip, Minuten- und HZ2-Reopen-Trigger sowie
  FINAL `HT 1:0 / FT 3:1 → second_half_goals=3 / 2_PLUS`.
- Der vorhandene Projektbestand enthielt beim Migrationslauf keine Snapshot-Rows;
  deshalb wurden 0 Rows exportiert und der Report ist dennoch formal konsistent.

## Noch offen für ein vollständiges PASS

- Langlauf-Verifikation mit mindestens 20 real vollständig beobachteten Events
  inklusive tatsächlichem FINAL-Feed und Paper-Settlement.
- Proxmox-Lauf mit installiertem `pyarrow`/`zstandard` und Backup-/Restore-
  Drill des persistenten `/var/lib/wetten`-Archivs.
