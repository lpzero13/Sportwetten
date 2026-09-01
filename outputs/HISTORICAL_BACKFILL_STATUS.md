# Historical Backfill Status

**Status:** `PASS`  
**Zeitraum:** `2024-09-01` bis `2026-08-31` inklusiv (730 Kalendertage)  
**Scope:** FotMob-Tagesfeed aller Länder und Ligen, kein Liga-Filter, 10 Detail-Worker  
**Laufzeit:** `4860.141 Sekunden`  
**Start/Ende UTC:** `2026-09-01T12:52:41.131315+00:00` / `2026-09-01T14:23:45.645794+00:00`

## Zusammenfassung

| Metrik | Wert |
| --- | --- |
| Tagesfeeds | 730 / 730 |
| Feed-Zeilen | 148,801 |
| Eindeutige Spiele | 131,496 |
| Länder / Liga-IDs / Länder-Liga-Schlüssel | 97 / 513 / 518 |
| Saisons | 2024/25, 2025/26, 2026/27 |
| HZ-Daten gespeichert | 45,895 neu, final 49,802 |
| Ohne HZ übersprungen | 71,159 im Lauf |
| Ohne Detaildaten übersprungen | 5 im Lauf |
| API-Requests | 126,641, Erfolg 100.0% |
| Payload | 11.15 GiB (11.97 GB) |
| Speicherzunahme gesamt | 3.90 GiB (4.19 GB) |

Die Tagesfeeds enthalten jedes von FotMob gelieferte Spiel unabhängig von Land, Liga und Uhrzeit. `includeNextDayLateNight=true` bleibt aktiv; diese Einträge sind im Tagesindex markiert. Detailantworten ohne nutzbare FirstHalf-Daten werden nicht als Metrikdataset gespeichert.

## Qualitätschecks

| Check | Ergebnis |
| --- | --- |
| Backfill-Runner | PASS |
| Indexfehler | PASS (0) |
| Warnings | PASS (0) |
| Metric-Parquet-Scan | PASS |
| 429 / 403 / 5xx / Timeout / Parse | INFO (15) |

## Zielverteilung

Die vollständige Verteilung liegt in [TARGET_DISTRIBUTION.csv](TARGET_DISTRIBUTION.csv). Sie trennt bewusst `FETCHED`/HZ-Daten von `SKIPPED_NO_HALFTIME` und `SKIPPED_NO_DATA`; fehlende HZ-Daten werden nicht künstlich als ZERO oder 2PLUS klassifiziert.

| Detailstatus | Target-Klasse | 2H-Tore | Spiele | Anteil |
| --- | --- | --- | --- | --- |
| FETCHED | 0 | 0 | 10,780 | 8.20% |
| FETCHED | 1 | 1 | 16,210 | 12.33% |
| FETCHED | 2_PLUS | 2 | 12,816 | 9.75% |
| FETCHED | 2_PLUS | 3 | 6,443 | 4.90% |
| FETCHED | 2_PLUS | 4 | 2,570 | 1.95% |
| FETCHED | 2_PLUS | 5 | 739 | 0.56% |
| FETCHED | 2_PLUS | 6 | 175 | 0.13% |
| FETCHED | 2_PLUS | 7 | 43 | 0.03% |
| FETCHED | 2_PLUS | 8 | 11 | 0.01% |
| FETCHED | 2_PLUS | 9 | 1 | 0.00% |
| FETCHED | 2_PLUS | 10 | 1 | 0.00% |
| FETCHED | 2_PLUS | 12 | 2 | 0.00% |
| PARTIAL | NOT_AVAILABLE |  | 11 | 0.01% |
| SKIPPED_NO_DATA | NOT_AVAILABLE |  | 5 | 0.00% |
| SKIPPED_NO_HALFTIME | NOT_AVAILABLE |  | 81,689 | 62.12% |

## Ablage der gewünschten Dateien

- [HISTORICAL_LEAGUE_COVERAGE.csv](HISTORICAL_LEAGUE_COVERAGE.csv) – jede beobachtete Länder-/Liga-Kombination
- [HISTORICAL_SEASON_COVERAGE.csv](HISTORICAL_SEASON_COVERAGE.csv) – Saisonabdeckung
- [DATASET_SUMMARY.csv](DATASET_SUMMARY.csv) – physische Dataset-Dateien, Bytes und Zeilen
- [TARGET_DISTRIBUTION.csv](TARGET_DISTRIBUTION.csv) – ZERO/2PLUS bzw. nicht verfügbare Targets
- [METRIC_COVERAGE.csv](METRIC_COVERAGE.csv) – HZ-/FT-Metrikabdeckung
- [STORAGE_REPORT.md](STORAGE_REPORT.md) – gemessene Größen und Hochrechnung

Zusätzlich bleibt [HISTORICAL_BACKFILL_RUN.json](HISTORICAL_BACKFILL_RUN.json) als maschinenlesbarer Laufnachweis erhalten.

## Reproduzierbarkeit

```text
python -m scripts.run_historical_backfill --root . --from-date 2024-09-01 --to-date 2026-08-31 --workers 10
python -m scripts.build_historical_backfill_reports --root . --run-json outputs/HISTORICAL_BACKFILL_RUN.json
```

