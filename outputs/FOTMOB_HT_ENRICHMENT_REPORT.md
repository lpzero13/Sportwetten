# FOTMOB_HT_ENRICHMENT_REPORT

HALF_TIME-only enrichment. Each successful event performs one FotMob public `/match/{id}` fetch and writes one idempotent `HALFTIME` snapshot.

- Events tested: **5**
- Successful FirstHalf snapshots: **5**
- Result: **PASS**

| Tipico event | Spiel | Resolver | FotMob-ID | Snapshot | stats_period | source_context | captured_live | HT stats | Error |
| --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- |
| 714510510 | SV Elversberg – Bayer Leverkusen | HIGH_CONFIDENCE | 5881146 | HALFTIME | FIRST_HALF | LIVE_HT | 1 | True | — |
| 714606610 | RB Leipzig – Borussia Mönchengladbach | EXACT | 5881150 | HALFTIME | FIRST_HALF | LIVE_HT | 1 | True | — |
| 714606910 | FSV Mainz 05 – SC Paderborn 07 | EXACT | 5881149 | HALFTIME | FIRST_HALF | LIVE_HT | 1 | True | — |
| 714607010 | Union Berlin – Eintracht Frankfurt | EXACT | 5881151 | HALFTIME | FIRST_HALF | LIVE_HT | 1 | True | — |
| 714607110 | 1. FC Köln – TSG Hoffenheim | HIGH_CONFIDENCE | 5881147 | HALFTIME | FIRST_HALF | LIVE_HT | 1 | True | — |

FirstHalf fields are read from `content.stats.Periods.FirstHalf`; All/SecondHalf values are not promoted into the HT columns. FotMob remains informational and does not affect Tipico ranking or paper trading.
