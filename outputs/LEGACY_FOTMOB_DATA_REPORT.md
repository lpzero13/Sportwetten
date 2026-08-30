# LEGACY_FOTMOB_DATA_REPORT

## Scope and safety

- Input: `C:\Programmieren\Fussball\Daten Sammler\AntiGrav\backend\data\sniper_football.db`
- Read mode: SQLite `mode=ro`; no in-place migration was performed.
- Backup: `C:\Users\chris\Documents\Codex\2026-08-29\es-x20\work\v053-legacy-backup\sniper_football.db`
- Historical schema target: `fotmob_historical_v1`

## League inventory

| League ID | Name | Matches | Min date | Max date | HT score | FT score | HT xG | HT shots | HT SOT | HT BC | HT corners | 60' coverage | FotMob ID |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 38 | AUT - Bundesliga | 206 | 2020-06-28 | 2025-12-14 | 206 | 206 | 206 | 206 | 206 | 206 | 206 | {"score": 206, "xg": 206, "shots": 206, "shots_on_target": 206, "corners": 206} | 206 |
| 40 | BEL - First Division A, BEL - Pro League | 480 | 2020-02-01 | 2026-02-01 | 480 | 480 | 479 | 479 | 479 | 479 | 479 | {"score": 480, "xg": 480, "shots": 480, "shots_on_target": 480, "corners": 480} | 480 |
| 42 | INT - Champions League | 182 | 2024-02-13 | 2026-01-28 | 182 | 182 | 182 | 182 | 182 | 182 | 182 | {"score": 182, "xg": 182, "shots": 182, "shots_on_target": 182, "corners": 182} | 182 |
| 46 | DEN - Superligaen | 189 | 2024-02-16 | 2025-12-08 | 189 | 189 | 189 | 189 | 189 | 189 | 189 | {"score": 189, "xg": 189, "shots": 189, "shots_on_target": 189, "corners": 189} | 189 |
| 47 | ENG - Premier League | 676 | 2019-02-23 | 2026-02-02 | 676 | 676 | 675 | 675 | 675 | 675 | 675 | {"score": 676, "xg": 676, "shots": 676, "shots_on_target": 676, "corners": 676} | 676 |
| 48 | ENG - Championship | 961 | 2019-03-30 | 2026-01-31 | 961 | 961 | 958 | 958 | 958 | 958 | 958 | {"score": 960, "xg": 960, "shots": 960, "shots_on_target": 960, "corners": 960} | 961 |
| 53 | FRA - Ligue 1 | 518 | 2019-01-15 | 2026-02-01 | 518 | 518 | 514 | 514 | 514 | 514 | 514 | {"score": 517, "xg": 517, "shots": 517, "shots_on_target": 517, "corners": 517} | 518 |
| 54 | GER - Bundesliga | 545 | 2020-02-22 | 2026-02-01 | 545 | 545 | 542 | 542 | 542 | 542 | 542 | {"score": 545, "xg": 545, "shots": 545, "shots_on_target": 545, "corners": 545} | 545 |
| 55 | ITA - Serie A | 680 | 2024-01-05 | 2026-02-02 | 680 | 680 | 680 | 680 | 680 | 680 | 680 | {"score": 680, "xg": 680, "shots": 680, "shots_on_target": 680, "corners": 680} | 680 |
| 57 | NED - Eredivisie | 507 | 2019-03-30 | 2026-02-01 | 507 | 507 | 507 | 507 | 507 | 507 | 507 | {"score": 507, "xg": 507, "shots": 507, "shots_on_target": 507, "corners": 507} | 507 |
| 61 | POR - Liga Portugal | 519 | 2020-02-22 | 2026-02-02 | 519 | 519 | 519 | 519 | 519 | 519 | 519 | {"score": 519, "xg": 519, "shots": 519, "shots_on_target": 519, "corners": 519} | 519 |
| 63 | NOR - Eliteserien, RUS - Premier League | 144 | 2025-07-18 | 2025-12-07 | 144 | 144 | 144 | 144 | 144 | 144 | 144 | {"score": 144, "xg": 144, "shots": 144, "shots_on_target": 144, "corners": 144} | 144 |
| 64 | SCO - Premiership | 284 | 2024-01-02 | 2026-02-01 | 284 | 284 | 284 | 284 | 284 | 284 | 284 | {"score": 284, "xg": 284, "shots": 284, "shots_on_target": 284, "corners": 284} | 284 |
| 67 | SWE - Allsvenskan | 719 | 2019-04-21 | 2025-11-09 | 719 | 719 | 480 | 480 | 480 | 480 | 480 | {"score": 719, "xg": 719, "shots": 719, "shots_on_target": 719, "corners": 719} | 719 |
| 69 | SUI - Super League | 282 | 2019-03-16 | 2026-02-01 | 282 | 282 | 281 | 281 | 281 | 281 | 281 | {"score": 282, "xg": 282, "shots": 282, "shots_on_target": 282, "corners": 282} | 282 |
| 71 | TUR - Süper Lig | 583 | 2019-04-07 | 2026-02-02 | 583 | 583 | 583 | 583 | 583 | 583 | 583 | {"score": 582, "xg": 582, "shots": 582, "shots_on_target": 582, "corners": 582} | 583 |
| 86 | ITA - Serie B | 471 | 2024-08-16 | 2026-02-01 | 471 | 471 | 81 | 470 | 470 | 470 | 470 | {"score": 471, "xg": 471, "shots": 471, "shots_on_target": 471, "corners": 471} | 471 |
| 87 | ESP - La Liga, ESP - LaLiga | 661 | 2020-02-08 | 2026-02-02 | 661 | 661 | 659 | 659 | 659 | 659 | 659 | {"score": 661, "xg": 661, "shots": 661, "shots_on_target": 661, "corners": 661} | 661 |
| 108 | ENG - League One | 699 | 2024-01-01 | 2026-01-31 | 699 | 699 | 699 | 699 | 699 | 699 | 699 | {"score": 697, "xg": 697, "shots": 697, "shots_on_target": 697, "corners": 697} | 699 |
| 109 | ENG - League Two | 680 | 2024-01-01 | 2026-01-31 | 680 | 680 | 680 | 680 | 680 | 680 | 680 | {"score": 679, "xg": 679, "shots": 679, "shots_on_target": 679, "corners": 679} | 680 |
| 110 | FRA - Ligue 2 | 168 | 2024-09-13 | 2026-01-31 | 168 | 168 | 51 | 168 | 168 | 168 | 168 | {"score": 168, "xg": 168, "shots": 168, "shots_on_target": 168, "corners": 168} | 168 |
| 111 | AUS - A-League | 161 | 2024-09-01 | 2024-12-23 | 161 | 161 | 0 | 161 | 161 | 161 | 161 | {"score": 161, "xg": 161, "shots": 161, "shots_on_target": 161, "corners": 161} | 161 |
| 112 | ARG - Liga Profesional | 930 | 2024-05-10 | 2026-02-03 | 930 | 930 | 928 | 928 | 928 | 928 | 928 | {"score": 930, "xg": 930, "shots": 930, "shots_on_target": 930, "corners": 930} | 930 |
| 113 | AUS - A-League | 280 | 2024-01-01 | 2026-02-01 | 280 | 280 | 280 | 280 | 280 | 280 | 280 | {"score": 280, "xg": 280, "shots": 280, "shots_on_target": 280, "corners": 280} | 280 |
| 126 | IRE - Premier Division | 180 | 2025-02-14 | 2025-11-01 | 180 | 180 | 180 | 180 | 180 | 180 | 180 | {"score": 180, "xg": 180, "shots": 180, "shots_on_target": 180, "corners": 180} | 180 |
| 130 | UNI - MLS, USA - MLS | 1371 | 2019-05-29 | 2025-12-06 | 1371 | 1371 | 1068 | 1068 | 1068 | 1068 | 1068 | {"score": 1370, "xg": 1370, "shots": 1370, "shots_on_target": 1370, "corners": 1370} | 1371 |
| 132 | ENG - FA Cup | 150 | 2024-01-04 | 2026-01-20 | 150 | 150 | 150 | 150 | 150 | 150 | 150 | {"score": 150, "xg": 150, "shots": 150, "shots_on_target": 150, "corners": 150} | 150 |
| 133 | ENG - EFL Cup | 16 | 2024-01-09 | 2026-01-14 | 16 | 16 | 16 | 16 | 16 | 16 | 16 | {"score": 16, "xg": 16, "shots": 16, "shots_on_target": 16, "corners": 16} | 16 |
| 134 | FRA - Coupe de France | 142 | 2024-01-05 | 2026-01-13 | 142 | 142 | 142 | 142 | 142 | 142 | 142 | {"score": 142, "xg": 142, "shots": 142, "shots_on_target": 142, "corners": 142} | 142 |
| 135 | GRE - Super League 1 | 48 | 2025-03-02 | 2026-02-01 | 48 | 48 | 48 | 48 | 48 | 48 | 48 | {"score": 48, "xg": 48, "shots": 48, "shots_on_target": 48, "corners": 48} | 48 |
| 138 | ESP - Copa del Rey | 117 | 2024-01-06 | 2026-01-15 | 117 | 117 | 117 | 117 | 117 | 117 | 117 | {"score": 117, "xg": 117, "shots": 117, "shots_on_target": 117, "corners": 117} | 117 |
| 139 | ESP - Supercopa de España | 3 | 2026-01-07 | 2026-01-11 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | {"score": 3, "xg": 3, "shots": 3, "shots_on_target": 3, "corners": 3} | 3 |
| 140 | ESP - La Liga 2, ESP - LaLiga2 | 289 | 2024-09-01 | 2026-02-02 | 289 | 289 | 87 | 289 | 289 | 289 | 289 | {"score": 289, "xg": 289, "shots": 289, "shots_on_target": 289, "corners": 289} | 289 |
| 141 | ITA - Coppa Italia | 30 | 2024-01-02 | 2026-01-27 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | {"score": 30, "xg": 30, "shots": 30, "shots_on_target": 30, "corners": 30} | 30 |
| 146 | GER - 2. Bundesliga | 513 | 2024-01-19 | 2026-02-01 | 513 | 513 | 513 | 513 | 513 | 513 | 513 | {"score": 513, "xg": 513, "shots": 513, "shots_on_target": 513, "corners": 513} | 513 |
| 165 | TUR - 1. Lig | 9 | 2025-01-03 | 2025-01-05 | 9 | 9 | 9 | 9 | 9 | 9 | 9 | {"score": 9, "xg": 9, "shots": 9, "shots_on_target": 9, "corners": 9} | 9 |
| 186 | POR - Taça de Portugal | 2 | 2025-12-17 | 2026-01-11 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | {"score": 2, "xg": 2, "shots": 2, "shots_on_target": 2, "corners": 2} | 2 |
| 187 | POR - League Cup | 4 | 2025-12-04 | 2026-01-10 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | {"score": 4, "xg": 4, "shots": 4, "shots_on_target": 4, "corners": 4} | 4 |
| 196 | POL - Ekstraklasa | 22 | 2025-12-01 | 2026-02-02 | 22 | 22 | 22 | 22 | 22 | 22 | 22 | {"score": 22, "xg": 22, "shots": 22, "shots_on_target": 22, "corners": 22} | 22 |
| 200 | POL - Superpuchar Polski | 1 | 2025-07-13 | 2025-07-13 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |
| 207 | FRA - Trophée des champions | 1 | 2026-01-08 | 2026-01-08 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |
| 209 | GER - DFB Pokal | 22 | 2024-01-30 | 2025-12-03 | 22 | 22 | 22 | 22 | 22 | 22 | 22 | {"score": 22, "xg": 22, "shots": 22, "shots_on_target": 22, "corners": 22} | 22 |
| 222 | ITA - Supercoppa | 3 | 2025-12-18 | 2025-12-22 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | {"score": 3, "xg": 3, "shots": 3, "shots_on_target": 3, "corners": 3} | 3 |
| 223 | JPN - J. League | 380 | 2025-02-14 | 2025-12-06 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | {"score": 380, "xg": 380, "shots": 380, "shots_on_target": 380, "corners": 380} | 380 |
| 230 | MEX - Liga MX | 36 | 2026-01-10 | 2026-02-01 | 36 | 36 | 36 | 36 | 36 | 36 | 36 | {"score": 36, "xg": 36, "shots": 36, "shots_on_target": 36, "corners": 36} | 36 |
| 242 | DEN - DBU Pokalen | 8 | 2025-12-03 | 2025-12-14 | 8 | 8 | 8 | 8 | 8 | 8 | 8 | {"score": 8, "xg": 8, "shots": 8, "shots_on_target": 8, "corners": 8} | 8 |
| 247 | ENG - Community Shield | 2 | 2024-08-10 | 2025-08-10 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | {"score": 2, "xg": 2, "shots": 2, "shots_on_target": 2, "corners": 2} | 2 |
| 264 | BEL - First Division B | 65 | 2025-12-05 | 2026-02-01 | 65 | 65 | 33 | 65 | 65 | 65 | 65 | {"score": 65, "xg": 65, "shots": 65, "shots_on_target": 65, "corners": 65} | 65 |
| 268 | BRA - Serie A | 825 | 2019-05-01 | 2026-01-30 | 825 | 825 | 825 | 825 | 825 | 825 | 825 | {"score": 825, "xg": 825, "shots": 825, "shots_on_target": 825, "corners": 825} | 825 |
| 274 | COL - Primera A | 262 | 2025-07-11 | 2026-02-03 | 262 | 262 | 262 | 262 | 262 | 262 | 262 | {"score": 262, "xg": 262, "shots": 262, "shots_on_target": 262, "corners": 262} | 262 |
| 519 | EGY - Premier League | 17 | 2025-12-03 | 2026-01-30 | 17 | 17 | 17 | 17 | 17 | 17 | 17 | {"score": 17, "xg": 17, "shots": 17, "shots_on_target": 17, "corners": 17} | 17 |
| 536 | SAU - Saudi Pro League | 412 | 2024-02-07 | 2026-02-02 | 412 | 412 | 412 | 412 | 412 | 412 | 412 | {"score": 412, "xg": 412, "shots": 412, "shots_on_target": 412, "corners": 412} | 412 |
| 537 | SOU - Premier Soccer League | 24 | 2025-12-02 | 2026-02-01 | 24 | 24 | 24 | 24 | 24 | 24 | 24 | {"score": 24, "xg": 24, "shots": 24, "shots_on_target": 24, "corners": 24} | 24 |
| 8814 | BRA - Serie B | 380 | 2025-04-04 | 2025-11-23 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | {"score": 380, "xg": 380, "shots": 380, "shots_on_target": 380, "corners": 380} | 380 |
| 8924 | GER - Super Cup | 1 | 2025-08-16 | 2025-08-16 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |
| 8984 | THA - Premier League | 50 | 2025-12-05 | 2026-02-01 | 50 | 50 | 50 | 50 | 50 | 50 | 50 | {"score": 50, "xg": 50, "shots": 50, "shots_on_target": 50, "corners": 50} | 50 |
| 9080 | SOU - K League 1 | 228 | 2025-02-15 | 2025-11-30 | 228 | 228 | 228 | 228 | 228 | 228 | 228 | {"score": 228, "xg": 228, "shots": 228, "shots_on_target": 228, "corners": 228} | 228 |
| 9081 | GER - Bundesliga Qualification | 4 | 2024-05-23 | 2025-05-26 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | {"score": 4, "xg": 4, "shots": 4, "shots_on_target": 4, "corners": 4} | 4 |
| 9134 | UNI - NWSL | 189 | 2025-03-15 | 2025-11-23 | 189 | 189 | 189 | 189 | 189 | 189 | 189 | {"score": 188, "xg": 188, "shots": 188, "shots_on_target": 188, "corners": 188} | 189 |
| 9227 | ENG - WSL | 30 | 2025-12-06 | 2026-02-01 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | {"score": 30, "xg": 30, "shots": 30, "shots_on_target": 30, "corners": 30} | 30 |
| 9381 | ARG - Super Cup | 1 | 2025-09-06 | 2025-09-06 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |
| 9471 | AUS - Australia Cup | 30 | 2025-07-22 | 2025-10-04 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | {"score": 30, "xg": 30, "shots": 30, "shots_on_target": 30, "corners": 30} | 30 |
| 9478 | IND - Indian Super League | 152 | 2019-02-24 | 2025-04-12 | 152 | 152 | 152 | 152 | 152 | 152 | 152 | {"score": 152, "xg": 152, "shots": 152, "shots_on_target": 152, "corners": 152} | 152 |
| 9495 | AUS - A-League Women | 51 | 2025-12-05 | 2026-02-01 | 51 | 51 | 0 | 51 | 51 | 51 | 51 | {"score": 51, "xg": 51, "shots": 51, "shots_on_target": 51, "corners": 51} | 51 |
| 9677 | FRA - Première Ligue Féminine | 30 | 2025-12-05 | 2026-02-01 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | {"score": 30, "xg": 30, "shots": 30, "shots_on_target": 30, "corners": 30} | 30 |
| 9734 | GER - 2. Bundesliga Qualification | 4 | 2024-05-24 | 2025-05-27 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | {"score": 4, "xg": 4, "shots": 4, "shots_on_target": 4, "corners": 4} | 4 |
| 10007 | ARG - Copa de la Liga Profesional | 203 | 2024-01-25 | 2024-05-05 | 203 | 203 | 202 | 202 | 202 | 202 | 202 | {"score": 203, "xg": 203, "shots": 203, "shots_on_target": 203, "corners": 203} | 203 |
| 10053 | ARG - Trofeo de Campeones | 1 | 2025-12-20 | 2025-12-20 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |
| 10077 | BRA - Supercopa do Brasil | 1 | 2026-02-01 | 2026-02-01 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |
| 10082 | ENG - FA Cup (Women) | 15 | 2026-01-15 | 2026-01-18 | 15 | 15 | 15 | 15 | 15 | 15 | 15 | {"score": 15, "xg": 15, "shots": 15, "shots_on_target": 15, "corners": 15} | 15 |
| 10215 | POR - Liga Portugal Qualification | 4 | 2024-05-25 | 2025-06-01 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | {"score": 4, "xg": 4, "shots": 4, "shots_on_target": 4, "corners": 4} | 4 |
| 10244 | BRA - Paulista A1 | 45 | 2026-01-10 | 2026-02-02 | 45 | 45 | 45 | 45 | 45 | 45 | 45 | {"score": 45, "xg": 45, "shots": 45, "shots_on_target": 45, "corners": 45} | 45 |
| 10832 | ARG - Supercopa | 1 | 2025-07-08 | 2025-07-08 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |
| 11039 | MEX - Campeón de Campeones | 1 | 2025-07-21 | 2025-07-21 | 1 | 1 | 0 | 1 | 1 | 1 | 1 | {"score": 1, "xg": 1, "shots": 1, "shots_on_target": 1, "corners": 1} | 1 |

## Bundesliga / League 54

| Metric | Value |
| --- | --- |
| Matches | 545 |
| Date range | 2020-02-22 – 2026-02-01 |
| Complete HT core | 542 |
| HT core coverage | {"xg": 542, "shots": 542, "shots_on_target": 542, "big_chances": 542, "corners": 542} |
| Full core coverage | {"xg": 545, "shots": 545, "shots_on_target": 545, "big_chances": 545, "corners": 545} |
| 60-minute coverage | {"score": 545, "xg": 545, "shots": 545, "shots_on_target": 545, "corners": 545} |
| Season counts | {"2019/2020": 5, "2023/2024": 163, "2024/2025": 306, "2025/2026": 71} |
| Expected seasons absent in source | 2020/21, 2021/22, 2022/23 |

## Fresh public-page check

- Endpoint: `GET https://www.fotmob.com/match/{match_id}`
- Mode: real public requests
- Client metrics: `{"requests": 5, "successes": 5, "errors": 0, "retries": 0, "http_failures": 0, "rate_limit_responses": 0, "parse_failures": 0, "average_response_ms": 85.4, "median_response_ms": 45, "p95_response_ms": 229.0, "last_response_ms": 45, "last_status_code": 200, "status_counts": {"200": 5}, "last_endpoint": "https://www.fotmob.com/match/4534544", "last_error": null, "last_request_at": "2026-08-30T15:24:50.729174+00:00", "last_success_at": "2026-08-30T15:24:50.799490+00:00", "average_payload_bytes": 1092096.2}`

| ID | Status | MATCH | MISMATCH | Legacy missing | Current missing | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 4534540 | MATCH | 16 | 0 | 0 | 0 | field-level comparison |
| 4534541 | MATCH | 16 | 0 | 0 | 0 | field-level comparison |
| 4534542 | MATCH | 16 | 0 | 0 | 0 | field-level comparison |
| 4534543 | MATCH | 16 | 0 | 0 | 0 | field-level comparison |
| 4534544 | MATCH | 16 | 0 | 0 | 0 | field-level comparison |

Field classifications are restricted to `MATCH`, `MISMATCH`, `LEGACY_MISSING` and `CURRENT_MISSING`; details are stored in the JSON emitted by the script.
