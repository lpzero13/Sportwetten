# V0.5.5.1 – Five-Day All-Leagues Real Data Canary Report

- Zeitraum: **2026-08-26 bis 2026-08-30**, inklusiv
- Zeitzone der Daily-Requests: `Europe/Berlin`
- Scope: **ALL_LEAGUES**, kein `--league`-Filter
- Detail-Worker: **10**; globaler Request-Limiter: **0.5 req/s**
- Persistierter Detail-Zeitbereich: `2026-08-31T15:42:59+00:00` bis `2026-08-31T17:41:55+00:00`; elapsed: `7136.1s`

## Gate

- `FIVE_DAY_CANARY`: **PASS**
- `READY_FOR_V06_DATASET`: **PASS**
- ML, Backtesting und Strategielogik wurden in diesem Milestone nicht ausgeführt.

## Gesamtübersicht

| Kennzahl | Wert |
| --- | --- |
| Tagesfeed-Gruppen | 504 |
| Roh-Feed-Einträge | 1755 |
| Feed-Unique-Matches (pro Tag summiert) | 1755 |
| SQLite-Daily-Indexzeilen | 1755 |
| Unique Match IDs im Bereich | 1618 |
| Distinct Länder | 86 |
| Distinct Ligen | 163 |
| Saisonlabels | 2026/27 |
| Next-Day-Einträge | 171 |
| Entfernte Feed-Duplikate | 0 |
| Finished-Unique-Matches | 1607 |
| Detailjobs selected/requested | 1618 |
| Detailrequests attempted, unique | 1618 |
| Detail-Run neu FETCHED | 608 |
| Detailstatus FETCHED | 610 |
| Detailstatus PARTIAL | 0 |
| Detail-Run Queue-Skips | 2 |
| SKIPPED_NO_HALFTIME | 1008 |
| FAILED | 0 |
| Canonical Match-Core-Dateien | 610 |
| Archive-Lesefehler | 0 |

Die Feed-Unique-Zahl ist je Tag dedupliziert und wird hier summiert. Überlappungen derselben Provider-ID zwischen benachbarten Tagesfeeds (z. B. Next-Day-Einträge) werden im Bereichs-Unique auf 1618 IDs reduziert.

## Tagesdetails

| Datum | Run | Gruppen | Roh | Unique | Index | Next-Day | Duplikate | Detail | Skip HZ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08-26 | COMPLETE | 53 | 208 | 208 | 208 | 25 | 0 | 56 | 152 |
| 2026-08-27 | COMPLETE | 34 | 100 | 100 | 100 | 13 | 0 | 64 | 36 |
| 2026-08-28 | COMPLETE | 111 | 232 | 232 | 232 | 32 | 0 | 105 | 127 |
| 2026-08-29 | COMPLETE | 157 | 697 | 697 | 697 | 67 | 0 | 253 | 444 |
| 2026-08-30 | COMPLETE | 149 | 518 | 518 | 518 | 34 | 0 | 223 | 295 |

## Detail- und Rate-Limit-Nachweis

Die folgenden Werte werden aus den terminalen SQLite-Detailstatus abgeleitet. Der Client hält HTTP-/Retry-Metriken nur im Prozess; wenn der CLI-Abschlussoutput nicht separat archiviert wurde, sind 200/Fehler hier eine Status-basierte Untergrenze. Für exakte Client-Zähler kann der Abschlussoutput als JSON mit --access-metrics übergeben werden.

| Metrik | Wert | Definition |
| --- | --- | --- |
| Client requests gesamt | n/a | FotMobClient-Zähler, inklusive Katalog/Tagesfeeds |
| Daily-Index/Katalog HTTP-200 (mind.) | 6 | 1 allLeagues-Katalog + fünf erfolgreiche Daily-Feeds |
| Detail-Run erfolgreiche Responses | 1616 | CLI-Detailresult; Queue-Skips sind keine HTTP-Requests |
| HTTP-200 mindestens gesamt | 1622 | Index/Katalog plus erfolgreiche Detail-Responses; ohne Retry-Zähler |
| HTTP-Fehler | 0 | Client-Zähler; ohne access.metrics terminale Scope-Untergrenze |
| HTTP-429 | n/a | FotMobClient-Zähler |
| Retries | n/a | FotMobClient-Zähler |
| Parse failures | n/a | FotMobClient-Zähler |
| Nicht terminal | 0 | NOT_FETCHED oder IN_PROGRESS nach Abschluss |

## FirstHalf-Abdeckung – Match-Core

Ein Match zählt als verfügbar, wenn Home- und Away-Wert des jeweiligen Paar-Metrics vorhanden sind. `None` bleibt fehlend und wird nicht als 0 interpretiert.

| Metric | verfügbar | eligible | Coverage | Label |
| --- | --- | --- | --- | --- |
| HT score | 610 | 610 | 100.0% | HIGH |
| HT xG | 377 | 610 | 61.8% | MEDIUM |
| HT shots | 610 | 610 | 100.0% | HIGH |
| HT shots on target | 610 | 610 | 100.0% | HIGH |
| HT big chances | 610 | 610 | 100.0% | HIGH |
| HT corners | 610 | 610 | 100.0% | HIGH |
| HT possession | 610 | 610 | 100.0% | HIGH |
| HT yellow cards | 610 | 610 | 100.0% | HIGH |
| HT red cards | 610 | 610 | 100.0% | HIGH |
| HT fouls | 610 | 610 | 100.0% | HIGH |
| HT offsides | 610 | 610 | 100.0% | HIGH |
| HT goalkeeper saves | 610 | 610 | 100.0% | HIGH |
| HT passes | 610 | 610 | 100.0% | HIGH |
| HT accurate passes | 610 | 610 | 100.0% | HIGH |
| HT shots inside box | 610 | 610 | 100.0% | HIGH |
| HT shots outside box | 610 | 610 | 100.0% | HIGH |
| HT touches in box | 571 | 610 | 93.6% | HIGH |
| HT expected threat | 0 | 610 | 0.0% | NONE |

### FirstHalf-Abdeckung je Liga (nur >=10 eligible Matches)

| Land | Liga | eligible | HT score | HT xG | HT shots |
| --- | --- | --- | --- | --- | --- |
| Argentinien | Liga Profesional | 11 | 100.0% | 100.0% | 100.0% |
| Brasilien | Serie B | 10 | 100.0% | 100.0% | 100.0% |
| England | Championship | 12 | 100.0% | 100.0% | 100.0% |
| England | League One | 11 | 100.0% | 100.0% | 100.0% |
| England | League Two | 12 | 100.0% | 100.0% | 100.0% |
| International | Conference League Qualifikation | 18 | 100.0% | 100.0% | 100.0% |
| International | Europa League Qualifikation | 11 | 100.0% | 100.0% | 100.0% |
| Italien | Serie B | 10 | 100.0% | 100.0% | 100.0% |
| Japan | J. League | 10 | 100.0% | 100.0% | 100.0% |
| Kolumbien | Primera A | 13 | 100.0% | 100.0% | 100.0% |
| Niederlande | Eerste Divisie | 10 | 100.0% | 0.0% | 100.0% |
| Saudi-Arabien | Saudi Pro League | 11 | 100.0% | 100.0% | 100.0% |
| Spanien | LaLiga | 11 | 100.0% | 100.0% | 100.0% |
| Türkei | 1. Lig | 10 | 100.0% | 0.0% | 100.0% |
| USA | MLS | 15 | 100.0% | 100.0% | 100.0% |
| USA | MLS Next Pro | 16 | 100.0% | 0.0% | 100.0% |
| USA | USL Championship | 14 | 100.0% | 0.0% | 100.0% |
| Ägypten | Premier League | 10 | 100.0% | 100.0% | 100.0% |

### `ht_extra_stats_json` – provider_metric_name

Die Extra-Metriken bleiben im Canonical-Schema als JSON erhalten; die Tabelle zeigt ihre beobachtete FirstHalf-Abdeckung.

| provider_metric_name | verfügbar | eligible | Coverage | Label | Beispiel |
| --- | --- | --- | --- | --- | --- |
| accurate crosses | 610 | 610 | 100.0% | HIGH | [1.0, 1.0] |
| accurate long balls | 610 | 610 | 100.0% | HIGH | [12.0, 14.0] |
| aerial duels won | 610 | 610 | 100.0% | HIGH | [2.0, 3.0] |
| big chances missed | 610 | 610 | 100.0% | HIGH | [0.0, 2.0] |
| blocked shots | 610 | 610 | 100.0% | HIGH | [0.0, 2.0] |
| blocks | 610 | 610 | 100.0% | HIGH | [2.0, 0.0] |
| clearances | 610 | 610 | 100.0% | HIGH | [8.0, 12.0] |
| duels won | 610 | 610 | 100.0% | HIGH | [15.0, 19.0] |
| ground duels won | 610 | 610 | 100.0% | HIGH | [13.0, 16.0] |
| hit woodwork | 610 | 610 | 100.0% | HIGH | [0.0, 0.0] |
| interceptions | 610 | 610 | 100.0% | HIGH | [4.0, 8.0] |
| shots off target | 610 | 610 | 100.0% | HIGH | [2.0, 0.0] |
| successful dribbles | 610 | 610 | 100.0% | HIGH | [4.0, 1.0] |
| tackles | 610 | 610 | 100.0% | HIGH | [4.0, 10.0] |
| throws | 610 | 610 | 100.0% | HIGH | [11.0, 7.0] |
| opposition half | 576 | 610 | 94.4% | HIGH | [61.0, 83.0] |
| own half | 576 | 610 | 94.4% | HIGH | [142.0, 109.0] |
| xg non penalty | 377 | 610 | 61.8% | MEDIUM | [0.52, 0.02] |
| xg on target xgot | 377 | 610 | 61.8% | MEDIUM | [0.11, 0.0] |
| xg open play | 377 | 610 | 61.8% | MEDIUM | [0.33, 0.02] |
| xg set play | 376 | 610 | 61.6% | MEDIUM | [0.18, 0.0] |
| distance covered | 9 | 610 | 1.5% | LOW | [56772.0, 55052.0] |
| number of sprints | 9 | 610 | 1.5% | LOW | [76.0, 61.0] |
| sprinting distance | 9 | 610 | 1.5% | LOW | [1839.0, 1529.0] |

## Shotmap-Feldabdeckung

Shot rows im Canary-Scope: **10706**

| Feld | vorhanden | gesamt | Coverage |
| --- | --- | --- | --- |
| minute | 10706 | 10706 | 100.0% |
| added_time | 0 | 10706 | 0.0% |
| xg | 10670 | 10706 | 99.7% |
| xgot | 10590 | 10706 | 98.9% |
| outcome | 10706 | 10706 | 100.0% |
| shot_type | 10706 | 10706 | 100.0% |
| situation | 10706 | 10706 | 100.0% |
| body_part | 0 | 10706 | 0.0% |
| x | 10706 | 10706 | 100.0% |
| y | 10706 | 10706 | 100.0% |
| team_id | 10706 | 10706 | 100.0% |
| is_home | 10706 | 10706 | 100.0% |
| player_id | 10706 | 10706 | 100.0% |
| player_name | 10706 | 10706 | 100.0% |
| period | 10706 | 10706 | 100.0% |

## Events

Event rows im Canary-Scope: **11881**

| Feld | vorhanden | gesamt | Coverage |
| --- | --- | --- | --- |
| event_type | 11881 | 11881 | 100.0% |
| period | 11881 | 11881 | 100.0% |
| minute | 11881 | 11881 | 100.0% |
| added_time | 0 | 11881 | 0.0% |
| team_id | 0 | 11881 | 0.0% |
| is_home | 10118 | 11881 | 85.2% |
| player_id | 4564 | 11881 | 38.4% |
| player_name | 4539 | 11881 | 38.2% |
| score_home_after | 11881 | 11881 | 100.0% |
| score_away_after | 11881 | 11881 | 100.0% |

### Event-Typen

| event_type | count |
| --- | --- |
| ADDEDTIME | 527 |
| CARD | 2486 |
| COMMENT | 71 |
| GOAL | 1919 |
| HALF | 1228 |
| MISSED_PENALTY | 41 |
| PENALTYSHOOTOUT | 8 |
| SUBSTITUTION | 5554 |
| VAR | 47 |

### Abgeleitete Event-Kategorien

| Kategorie | count |
| --- | --- |
| Goals | 1919 |
| Penalties | 49 |
| Substitutions | 5554 |
| VAR | 47 |

## Derived-feature readiness

| Bereich | Status | Begründung |
| --- | --- | --- |
| HT target / second-half outcome | READY | HT score plus FT score are present in canonical core where available. |
| HT feature matrix | READY | Nullable fixed metrics and provider extras are archived; coverage labels above apply. |
| Shot-derived features | READY | Canonical shot rows are available only for matches with provider shotmap data. |
| Event-derived features | READY | Canonical event rows are available only for matches with provider timeline data. |
| ML/backtest/strategy | NOT_IN_SCOPE | Explicitly excluded from V0.5.5.1. |

## Archive- und Qualitätschecks

| Check | Ergebnis |
| --- | --- |
| Daily runs vorhanden | PASS |
| Feed unique == Daily-Index je Tag | PASS |
| Keine fehlende Canonical-Datei für FETCHED/PARTIAL | PASS |
| SKIPPED_NO_HALFTIME => NO_HALFTIME | PASS |
| Alle Detailjobs terminal | PASS |
| Archive-Lesbarkeit | PASS |

## Empfehlungen

1. HT-Metriken ohne Providerwerte nicht imputieren; nullable halten: HT expected threat.
2. Shotmap-Felder ohne Werte als nullable behandeln: added_time, body_part.
3. Event-Felder ohne Werte nicht imputieren; Providerlücke dokumentieren: added_time, team_id.
4. Für den nächsten Lauf FotMobClient-access.metrics als kompakten JSON-Sidecar archivieren, damit 429-/Retry-/Parse-Zähler exakt statt nur statusbasiert vorliegen.

## Reproduzierbarkeit

```text
python scripts/fotmob_history.py dates --from-date 2026-08-26 --to-date 2026-08-30 --workers 10 --root .
python scripts/fotmob_history.py dates --from-date 2026-08-26 --to-date 2026-08-30 --workers 10 --index-only --root .
python scripts/report_v0551_canary.py --from-date 2026-08-26 --to-date 2026-08-30 --root . --execution-summary outputs/V0551_DETAIL_RUN_SUMMARY.json
```
