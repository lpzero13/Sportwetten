# Project Capability Audit – V0.5.6.1

Generated at `2026-09-01T11:21:51.809617+00:00`.

## Result

`CAPABILITY_AUDIT = PASS`

The audit separates research throughput controls from provider-safety, storage and live-Tipico guards. The old historical 0.5 req/s value is no longer the normal code or deployment default; the compatibility name remains documented and does not construct the historical limiter.

## Runtime configuration observed

- FotMob enabled/history enabled: `True` / `True`
- Network mode: `manual`; background worker allowed: `False`
- Rate mode and range: `ADAPTIVE` / `5.0 -> 100.0 req/s in +5.0`
- Historical workers: `10` initial, `40` maximum
- Connection pool: `40`; retries/timeout: `3` / `10s`

## Restrictions and defaults

| name | location | current_default | effective_runtime_value | reason_if_documented | performance_impact | risk_if_removed | recommended_default | recommended_max | classification |
|---|---|---|---|---|---|---|---|---|---|
| FOTMOB_ENABLED | config.py; deploy/tipico-observer.env.example | true | True | Read-only FotMob research is available; no request occurs during app construction. | Enables explicit UI/CLI research paths. | FotMob index, detail and display paths become unavailable. | true | true | USEFUL_GUARDRAIL |
| FOTMOB_HISTORY_ENABLED | config.py; deploy/tipico-observer.env.example | true | True | Separate switch for historical network use. | When false, all historical network work is blocked. | Historical imports could be started unintentionally. | true for manual research | true | USEFUL_GUARDRAIL |
| FOTMOB_NETWORK_MODE | config.py; fotmob/history_pipeline.py; deploy/*.service | manual | manual | Manual protects against background provider traffic; worker has an additional policy gate. | Does not throttle a manually started job; only selects the permitted execution context. | A permanent worker could make unreviewed external requests. | manual | worker only after provider approval | REQUIRED_GUARDRAIL |
| Worker provider-policy gate | fotmob/history_pipeline.py; fotmob/service.py; deploy/install_proxmox.sh | PRODUCTION_READY + ACCEPTABLE_FOR_PROJECT required | blocked for background worker (LIMITED_USE/UNCLEAR) | No automated FotMob worker without explicit project/provider decision. | None for explicit manual date-range jobs. | Uncontrolled recurring provider access. | keep gate | worker only with both approvals | REQUIRED_GUARDRAIL |
| FOTMOB_RATE_MODE | config.py; fotmob/rate_control.py; fotmob/client.py | ADAPTIVE | ADAPTIVE | Ramp only after healthy windows and back off on provider/transport problems. | Removes the old fixed 0.5 req/s historical bottleneck. | Either unnecessary slow operation or blind overuse. | ADAPTIVE | FIXED only for measured probes/controlled runs | REQUIRED_GUARDRAIL |
| FOTMOB_INITIAL_RPS / FOTMOB_RPS_STEP | config.py; deploy/tipico-observer.env.example | 5 / 5 | 5.0 / 5.0 | Measured V0.5.6 probe starts at 5 and advances in +5 steps. | Controls startup and ramp-up speed. | Magic-number rate changes or accidental request burst. | 5 / 5 | provider-tested values only | USEFUL_GUARDRAIL |
| FOTMOB_MAX_RPS | config.py; fotmob/rate_control.py | 100.0 | 100.0 | Explicit upper boundary for the controlled probe and normal adaptive client. | Caps maximum request-start rate. | Adaptive logic could continue past the tested range. | 100 after two independent V0.5.6.1 canaries | highest repeatedly stable measured value; re-probe before raising | REQUIRED_GUARDRAIL |
| FOTMOB_INITIAL_WORKERS / FOTMOB_MAX_WORKERS | config.py; fotmob/history_pipeline.py | 10 / 40 | 10 / 40 | Worker count is configurable and separately benchmarked from RPS. | Controls parallel detail fetches; does not bypass the shared rate controller. | Unbounded threads, connection pressure and SQLite contention. | 10 / 40 | 40 pending measured worker benchmark | USEFUL_GUARDRAIL |
| Adaptive window / cooldown / health thresholds | config.py; fotmob/rate_control.py | 20 requests / 5 seconds / error 10%, 5xx-timeout-connection 5%, p95 3000ms | 20 / 5.0s | Avoids reacting to one noisy response while still backing off on a clear provider or transport problem. | Determines how quickly the controller ramps or backs off. | Blind rate changes or oscillation under transient failures. | 20 / 5s with measured thresholds | project-tested threshold values | REQUIRED_GUARDRAIL |
| FOTMOB_PERFORMANCE_WORKER_LEVELS | config.py; fotmob/performance.py; deploy/tipico-observer.env.example | 10,20,30,40 | 10,20,30,40 | Separates worker scaling from RPS and stops at the configured max worker boundary. | Controls the worker benchmark duration and parallelism. | Worker conclusions become untested or unbounded. | 10,20,30,40 | FOTMOB_MAX_WORKERS | USEFUL_GUARDRAIL |
| Old FOTMOB_HISTORY_REQUESTS_PER_SECOND alias | config.py; deploy/tipico-observer.env.example | 5 (compatibility alias; old value was 0.5) | 5.0 | Kept so older integrations parse, while the historical client uses adaptive settings. | No longer constructs the historical 0.5 req/s limiter. | Older deployments may fail to load settings. | 5 or remove after migration | not a control path | LEGACY_CONSERVATIVE_DEFAULT |
| FOTMOB_MIN_REQUEST_INTERVAL_SECONDS | config.py; fotmob/client.py; fotmob/service.py | 1 second | 1.0 | Legacy FIXED-mode/live-client compatibility; historical ADAPTIVE client passes no fixed interval. | Only applies when rate mode is FIXED or an explicit zero interval is requested. | Legacy fixed-mode integrations lose their explicit pacing option. | retain for FIXED compatibility | not used by adaptive historical path | LEGACY_CONSERVATIVE_DEFAULT |
| Retries / timeout | config.py; fotmob/client.py; fotmob/history_pipeline.py | 3 retries, 10 seconds | 3 / 10 | Bounded recovery for transient failures; attempts and errors remain visible. | Retries can extend a failing stage but prevent one transient response from losing a match. | Transient provider/network errors become permanent data gaps. | 3 / 10 seconds | measured provider/network tolerance | USEFUL_GUARDRAIL |
| Connection pool / compression | fotmob/client.py | pool 40; gzip/deflate accepted | 40 | Reuse one Session with HTTPAdapter, keep-alive and bounded connection reuse. | Avoids a new connection for every match and reduces transfer volume. | More handshakes, latency and connection pressure. | 40 | no larger than worker cap without measurement | USEFUL_GUARDRAIL |
| FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL | config.py; fotmob/performance.py | 25 | 25 | Finite probe; do not start a large backfill just to measure throughput. | Determines confidence and duration of each level. | Too few samples produce noisy decisions or too many create unnecessary traffic. | 25 | project-approved finite sample | USEFUL_GUARDRAIL |
| COLLECTOR_DETAIL_WORKERS | config.py; deploy/tipico-collector.service; scripts/run_collector.py | 3, max 5 | 3 | Separate Tipico live collector guardrail; not the FotMob historical worker pool. | Caps live Tipico detail concurrency. | Live odds collector could create avoidable provider/SQLite pressure. | 3 | 5 until a separate Tipico benchmark | REQUIRED_GUARDRAIL |
| STORE_FOTMOB_HISTORICAL_RAW | config.py; deploy/tipico-observer.env.example | false | False | Canonical Parquet stores normalized detail; raw payload is optional due volume. | Avoids extra compressed raw writes. | Less forensic replay data, but no loss of canonical metrics. | false | true only for bounded debugging runs | USEFUL_GUARDRAIL |
| FOTMOB_HISTORY_BATCH_SIZE | config.py; fotmob/history_pipeline.py; fotmob/history_storage.py | 100 | 100 | Groups Parquet/SQLite writes while keeping the detail queue resumable. | Larger batches reduce write overhead but increase flush memory and recovery scope. | Per-row writes can make storage the bottleneck. | 100 | measure with archive size and memory | USEFUL_GUARDRAIL |
| UI refresh/raw storage flags | config.py (PERSIST_UI_REFRESH, RAW_EVERY_POLL, RAW_AT_HALFTIME) | false / false / false | false / false / false | Current-state refreshes and optional raw retention are intentionally separated from history. | Prevents UI reruns from creating unbounded historical writes. | Storage growth and duplicate refresh rows. | false | true only with explicit retention policy | USEFUL_GUARDRAIL |
| wetten-fotmob.service | deploy/wetten-fotmob.service; deploy/install_proxmox.sh; deploy/activate_fotmob.sh | installed but disabled | disabled; UI/CLI manual path enabled | Background provider access remains behind explicit policy approval. | No impact on manual date-range runs. | A restart could turn a research feature into a recurring network worker. | disabled | enable only after worker gates are approved | REQUIRED_GUARDRAIL |

## Legacy collector comparison

The locally available implementation at `C:/Programmieren/Fussball/Daten Sammler/AntiGrav/backend/app/services/collector.py` was reviewed. It explains why the old project could appear faster, but it did not provide a safe drop-in replacement.

| Capability | OLD_IMPLEMENTATION | CURRENT_IMPLEMENTATION | Decision |
|---|---|---|---|
| Scan/detail parallelism | `SCAN_WORKERS=50`, `DL_WORKERS=30` | Historical detail pool is configurable (`10` initial, `40` max); separate worker benchmark | Keep bounded configurable pool; do not copy 50/30 blindly |
| HTTP client | One global `cloudscraper` session with Chrome/Windows profile | One shared `requests.Session` with `HTTPAdapter`, keep-alive, pool size, compression | Use normal public client; no fingerprint evasion |
| Request pacing | Random sleeps around 0.1–0.6s; no central global limiter | Shared adaptive controller, fixed probe mode, +5 ramp, backoff/cooldown | Current model is measurable and provider-friendly |
| Retry/error handling | Mostly empty/`None` on errors; no durable retry state | Bounded retries, typed transport counters, 429/403/5xx/parse metrics | Preserve current error visibility |
| Fetch strategy | Direct old `/api/leagues` and `/api/matchDetails` calls | Daily all-league index plus public detail path, resumable queue | Do not restore obsolete endpoint assumptions |
| Storage | Per-row SQLite writes and old schema | SQLite index/queue plus canonical Parquet batches and performance profiles | Keep batch/archive path; V0.5.6.1 isolates storage from the detail max-throughput probe |
| Missing halftime | No equivalent V0.5.6 quality rule | `SKIPPED_NO_HALFTIME` is explicit and not instability | Preserve data-quality separation |

No safe same-league/season apples-to-apples legacy benchmark was executed: the old collector writes its own database, uses different endpoints and has no equivalent counters. The V0.5.6 real probe is therefore the authoritative measurement for the current client.

## Pipeline bottleneck review

The current path is `HTTP Session -> normalized parser -> SQLite queue/index -> bounded Parquet batch`. V0.5.6.1 additionally isolates detail HTTP work and records controller target, rate-slot starts, actual HTTP starts, detail/parser timing, CPU/RSS and pool size. It does not claim SQLite or Parquet is the bottleneck without a measured storage comparison. Parquet writes remain batch-based and protected by the archive lock; a bounded fetch/parser/writer queue can be introduced only if a future storage probe shows disk or SQLite wait time materially reducing effective throughput.

## Search inventory

The source/config/deployment scan used the requested restriction vocabulary. Counts include documentation and intentional explanatory references; `data`, `outputs`, virtual environments and generated archives are excluded. V0.5.6.1 additionally records rate-slot scheduling, parser timing, process CPU/RSS and connection-pool decisions for the finite max-throughput probe.

| Token | Hits |
|---|---:|
| `ENABLED=false` | 15 |
| `DISABLED` | 18 |
| `OFF_BY_DESIGN` | 1 |
| `NETWORK_MODE` | 51 |
| `RATE_LIMIT` | 22 |
| `REQUESTS_PER_SECOND` | 16 |
| `WORKERS` | 178 |
| `CONCURRENCY` | 2 |
| `SEMAPHORE` | 1 |
| `SLEEP` | 12 |
| `DELAY` | 62 |
| `BACKOFF` | 15 |
| `TIMEOUT` | 91 |
| `BATCH_SIZE` | 43 |
| `MAX_` | 407 |
| `MIN_` | 108 |
| `FEATURE_FLAG` | 1 |
| `DRY_RUN` | 1 |
| `MANUAL` | 120 |
| `SAFE` | 50 |
| `CONSERVATIVE` | 9 |

### Representative scan hits

| Token | Location | Excerpt |
|---|---|---|
| `MANUAL` | `app.py:140` | manual_refresh = control_columns[1].button( |
| `MANUAL` | `app.py:142` | key=f"manual-detail-refresh-{selected_id}", |
| `MANUAL` | `app.py:168` | or manual_refresh |
| `MANUAL` | `app.py:200` | if manual_refresh and result.success and result.metrics is not None and result.details is not None: |
| `TIMEOUT` | `config.py:87` | REQUEST_TIMEOUT_SECONDS = 10 |
| `WORKERS` | `config.py:90` | COLLECTOR_DETAIL_WORKERS = 3 |
| `DELAY` | `config.py:92` | COLLECTOR_HALFTIME_DELAYS_SECONDS = (0, 20, 60) |
| `DELAY` | `config.py:94` | COLLECTOR_RETRY_DELAYS_SECONDS = (1, 3, 10) |
| `MAX_` | `config.py:95` | MAX_LIVE_ODDS_AGE_SECONDS = 10 |
| `DELAY` | `config.py:97` | SNAPSHOT_HT_STABLE_DELAY_SECONDS = 45 |
| `BATCH_SIZE` | `config.py:99` | SNAPSHOT_OUTBOX_BATCH_SIZE = 100 |
| `MANUAL` | `config.py:112` | # network mode is manual, so constructing the app or running the Tipico |
| `TIMEOUT` | `config.py:119` | FOTMOB_TIMEOUT_SECONDS = 10 |
| `MAX_` | `config.py:120` | FOTMOB_MAX_RETRIES = 3 |
| `MIN_` | `config.py:121` | FOTMOB_MIN_REQUEST_INTERVAL_SECONDS = 1.0 |
| `DELAY` | `config.py:123` | FOTMOB_HT_STABLE_DELAY_SECONDS = 45 |
| `BATCH_SIZE` | `config.py:125` | FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE = 100 |
| `CONSERVATIVE` | `config.py:149` | FOTMOB_RATE_MODE_VALUES = ("ADAPTIVE", "FIXED", "CONSERVATIVE") |
| `MIN_` | `config.py:152` | FOTMOB_MIN_RPS = 0.5 |
| `TIMEOUT` | `config.py:154` | # 100% success and no 429/403/5xx/timeout/parse failures. Windows scheduling |
| `MAX_` | `config.py:157` | FOTMOB_MAX_RPS = 100.0 |
| `WORKERS` | `config.py:158` | FOTMOB_INITIAL_WORKERS = 10 |
| `WORKERS` | `config.py:159` | FOTMOB_MAX_WORKERS = 40 |
| `MAX_` | `config.py:159` | FOTMOB_MAX_WORKERS = 40 |
| `MAX_` | `config.py:162` | FOTMOB_MAX_ERROR_RATE = 0.10 |
| `MAX_` | `config.py:163` | FOTMOB_MAX_5XX_RATE = 0.05 |
| `TIMEOUT` | `config.py:164` | FOTMOB_MAX_TIMEOUT_RATE = 0.05 |
| `MAX_` | `config.py:164` | FOTMOB_MAX_TIMEOUT_RATE = 0.05 |
| `MAX_` | `config.py:165` | FOTMOB_MAX_CONNECTION_ERROR_RATE = 0.05 |
| `MAX_` | `config.py:166` | FOTMOB_MAX_P95_LATENCY_MS = 3000.0 |
| `WORKERS` | `config.py:172` | FOTMOB_HISTORY_WORKERS = FOTMOB_INITIAL_WORKERS |
| `REQUESTS_PER_SECOND` | `config.py:173` | FOTMOB_HISTORY_REQUESTS_PER_SECOND = FOTMOB_INITIAL_RPS |
| `TIMEOUT` | `config.py:174` | FOTMOB_HISTORY_TIMEOUT_SECONDS = 10 |
| `MAX_` | `config.py:175` | FOTMOB_HISTORY_MAX_RETRIES = 3 |
| `MAX_` | `config.py:177` | FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS = 3 |
| `BATCH_SIZE` | `config.py:178` | FOTMOB_HISTORY_BATCH_SIZE = 100 |
| `NETWORK_MODE` | `config.py:180` | FOTMOB_NETWORK_MODE = "manual" |
| `MANUAL` | `config.py:180` | FOTMOB_NETWORK_MODE = "manual" |
| `NETWORK_MODE` | `config.py:181` | FOTMOB_NETWORK_MODE_VALUES = ("off", "manual", "worker") |
| `MANUAL` | `config.py:181` | FOTMOB_NETWORK_MODE_VALUES = ("off", "manual", "worker") |
| `TIMEOUT` | `config.py:208` | request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS |
| `WORKERS` | `config.py:213` | collector_detail_workers: int = COLLECTOR_DETAIL_WORKERS |
| `DELAY` | `config.py:215` | collector_halftime_delays_seconds: tuple[int, ...] = COLLECTOR_HALFTIME_DELAYS_SECONDS |
| `DELAY` | `config.py:217` | collector_retry_delays_seconds: tuple[int, ...] = COLLECTOR_RETRY_DELAYS_SECONDS |
| `MAX_` | `config.py:218` | max_live_odds_age_seconds: int = MAX_LIVE_ODDS_AGE_SECONDS |
| `DELAY` | `config.py:220` | snapshot_ht_stable_delay_seconds: int = SNAPSHOT_HT_STABLE_DELAY_SECONDS |
| `BATCH_SIZE` | `config.py:222` | snapshot_outbox_batch_size: int = SNAPSHOT_OUTBOX_BATCH_SIZE |
| `TIMEOUT` | `config.py:238` | fotmob_timeout_seconds: int = FOTMOB_TIMEOUT_SECONDS |
| `MAX_` | `config.py:239` | fotmob_max_retries: int = FOTMOB_MAX_RETRIES |
| `MIN_` | `config.py:240` | fotmob_min_request_interval_seconds: float = FOTMOB_MIN_REQUEST_INTERVAL_SECONDS |
| `DELAY` | `config.py:242` | fotmob_ht_stable_delay_seconds: int = FOTMOB_HT_STABLE_DELAY_SECONDS |
| `BATCH_SIZE` | `config.py:244` | fotmob_snapshot_outbox_batch_size: int = FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE |
| `MIN_` | `config.py:258` | fotmob_min_rps: float = FOTMOB_MIN_RPS |
| `MAX_` | `config.py:259` | fotmob_max_rps: float = FOTMOB_MAX_RPS |
| `WORKERS` | `config.py:260` | fotmob_initial_workers: int = FOTMOB_INITIAL_WORKERS |
| `WORKERS` | `config.py:261` | fotmob_max_workers: int = FOTMOB_MAX_WORKERS |
| `MAX_` | `config.py:261` | fotmob_max_workers: int = FOTMOB_MAX_WORKERS |
| `MAX_` | `config.py:264` | fotmob_max_error_rate: float = FOTMOB_MAX_ERROR_RATE |
| `MAX_` | `config.py:265` | fotmob_max_5xx_rate: float = FOTMOB_MAX_5XX_RATE |
| `TIMEOUT` | `config.py:266` | fotmob_max_timeout_rate: float = FOTMOB_MAX_TIMEOUT_RATE |
| `MAX_` | `config.py:266` | fotmob_max_timeout_rate: float = FOTMOB_MAX_TIMEOUT_RATE |
| `MAX_` | `config.py:267` | fotmob_max_connection_error_rate: float = FOTMOB_MAX_CONNECTION_ERROR_RATE |
| `MAX_` | `config.py:268` | fotmob_max_p95_latency_ms: float = FOTMOB_MAX_P95_LATENCY_MS |
| `WORKERS` | `config.py:274` | fotmob_history_workers: int = FOTMOB_HISTORY_WORKERS |
| `REQUESTS_PER_SECOND` | `config.py:275` | fotmob_history_requests_per_second: float = FOTMOB_HISTORY_REQUESTS_PER_SECOND |
| `TIMEOUT` | `config.py:276` | fotmob_history_timeout_seconds: int = FOTMOB_HISTORY_TIMEOUT_SECONDS |
| `MAX_` | `config.py:277` | fotmob_history_max_retries: int = FOTMOB_HISTORY_MAX_RETRIES |
| `MAX_` | `config.py:279` | fotmob_history_max_retry_attempts: int = FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS |
| `BATCH_SIZE` | `config.py:280` | fotmob_history_batch_size: int = FOTMOB_HISTORY_BATCH_SIZE |
| `NETWORK_MODE` | `config.py:282` | fotmob_network_mode: str = FOTMOB_NETWORK_MODE |
| `MIN_` | `config.py:328` | min_rps = max(0.0, _env_float("FOTMOB_MIN_RPS", FOTMOB_MIN_RPS)) |
| `MAX_` | `config.py:329` | max_rps = max( |
| `MIN_` | `config.py:330` | min_rps, |
| `MAX_` | `config.py:332` | _env_float("FOTMOB_MAX_RPS", FOTMOB_MAX_RPS), |
| `WORKERS` | `config.py:334` | initial_workers = max(1, _env_int("FOTMOB_INITIAL_WORKERS", FOTMOB_INITIAL_WORKERS)) |
| `WORKERS` | `config.py:335` | max_workers = max( |
| `MAX_` | `config.py:335` | max_workers = max( |
| `WORKERS` | `config.py:336` | initial_workers, |
| `WORKERS` | `config.py:337` | _env_int("FOTMOB_MAX_WORKERS", FOTMOB_MAX_WORKERS), |
| `MAX_` | `config.py:337` | _env_int("FOTMOB_MAX_WORKERS", FOTMOB_MAX_WORKERS), |
| `TIMEOUT` | `config.py:369` | request_timeout_seconds=_env_int( |
| `TIMEOUT` | `config.py:370` | "REQUEST_TIMEOUT_SECONDS", REQUEST_TIMEOUT_SECONDS |
| `WORKERS` | `config.py:380` | collector_detail_workers=min( |
| `WORKERS` | `config.py:382` | _env_int("COLLECTOR_DETAIL_WORKERS", COLLECTOR_DETAIL_WORKERS), |
| `DELAY` | `config.py:387` | collector_halftime_delays_seconds=_env_int_tuple( |
| `DELAY` | `config.py:388` | "COLLECTOR_HALFTIME_DELAYS_SECONDS", COLLECTOR_HALFTIME_DELAYS_SECONDS |
| `DELAY` | `config.py:393` | collector_retry_delays_seconds=_env_int_tuple( |
| `DELAY` | `config.py:394` | "COLLECTOR_RETRY_DELAYS_SECONDS", COLLECTOR_RETRY_DELAYS_SECONDS |
| `MAX_` | `config.py:396` | max_live_odds_age_seconds=_env_int( |
| `MAX_` | `config.py:397` | "MAX_LIVE_ODDS_AGE_SECONDS", MAX_LIVE_ODDS_AGE_SECONDS |
| `DELAY` | `config.py:402` | snapshot_ht_stable_delay_seconds=_env_int( |
| `DELAY` | `config.py:403` | "SNAPSHOT_HT_STABLE_DELAY_SECONDS", SNAPSHOT_HT_STABLE_DELAY_SECONDS |
| `BATCH_SIZE` | `config.py:409` | snapshot_outbox_batch_size=_env_int( |
| `BATCH_SIZE` | `config.py:410` | "SNAPSHOT_OUTBOX_BATCH_SIZE", SNAPSHOT_OUTBOX_BATCH_SIZE |
| `TIMEOUT` | `config.py:434` | fotmob_timeout_seconds=_env_int( |
| `TIMEOUT` | `config.py:435` | "FOTMOB_TIMEOUT_SECONDS", FOTMOB_TIMEOUT_SECONDS |
| `MAX_` | `config.py:437` | fotmob_max_retries=_env_nonnegative_int("FOTMOB_MAX_RETRIES", FOTMOB_MAX_RETRIES), |
| `MIN_` | `config.py:438` | fotmob_min_request_interval_seconds=max( |
| `MIN_` | `config.py:441` | "FOTMOB_MIN_REQUEST_INTERVAL_SECONDS", |
| `MIN_` | `config.py:442` | FOTMOB_MIN_REQUEST_INTERVAL_SECONDS, |
| `DELAY` | `config.py:449` | fotmob_ht_stable_delay_seconds=_env_int( |
| `DELAY` | `config.py:450` | "FOTMOB_HT_STABLE_DELAY_SECONDS", FOTMOB_HT_STABLE_DELAY_SECONDS |
| `BATCH_SIZE` | `config.py:456` | fotmob_snapshot_outbox_batch_size=_env_int( |
| `BATCH_SIZE` | `config.py:457` | "FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE", |
| `BATCH_SIZE` | `config.py:458` | FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE, |
| `MIN_` | `config.py:496` | fotmob_min_rps=min_rps, |
| `MAX_` | `config.py:497` | fotmob_max_rps=max_rps, |
| `WORKERS` | `config.py:498` | fotmob_initial_workers=initial_workers, |
| `WORKERS` | `config.py:499` | fotmob_max_workers=max_workers, |
| `MAX_` | `config.py:499` | fotmob_max_workers=max_workers, |
| `MAX_` | `config.py:507` | fotmob_max_error_rate=max( |
| `MAX_` | `config.py:508` | 0.0, _env_float("FOTMOB_MAX_ERROR_RATE", FOTMOB_MAX_ERROR_RATE) |
| `MAX_` | `config.py:510` | fotmob_max_5xx_rate=max( |
| `MAX_` | `config.py:511` | 0.0, _env_float("FOTMOB_MAX_5XX_RATE", FOTMOB_MAX_5XX_RATE) |
| `TIMEOUT` | `config.py:513` | fotmob_max_timeout_rate=max( |
| `MAX_` | `config.py:513` | fotmob_max_timeout_rate=max( |
| `TIMEOUT` | `config.py:514` | 0.0, _env_float("FOTMOB_MAX_TIMEOUT_RATE", FOTMOB_MAX_TIMEOUT_RATE) |
| `MAX_` | `config.py:514` | 0.0, _env_float("FOTMOB_MAX_TIMEOUT_RATE", FOTMOB_MAX_TIMEOUT_RATE) |
| `MAX_` | `config.py:516` | fotmob_max_connection_error_rate=max( |
| `MAX_` | `config.py:519` | "FOTMOB_MAX_CONNECTION_ERROR_RATE", FOTMOB_MAX_CONNECTION_ERROR_RATE |
| `MAX_` | `config.py:522` | fotmob_max_p95_latency_ms=max( |
| `MAX_` | `config.py:524` | _env_float("FOTMOB_MAX_P95_LATENCY_MS", FOTMOB_MAX_P95_LATENCY_MS), |
| `WORKERS` | `config.py:538` | fotmob_history_workers=min( |
| `WORKERS` | `config.py:539` | max_workers, |
| `MAX_` | `config.py:539` | max_workers, |
| `WORKERS` | `config.py:540` | _env_int("FOTMOB_HISTORY_WORKERS", initial_workers), |
| `REQUESTS_PER_SECOND` | `config.py:542` | fotmob_history_requests_per_second=initial_rps, |
| `TIMEOUT` | `config.py:543` | fotmob_history_timeout_seconds=_env_int( |
| `TIMEOUT` | `config.py:544` | "FOTMOB_HISTORY_TIMEOUT_SECONDS", FOTMOB_HISTORY_TIMEOUT_SECONDS |
| `MAX_` | `config.py:546` | fotmob_history_max_retries=_env_nonnegative_int( |
| `MAX_` | `config.py:547` | "FOTMOB_HISTORY_MAX_RETRIES", FOTMOB_HISTORY_MAX_RETRIES |
| `MAX_` | `config.py:552` | fotmob_history_max_retry_attempts=_env_int( |
| `MAX_` | `config.py:553` | "FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS", FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS |
| `BATCH_SIZE` | `config.py:555` | fotmob_history_batch_size=_env_int( |
| `BATCH_SIZE` | `config.py:556` | "FOTMOB_HISTORY_BATCH_SIZE", FOTMOB_HISTORY_BATCH_SIZE |
| `NETWORK_MODE` | `config.py:561` | fotmob_network_mode=_env_choice( |
| `NETWORK_MODE` | `config.py:562` | "FOTMOB_NETWORK_MODE", |
| `NETWORK_MODE` | `config.py:563` | FOTMOB_NETWORK_MODE, |
| `NETWORK_MODE` | `config.py:564` | FOTMOB_NETWORK_MODE_VALUES, |
| `MANUAL` | `deploy/activate_fotmob.sh:41` | # The date-range button is an explicit manual action. The worker remains |
| `DISABLED` | `deploy/activate_fotmob.sh:42` | # disabled below, even though the shared FotMob feature is enabled. |
| `NETWORK_MODE` | `deploy/activate_fotmob.sh:45` | set_env_value FOTMOB_NETWORK_MODE manual |
| `MANUAL` | `deploy/activate_fotmob.sh:45` | set_env_value FOTMOB_NETWORK_MODE manual |
| `MIN_` | `deploy/activate_fotmob.sh:59` | set_env_value FOTMOB_MIN_RPS 0.5 |
| `MAX_` | `deploy/activate_fotmob.sh:60` | set_env_value FOTMOB_MAX_RPS 100 |
| `WORKERS` | `deploy/activate_fotmob.sh:61` | set_env_value FOTMOB_INITIAL_WORKERS 10 |
| `WORKERS` | `deploy/activate_fotmob.sh:62` | set_env_value FOTMOB_MAX_WORKERS 40 |
| `MAX_` | `deploy/activate_fotmob.sh:62` | set_env_value FOTMOB_MAX_WORKERS 40 |
| `WORKERS` | `deploy/activate_fotmob.sh:69` | set_env_value FOTMOB_HISTORY_WORKERS 10 |
| `REQUESTS_PER_SECOND` | `deploy/activate_fotmob.sh:70` | set_env_value FOTMOB_HISTORY_REQUESTS_PER_SECOND 5 |
| `MANUAL` | `deploy/install_proxmox.sh:108` | # Reconcile the V0.5.4 manual FotMob flags even when an older env file already |
| `DISABLED` | `deploy/install_proxmox.sh:117` | # legacy/polling FotMob service disabled; no permanent FotMob worker is needed. |
| `WORKERS` | `deploy/tipico-collector.service:14` | ExecStart=__INSTALL_DIR__/.venv/bin/python __INSTALL_DIR__/scripts/run_collector.py --root __INSTALL_DIR__ --workers 3 |
| `TIMEOUT` | `deploy/tipico-collector.service:17` | TimeoutStopSec=30 |
| `WORKERS` | `deploy/tipico-observer.env.example:15` | COLLECTOR_DETAIL_WORKERS=3 |
| `DELAY` | `deploy/tipico-observer.env.example:16` | SNAPSHOT_HT_STABLE_DELAY_SECONDS=45 |
| `BATCH_SIZE` | `deploy/tipico-observer.env.example:18` | SNAPSHOT_OUTBOX_BATCH_SIZE=100 |
| `MAX_` | `deploy/tipico-observer.env.example:29` | MAX_LIVE_ODDS_AGE_SECONDS=10 |
| `MANUAL` | `deploy/tipico-observer.env.example:32` | # FotMob V0.5.4: the UI date-range button is an explicit manual action. These |
| `TIMEOUT` | `deploy/tipico-observer.env.example:39` | FOTMOB_TIMEOUT_SECONDS=10 |
| `MAX_` | `deploy/tipico-observer.env.example:40` | FOTMOB_MAX_RETRIES=3 |
| `MIN_` | `deploy/tipico-observer.env.example:41` | # FOTMOB_MIN_REQUEST_INTERVAL_SECONDS is only the legacy FIXED-mode interval. |
| `MANUAL` | `deploy/tipico-observer.env.example:42` | # Historical manual loads use the adaptive profile below. |
| `MIN_` | `deploy/tipico-observer.env.example:43` | FOTMOB_MIN_REQUEST_INTERVAL_SECONDS=1 |
| `DELAY` | `deploy/tipico-observer.env.example:45` | FOTMOB_HT_STABLE_DELAY_SECONDS=45 |
| `BATCH_SIZE` | `deploy/tipico-observer.env.example:47` | FOTMOB_SNAPSHOT_OUTBOX_BATCH_SIZE=100 |
| `MIN_` | `deploy/tipico-observer.env.example:61` | FOTMOB_MIN_RPS=0.5 |
| `MAX_` | `deploy/tipico-observer.env.example:62` | FOTMOB_MAX_RPS=100 |
| `WORKERS` | `deploy/tipico-observer.env.example:63` | FOTMOB_INITIAL_WORKERS=10 |
| `WORKERS` | `deploy/tipico-observer.env.example:64` | FOTMOB_MAX_WORKERS=40 |
| `MAX_` | `deploy/tipico-observer.env.example:64` | FOTMOB_MAX_WORKERS=40 |
| `MAX_` | `deploy/tipico-observer.env.example:67` | FOTMOB_MAX_ERROR_RATE=0.10 |
| `MAX_` | `deploy/tipico-observer.env.example:68` | FOTMOB_MAX_5XX_RATE=0.05 |
| `TIMEOUT` | `deploy/tipico-observer.env.example:69` | FOTMOB_MAX_TIMEOUT_RATE=0.05 |
| `MAX_` | `deploy/tipico-observer.env.example:69` | FOTMOB_MAX_TIMEOUT_RATE=0.05 |
| `MAX_` | `deploy/tipico-observer.env.example:70` | FOTMOB_MAX_CONNECTION_ERROR_RATE=0.05 |
| `MAX_` | `deploy/tipico-observer.env.example:71` | FOTMOB_MAX_P95_LATENCY_MS=3000 |
| `WORKERS` | `deploy/tipico-observer.env.example:77` | FOTMOB_HISTORY_WORKERS=10 |
| `REQUESTS_PER_SECOND` | `deploy/tipico-observer.env.example:78` | FOTMOB_HISTORY_REQUESTS_PER_SECOND=5 |
| `TIMEOUT` | `deploy/tipico-observer.env.example:79` | FOTMOB_HISTORY_TIMEOUT_SECONDS=10 |
| `MAX_` | `deploy/tipico-observer.env.example:80` | FOTMOB_HISTORY_MAX_RETRIES=3 |
| `MAX_` | `deploy/tipico-observer.env.example:82` | FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS=3 |
| `BATCH_SIZE` | `deploy/tipico-observer.env.example:83` | FOTMOB_HISTORY_BATCH_SIZE=100 |
| `NETWORK_MODE` | `deploy/tipico-observer.env.example:85` | FOTMOB_NETWORK_MODE=manual |
| `MANUAL` | `deploy/tipico-observer.env.example:85` | FOTMOB_NETWORK_MODE=manual |
| `TIMEOUT` | `deploy/tipico-observer.service:17` | TimeoutStopSec=20 |
| `WORKERS` | `deploy/wetten-collector.service:14` | ExecStart=__INSTALL_DIR__/.venv/bin/python __INSTALL_DIR__/scripts/run_collector.py --root __INSTALL_DIR__ --workers 3 |
| `TIMEOUT` | `deploy/wetten-collector.service:17` | TimeoutStopSec=30 |
| `TIMEOUT` | `deploy/wetten-fotmob.service:17` | TimeoutStopSec=30 |
| `TIMEOUT` | `deploy/wetten-paper.service:17` | TimeoutStopSec=30 |
| `TIMEOUT` | `deploy/wetten-ui.service:17` | TimeoutStopSec=20 |
| `SAFE` | `fotmob/canonical.py:125` | def _safe(value: Any, default: str = "unknown") -> str: |
| `SAFE` | `fotmob/canonical.py:675` | """Crash-safe deterministic writer for the V0.5.4 FotMob archive.""" |
| `SAFE` | `fotmob/canonical.py:740` | destination = directory / f"{_safe(stem)}.parquet" |
| `SAFE` | `fotmob/canonical.py:787` | season = _safe(index.season_label or index.season_id, "unknown-season") |
| `SAFE` | `fotmob/canonical.py:788` | league = _safe(index.league_id) |
| `SAFE` | `fotmob/canonical.py:798` | old = self.root / "period_stats" / f"league_id={league}" / f"season={season}" / f"{_safe(match_stem)}.parquet" |
| `SAFE` | `fotmob/canonical.py:803` | old = self.root / "shots" / f"league_id={league}" / f"season={season}" / f"{_safe(match_stem)}.parquet" |
| `SAFE` | `fotmob/canonical.py:808` | old = self.root / "events" / f"league_id={league}" / f"season={season}" / f"{_safe(match_stem)}.parquet" |
| `SAFE` | `fotmob/canonical.py:825` | league = _safe(row.get("league_id")) |
| `SAFE` | `fotmob/canonical.py:826` | season = _safe(row.get("season_label") or row.get("season_id"), "unknown-season") |
| `DISABLED` | `fotmob/client.py:5` | disabled by the service unless the deployment explicitly opts in. |
| `RATE_LIMIT` | `fotmob/client.py:51` | rate_limit_responses: int = 0 |
| `TIMEOUT` | `fotmob/client.py:54` | timeout_errors: int = 0 |
| `MAX_` | `fotmob/client.py:60` | max_rate_wait_ms: float = 0.0 |
| `RATE_LIMIT` | `fotmob/client.py:177` | "rate_limit_responses": self.rate_limit_responses, |
| `RATE_LIMIT` | `fotmob/client.py:178` | "429": self.rate_limit_responses, |
| `TIMEOUT` | `fotmob/client.py:183` | "timeout_errors": self.timeout_errors, |
| `TIMEOUT` | `fotmob/client.py:184` | "timeouts": self.timeout_errors, |
| `REQUESTS_PER_SECOND` | `fotmob/client.py:204` | "effective_requests_per_second": round(self.effective_rps, 4), |
| `MAX_` | `fotmob/client.py:211` | "max_rate_wait_ms": round(self.max_rate_wait_ms, 3), |
| `TIMEOUT` | `fotmob/client.py:252` | timeout_seconds: int = 10, |
| `MAX_` | `fotmob/client.py:253` | max_retries: int = 3, |
| `MIN_` | `fotmob/client.py:254` | min_request_interval_seconds: float \| None = 1.0, |
| `DELAY` | `fotmob/client.py:255` | retry_delays_seconds: tuple[int, ...] = (1, 3, 10), |
| `MIN_` | `fotmob/client.py:260` | min_rps: float = 0.5, |
| `MAX_` | `fotmob/client.py:261` | max_rps: float = 30.0, |
| `MAX_` | `fotmob/client.py:264` | max_error_rate: float = 0.10, |
| `MAX_` | `fotmob/client.py:265` | max_5xx_rate: float = 0.05, |
| `TIMEOUT` | `fotmob/client.py:266` | max_timeout_rate: float = 0.05, |
| `MAX_` | `fotmob/client.py:266` | max_timeout_rate: float = 0.05, |
| `MAX_` | `fotmob/client.py:267` | max_connection_error_rate: float = 0.05, |
| `MAX_` | `fotmob/client.py:268` | max_p95_latency_ms: float = 3000.0, |
| `TIMEOUT` | `fotmob/client.py:276` | self.timeout_seconds = max(1, int(timeout_seconds)) |
| `MAX_` | `fotmob/client.py:277` | self.max_retries = max(0, int(max_retries)) |
| `MIN_` | `fotmob/client.py:278` | self.min_request_interval_seconds = ( |
| `MIN_` | `fotmob/client.py:279` | max(0.0, float(min_request_interval_seconds)) |
| `MIN_` | `fotmob/client.py:280` | if min_request_interval_seconds is not None |
| `DELAY` | `fotmob/client.py:283` | self.retry_delays_seconds = tuple(float(item) for item in retry_delays_seconds) or (1.0,) |
| `MAX_` | `fotmob/client.py:302` | max_retries=0, |
| `MIN_` | `fotmob/client.py:317` | min_rps=min_rps, |
| `MAX_` | `fotmob/client.py:318` | max_rps=max_rps, |
| `MAX_` | `fotmob/client.py:321` | max_error_rate=max_error_rate, |
| `MAX_` | `fotmob/client.py:322` | max_5xx_rate=max_5xx_rate, |
| `TIMEOUT` | `fotmob/client.py:323` | max_timeout_rate=max_timeout_rate, |
| `MAX_` | `fotmob/client.py:323` | max_timeout_rate=max_timeout_rate, |
| `MAX_` | `fotmob/client.py:324` | max_connection_error_rate=max_connection_error_rate, |
| `MAX_` | `fotmob/client.py:325` | max_p95_latency_ms=max_p95_latency_ms, |
| `DISABLED` | `fotmob/client.py:330` | self._rate_disabled = self.min_request_interval_seconds == 0.0 |
| `MIN_` | `fotmob/client.py:330` | self._rate_disabled = self.min_request_interval_seconds == 0.0 |
| `MIN_` | `fotmob/client.py:335` | and self.min_request_interval_seconds is not None |
| `MIN_` | `fotmob/client.py:336` | and self.min_request_interval_seconds > 0 |
| `MIN_` | `fotmob/client.py:339` | 1.0 / self.min_request_interval_seconds, |
| `RATE_LIMIT` | `fotmob/client.py:360` | def _wait_for_rate_limit(self) -> None: |
| `DISABLED` | `fotmob/client.py:361` | if self._rate_disabled: |
| `MAX_` | `fotmob/client.py:371` | self.metrics.max_rate_wait_ms = max(self.metrics.max_rate_wait_ms, waited_ms) |
| `RATE_LIMIT` | `fotmob/client.py:457` | self.metrics.rate_limit_responses += 1 |
| `TIMEOUT` | `fotmob/client.py:462` | if error_kind == "timeout": |
| `TIMEOUT` | `fotmob/client.py:463` | self.metrics.timeout_errors += 1 |
| `MAX_` | `fotmob/client.py:518` | for attempt in range(self.max_retries + 1): |
| `DELAY` | `fotmob/client.py:523` | delay = self.retry_delays_seconds[min(attempt - 1, len(self.retry_delays_seconds) - 1)] |
| `SLEEP` | `fotmob/client.py:524` | time.sleep(delay) |
| `DELAY` | `fotmob/client.py:524` | time.sleep(delay) |
| `RATE_LIMIT` | `fotmob/client.py:525` | self._wait_for_rate_limit() |
| `TIMEOUT` | `fotmob/client.py:533` | response = self.session.get(url, timeout=self.timeout_seconds) |
| `MAX_` | `fotmob/client.py:547` | terminal_error=not retryable or attempt >= self.max_retries, |
| `MAX_` | `fotmob/client.py:551` | if not retryable or attempt >= self.max_retries: |
| `TIMEOUT` | `fotmob/client.py:582` | except requests.Timeout as exc: |
| `TIMEOUT` | `fotmob/client.py:589` | error_kind="timeout", |
| `MAX_` | `fotmob/client.py:590` | terminal_error=attempt >= self.max_retries, |
| `MAX_` | `fotmob/client.py:594` | if attempt >= self.max_retries: |
| `MAX_` | `fotmob/client.py:604` | terminal_error=attempt >= self.max_retries, |
| `MAX_` | `fotmob/client.py:608` | if attempt >= self.max_retries: |
| `MAX_` | `fotmob/client.py:618` | terminal_error=attempt >= self.max_retries, |
| `MAX_` | `fotmob/client.py:622` | if attempt >= self.max_retries: |
| `MANUAL` | `fotmob/client.py:741` | reason: str = "manual_rate_change", |
| `MAX_` | `fotmob/client.py:747` | def set_benchmark_max_rps( |
| `MAX_` | `fotmob/client.py:749` | max_rps: float \| None, |
| `MAX_` | `fotmob/client.py:751` | reason: str = "benchmark_max_rps", |
| `MAX_` | `fotmob/client.py:755` | return self._rate_controller.set_max_rps_override(max_rps, reason=reason) |
| `SAFE` | `fotmob/enrichment.py:1` | """Safe Tipico-to-FotMob fixture resolution for the V0.5.3 HT path.""" |
| `MANUAL` | `fotmob/enrichment.py:17` | CONFIRMED_LINK_STATUSES = {"EXACT", "HIGH_CONFIDENCE", "MANUALLY_CONFIRMED"} |
| `MANUAL` | `fotmob/enrichment.py:29` | match_status: str = "MANUALLY_CONFIRMED" |
| `MAX_` | `fotmob/history_cli.py:17` | from .max_throughput import write_max_status_report, write_max_throughput_report |
| `WORKERS` | `fotmob/history_cli.py:87` | fetch.add_argument("--workers", type=int) |
| `WORKERS` | `fotmob/history_cli.py:107` | dates.add_argument("--workers", type=int) |
| `MAX_` | `fotmob/history_cli.py:128` | max_performance = subparsers.add_parser( |
| `MAX_` | `fotmob/history_cli.py:133` | _add_root(max_performance) |
| `MAX_` | `fotmob/history_cli.py:134` | max_performance.add_argument("--from-date", required=True, help="Startdatum YYYY-MM-DD") |
| `MAX_` | `fotmob/history_cli.py:135` | max_performance.add_argument("--to-date", required=True, help="Enddatum YYYY-MM-DD") |
| `MAX_` | `fotmob/history_cli.py:136` | max_performance.add_argument( |
| `MAX_` | `fotmob/history_cli.py:142` | max_performance.add_argument( |
| `MAX_` | `fotmob/history_cli.py:148` | max_performance.add_argument( |
| `MAX_` | `fotmob/history_cli.py:154` | max_performance.add_argument( |
| `MANUAL` | `fotmob/history_cli.py:244` | execution_mode="manual", |
| `MAX_` | `fotmob/history_cli.py:265` | result = pipeline.run_max_throughput_probe( |
| `MAX_` | `fotmob/history_cli.py:270` | max_target_rps=args.max_target_rps, |
| `MANUAL` | `fotmob/history_cli.py:272` | execution_mode="manual", |
| `MAX_` | `fotmob/history_cli.py:274` | report_path = write_max_throughput_report( |
| `MAX_` | `fotmob/history_cli.py:276` | root / "outputs" / "V0561_MAX_THROUGHPUT_REPORT.md", |
| `MAX_` | `fotmob/history_cli.py:278` | status_path = write_max_status_report( |
| `MANUAL` | `fotmob/history_cli.py:293` | execution_mode="manual", |
| `NETWORK_MODE` | `fotmob/history_cli.py:304` | "network_mode": settings.fotmob_network_mode, |
| `WORKERS` | `fotmob/history_cli.py:316` | workers=args.workers or settings.fotmob_history_workers, |
| `MANUAL` | `fotmob/history_cli.py:317` | execution_mode="manual", |
| `MANUAL` | `fotmob/history_cli.py:334` | execution_mode="manual", |
| `MANUAL` | `fotmob/history_cli.py:352` | execution_mode="manual", |
| `WORKERS` | `fotmob/history_cli.py:381` | workers=args.workers or settings.fotmob_history_workers, |
| `BATCH_SIZE` | `fotmob/history_cli.py:385` | batch_size=args.batch_size or settings.fotmob_history_batch_size, |
| `MANUAL` | `fotmob/history_cli.py:386` | execution_mode="manual", |
| `SAFE` | `fotmob/history_pipeline.py:4` | is safe to use with local fixtures in tests. Explicit CLI jobs use the |
| `MANUAL` | `fotmob/history_pipeline.py:5` | ``manual`` network mode; a permanent worker remains behind the stricter |
| `NETWORK_MODE` | `fotmob/history_pipeline.py:74` | def _network_mode(settings: Settings) -> str: |
| `NETWORK_MODE` | `fotmob/history_pipeline.py:75` | mode = str(getattr(settings, "fotmob_network_mode", "off")).strip().casefold() |
| `MANUAL` | `fotmob/history_pipeline.py:76` | return mode if mode in {"off", "manual", "worker"} else "off" |
| `MANUAL` | `fotmob/history_pipeline.py:79` | def manual_history_allowed(settings: Settings) -> bool: |
| `NETWORK_MODE` | `fotmob/history_pipeline.py:85` | and _network_mode(settings) == "manual" |
| `MANUAL` | `fotmob/history_pipeline.py:85` | and _network_mode(settings) == "manual" |
| `NETWORK_MODE` | `fotmob/history_pipeline.py:95` | and _network_mode(settings) == "worker" |
| `MANUAL` | `fotmob/history_pipeline.py:109` | if mode == "manual": |
| `MANUAL` | `fotmob/history_pipeline.py:110` | return manual_history_allowed(settings) |
| `TIMEOUT` | `fotmob/history_pipeline.py:188` | timeout_seconds=settings.fotmob_history_timeout_seconds, |
| `MAX_` | `fotmob/history_pipeline.py:189` | max_retries=settings.fotmob_history_max_retries, |
| `MIN_` | `fotmob/history_pipeline.py:190` | min_request_interval_seconds=None, |
| `REQUESTS_PER_SECOND` | `fotmob/history_pipeline.py:195` | getattr(settings, "fotmob_history_requests_per_second", 5.0), |
| `MIN_` | `fotmob/history_pipeline.py:198` | min_rps=getattr(settings, "fotmob_min_rps", 0.5), |
| `MAX_` | `fotmob/history_pipeline.py:199` | max_rps=getattr(settings, "fotmob_max_rps", 30.0), |
| `MAX_` | `fotmob/history_pipeline.py:202` | max_error_rate=getattr(settings, "fotmob_max_error_rate", 0.10), |
| `MAX_` | `fotmob/history_pipeline.py:203` | max_5xx_rate=getattr(settings, "fotmob_max_5xx_rate", 0.05), |
| `TIMEOUT` | `fotmob/history_pipeline.py:204` | max_timeout_rate=getattr(settings, "fotmob_max_timeout_rate", 0.05), |
| `MAX_` | `fotmob/history_pipeline.py:204` | max_timeout_rate=getattr(settings, "fotmob_max_timeout_rate", 0.05), |
| `MAX_` | `fotmob/history_pipeline.py:205` | max_connection_error_rate=getattr( |
| `MAX_` | `fotmob/history_pipeline.py:206` | settings, "fotmob_max_connection_error_rate", 0.05 |
| `MAX_` | `fotmob/history_pipeline.py:208` | max_p95_latency_ms=getattr(settings, "fotmob_max_p95_latency_ms", 3000.0), |
| `MAX_` | `fotmob/history_pipeline.py:218` | known_stable_rps = self.store.known_stable_max_rps( |
| `WORKERS` | `fotmob/history_pipeline.py:225` | "rps_step=%.2f max_rps=%.2f workers=%d max_workers=%d pool=%d " |
| `MAX_` | `fotmob/history_pipeline.py:225` | "rps_step=%.2f max_rps=%.2f workers=%d max_workers=%d pool=%d " |
| `MAX_` | `fotmob/history_pipeline.py:230` | float(getattr(self.settings, "fotmob_max_rps", 30.0)), |
| `WORKERS` | `fotmob/history_pipeline.py:231` | int(getattr(self.settings, "fotmob_initial_workers", 10)), |
| `WORKERS` | `fotmob/history_pipeline.py:232` | int(getattr(self.settings, "fotmob_max_workers", 40)), |
| `MAX_` | `fotmob/history_pipeline.py:232` | int(getattr(self.settings, "fotmob_max_workers", 40)), |
| `WORKERS` | `fotmob/history_pipeline.py:237` | def _configured_workers(self, workers: int \| None) -> int: |
| `WORKERS` | `fotmob/history_pipeline.py:238` | default_workers = int( |
| `WORKERS` | `fotmob/history_pipeline.py:241` | "fotmob_initial_workers", |
| `WORKERS` | `fotmob/history_pipeline.py:242` | getattr(self.settings, "fotmob_history_workers", 10), |
| `WORKERS` | `fotmob/history_pipeline.py:245` | max_workers = int( |
| `MAX_` | `fotmob/history_pipeline.py:245` | max_workers = int( |
| `WORKERS` | `fotmob/history_pipeline.py:248` | "fotmob_max_workers", |
| `MAX_` | `fotmob/history_pipeline.py:248` | "fotmob_max_workers", |
| `WORKERS` | `fotmob/history_pipeline.py:249` | max(default_workers, getattr(self.settings, "fotmob_history_workers", 10)), |
| `WORKERS` | `fotmob/history_pipeline.py:252` | requested = default_workers if workers is None else int(workers) |
| `WORKERS` | `fotmob/history_pipeline.py:253` | return max(1, min(max_workers, requested)) |
| `MAX_` | `fotmob/history_pipeline.py:253` | return max(1, min(max_workers, requested)) |
| `NETWORK_MODE` | `fotmob/history_pipeline.py:259` | "FOTMOB_HISTORY_ENABLED=true und FOTMOB_NETWORK_MODE=manual. " |
| `MANUAL` | `fotmob/history_pipeline.py:259` | "FOTMOB_HISTORY_ENABLED=true und FOTMOB_NETWORK_MODE=manual. " |
| `MAX_` | `fotmob/history_pipeline.py:404` | max_attempts=self.settings.fotmob_history_max_retry_attempts, |
| `MAX_` | `fotmob/history_pipeline.py:504` | max_attempts=self.settings.fotmob_history_max_retry_attempts, |
| `WORKERS` | `fotmob/history_pipeline.py:524` | workers: int \| None = None, |
| `MANUAL` | `fotmob/history_pipeline.py:528` | execution_mode: str = "manual", |
| `WORKERS` | `fotmob/history_pipeline.py:553` | "workers": self._configured_workers(workers), |

## Safety conclusion

No proxy rotation, IP rotation, CAPTCHA bypass, fingerprint masking or provider-protection bypass was added. Read-only manual research is enabled; permanent worker access and destructive/storage-heavy behavior remain explicitly guarded.
