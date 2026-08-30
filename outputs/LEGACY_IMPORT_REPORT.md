# LEGACY_IMPORT_REPORT

Pipeline: Legacy SQLite → read-only adapter → validation/target recalculation → `fotmob_historical_v1` → Parquet.
The source database was never opened for writing. Fresh provider rows have precedence over the imported legacy rows.

- Input: `C:\Programmieren\Fussball\Daten Sammler\AntiGrav\backend\data\sniper_football.db`
- Database: `C:\Users\chris\Documents\Codex\2026-08-29\es-x20\work\v053-validation-final2\data\tipico.db`
- Archive root: `C:\Users\chris\Documents\Codex\2026-08-29\es-x20\work\v053-validation-final2\data\archive`

| Counter | Value |
| --- | ---: |
| Rows input | 545 |
| Rows valid | 545 |
| Rows complete | 542 |
| Rows partial | 3 |
| Rows score-only | 0 |
| Rows invalid | 0 |
| Rows duplicate | 0 |
| Rows imported | 545 |
| Rows skipped | 0 |
| Rows replaced by fresh data | 0 |

## Archive result

- Active archive rows written during import: **545**
- Parquet files touched during import: **9**
- Historical Parquet size after the fresh cross-check: **1179192 bytes**

`second_half_goals` is recalculated from FT total minus HT total. The legacy target is retained only as an audit field and is never used as the training target.

## Index/archive gap

- Indexed League 54 fixture IDs: **3366**
- Already archived unique detail IDs: **545**
- Missing detail queue: **2821**
- First missing IDs: `2272256, 2272257, 2272258, 2272259, 2272261, 2272262, 2272263, 2272264, 2272260, 2272272, 2272271, 2272274, 2272275, 2272277, 2272278, 2272279, 2272276, 2272273, 2272320, 2272316`
