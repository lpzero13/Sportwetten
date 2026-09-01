# Historical Backfill – Storage Report

**Zeitraum:** `2024-09-01` bis `2026-08-31` (730 Tage)

SQLite enthält den kleinen, filterbaren Katalog und Queue-/Statusdaten. Die umfangreichen Matchmetriken liegen im kanonischen Parquet-Archiv. WAL/SHM werden für die SQLite-Gesamtgröße mitgerechnet.

| Bereich | Vorher | Nachher | Delta |
| --- | --- | --- | --- |
| SQLite inkl. WAL/SHM | 34.43 MiB (36.10 MB) | 655.10 MiB (686.92 MB) | 620.67 MiB (650.82 MB) |
| Parquet-Archiv | 337.00 MiB (353.37 MB) | 3.62 GiB (3.89 GB) | 3.29 GiB (3.54 GB) |
| Gesamt | 371.43 MiB (389.47 MB) | 4.26 GiB (4.58 GB) | 3.90 GiB (4.19 GB) |
| Archivdateien | 15,077 | 180,304 | +165,227 |

## Gemessene Rate

| Speicher | pro Tag | pro Ø-Monat (30,44 Tage) |
| --- | --- | --- |
| SQLite inkl. WAL/SHM | 870.64 KiB (891.53 kB) | 25.88 MiB (27.14 MB) |
| Parquet | 4.62 MiB (4.85 MB) | 140.65 MiB (147.48 MB) |
| Gesamt | 5.47 MiB (5.74 MB) | 166.53 MiB (174.61 MB) |

## Frischer Erstimport desselben Volumens

Die tatsächliche Delta-Zunahme ist durch bereits vorhandene Daten beeinflusst: 136,691 neue Tagesindexzeilen und 45,906 neue HZ-Archive kamen hinzu. Auf Basis dieser gemessenen Einheitskosten würde ein leerer Erstimport des gesamten Zeitraums ungefähr **4.23 GiB (4.55 GB)** benötigen (SQLite 675.66 MiB (708.48 MB), Parquet 3.57 GiB (3.84 GB)).

| Erstimport-Schätzung | pro Tag | pro Ø-Monat (30,44 Tage) |
| --- | --- | --- |
| SQLite-Katalog | 947.77 KiB (970.52 kB) | 28.17 MiB (29.54 MB) |
| Parquet-Metriken | 5.01 MiB (5.26 MB) | 152.58 MiB (160.00 MB) |
| Gesamt | 5.94 MiB (6.23 MB) | 180.75 MiB (189.54 MB) |

## Physische Dataset-Größen

| Dataset | Dateien | Bytes | physische Zeilen |
| --- | --- | --- | --- |
| events | 49,498 | 645.14 MiB (676.48 MB) | 864,502 |
| historical | 3,579 | 232.76 MiB (244.07 MB) | 49,802 |
| match_core | 49,802 | 1.90 GiB (2.05 GB) | 49,802 |
| period_stats | 49,802 | 429.23 MiB (450.08 MB) | 5,184,046 |
| shots | 27,623 | 452.64 MiB (474.62 MB) | 709,093 |
| sqlite_daily_index | 1 | 253.01 MiB (265.30 MB) | 148,801 |
| sqlite_match_index | 1 | 0 B | 131,793 |

Die Projektion ist eine belastbare Größenordnung, keine harte Obergrenze: Wochenenden, Liga-Mix, vorhandene Provider-Metriken und die Zahl der Shots/Events pro Match verändern die Parquet-Größe.

