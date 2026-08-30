# V0.5.2 Status – FotMob Historical Bundesliga Foundation

Stand: 30.08.2026, Europe/Berlin

```text
FOTMOB_V052_STATUS = PASS
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
FOTMOB_NETWORK_MODE = manual
FOTMOB_EXTERNAL_DISCOVERY = PASS
FOTMOB_EXTERNAL_DETAILS = PASS (10/10)
```

## Abnahme

| Kriterium | Status | Beleg |
|---|---|---|
| League-/Season-Discovery ohne ID-Brute-Force | PASS (real) | öffentliche FotMob-HTML-Payload, Liga 54 |
| echte Season-ID persistieren | PASS (real) | `26891` = 2025/26, `23794` = 2024/25 |
| Fixture-/Results-Index mit Provider-ID | PASS (real) | `306 + 306` indexierte Partien |
| deterministische 5er-Sample pro Season | PASS (real) | `fotmob_history_samples`, `5 + 5` Finished-Matches |
| explizite HT-/FT-Normalisierung | PASS | `fotmob/history_models.py` + Parser |
| Queue, Retry, Worker, stale-Recovery, Resume | PASS | `fotmob/history_storage.py` + Resume-Lauf ohne neue Requests |
| zstd-Parquet plus Archive-Dedupe | PASS (real) | `2` Dateien, je `5` Zeilen, `ZSTD` |
| echte 2025/26-Discoveryantwort | PASS | League 54, `Bundesliga`, Country-Code `GER` |
| fünf echte Details 2025/26 | PASS | `5/5 FETCHED/COMPLETE`, `5` HTTP-200-Requests |
| fünf echte Details 2024/25 | PASS | `5/5 FETCHED/COMPLETE`, `5` HTTP-200-Requests |

## Interpretation

Die technische Abnahme wurde mit einem begrenzten, manuell gestarteten Lauf
unter `work/v052-real-validation-final` abgeschlossen. FotMob liefert die
aktuelle Katalog- und Detailpayload als Next.js-`__NEXT_DATA__`: Liga 54 über
`/leagues/54`, die historischen Seasons über
`/leagues/54?season=2025/2026` bzw. `2024/2025`, und Details über
`/match/{match_id}`. Die früheren `/api/leagues`- und
`/api/matchDetails`-Pfade antworten aktuell mit HTTP 404 und werden nicht mehr
verwendet.

Der manuelle CLI-Modus ist ausdrücklich für diese einmalige Discovery-/Sample-
Abnahme vorgesehen und benötigt keine Worker-Providerfreigabe. Der permanente
Worker bleibt trotzdem sicher deaktiviert: Standard ist `FOTMOB_NETWORK_MODE=off`;
der Modus `worker` verlangt weiterhin `PRODUCTION_READY` und
`ACCEPTABLE_FOR_PROJECT`. Die V0.5.1-Entscheidung bleibt daher korrekt bei
`LIMITED_USE`/`UNCLEAR`. Es wurde kein mehrstündiger Bulk-Scan gestartet.

## Fertiggestellt

- Unified CLI: `scripts/fotmob_history.py`
- Discovery-Wrapper: `scripts/discover_fotmob_league.py`
- Index-Wrapper: `scripts/index_fotmob_matches.py`
- Detailscan-Wrapper: `scripts/scan_fotmob_history.py`
- Offline-Fixture- und End-to-End-Tests in `tests/test_fotmob_history.py`
- reale Validierung: je Season `306` indexierte Matches, `5` Detailantworten,
  `5` Parquet-Zeilen und optional `5` zstd-Raw-Payloads
- SQLite-Katalog, resumierbare Queue und Parquet-Archiv

Die produktive, regelmäßige Worker-Automation bleibt von dieser manuellen
technischen Validierung getrennt und erfordert weiterhin eine neue,
ausdrücklich zulässige Providerfreigabe.
