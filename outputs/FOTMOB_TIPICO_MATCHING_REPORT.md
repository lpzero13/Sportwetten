# FOTMOB_TIPICO_MATCHING_REPORT

Resolver chain: Tipico event → persisted competition/country mapping → indexed FotMob fixtures → deterministic team/order/kickoff match.

- FotMob League 54 index rows available: **3366**
- German Tipico Bundesliga events in copied archive: **6**
- Confirmed links: **6**
- Required real-event threshold from V0.5.3: **20**
- Coverage: `EXACT=4`, `HIGH_CONFIDENCE=2`, `AMBIGUOUS=0`, `UNMATCHED=0`
- Wrong auto links in the deterministic control: **0 observed**; the report keeps the links and reasons available for manual review.
- Result: **PARTIAL** — the supplied Tipico archive contains only 6 German events, so the 20-event target is not claimed.

| Tipico event | Spiel | Mapping | Status | FotMob-ID | Confidence | Reason |
| --- | --- | --- | --- | --- | ---: | --- |
| 714510510 | SV Elversberg – Bayer Leverkusen | 54 | HIGH_CONFIDENCE | 5881146 | 0.92 | away_name_or_alias; kickoff_delta_0.0m; competition_exact_or_alias; controlled_fuzzy_0.87/1.00 |
| 714606610 | RB Leipzig – Borussia Mönchengladbach | 54 | EXACT | 5881150 | 1.00 | home_name_or_alias; away_name_or_alias; kickoff_delta_0.2m; competition_exact_or_alias |
| 714606910 | FSV Mainz 05 – SC Paderborn 07 | 54 | EXACT | 5881149 | 1.00 | home_name_or_alias; away_name_or_alias; kickoff_delta_0.3m; competition_exact_or_alias |
| 714607010 | Union Berlin – Eintracht Frankfurt | 54 | EXACT | 5881151 | 1.00 | home_name_or_alias; away_name_or_alias; kickoff_delta_0.9m; competition_exact_or_alias |
| 714607110 | 1. FC Köln – TSG Hoffenheim | 54 | HIGH_CONFIDENCE | 5881147 | 0.92 | home_name_or_alias; kickoff_delta_3.0m; competition_exact_or_alias; controlled_fuzzy_1.00/0.83 |
| 714606510 | Borussia Dortmund – Hamburger SV | 54 | EXACT | 5881145 | 1.00 | home_name_or_alias; away_name_or_alias; kickoff_delta_0.4m; competition_exact_or_alias |

Control: Tipico competition `42301` maps to FotMob `54` / `Bundesliga` / `GER`; Austrian competition `29301` has no link and is never considered by this resolver.
