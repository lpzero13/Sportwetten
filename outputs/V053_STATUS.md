# V053_STATUS

`FOTMOB_V053_STATUS=PARTIAL`

- Legacy inventory/import: **PASS** (545 valid rows; 542 COMPLETE; 3 PARTIAL; 0 INVALID; 0 duplicates).
- Historical League 54 index: **PASS** (11 seasons; 3366 index records).
- Historical detail queue: **2821** missing IDs (3366 indexed; 545 already archived).
- Fresh legacy cross-check: **PASS** (5 real public match pages).
- Tipico mapping/link validation: **PARTIAL** (6 available German Bundesliga events, 6 confirmed; target 20).
- HALF_TIME enrichment: **PASS** (5/5 snapshots).
- FotMob HT Parquet export: **PASS** (5 snapshots; pending 0).
- Active archive sources: `{"FRESH_FETCH": 5, "LEGACY_IMPORT": 540}`; Parquet bytes: 1220624.

The status is PARTIAL when the supplied Tipico archive cannot provide the specification's 20-event observation set. No links or successful halftime snapshots are fabricated to turn that source limitation into PASS.
