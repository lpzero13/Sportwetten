"""Read-only Streamlit view for collector coverage and event timelines."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from config import Settings
from fotmob.service import FotMobService
from storage.database import Database
from storage.parquet_archive import ParquetArchive
from ui.time_format import format_local_datetime


def _load_status(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _score(row: object) -> str:
    home = row["score_home"] if row["score_home"] is not None else "-"  # type: ignore[index]
    away = row["score_away"] if row["score_away"] is not None else "-"  # type: ignore[index]
    return f"{home}:{away}"


def _directory_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            try:
                total += candidate.stat().st_size
            except OSError:
                pass
    return total


def _size_label(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0.0 B"


def _duration_seconds(start: object, end: object) -> float | None:
    """Parse persisted UTC timestamps for a non-invasive progress estimate."""

    if not start or not end:
        return None
    try:
        left = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        if left.tzinfo is None:
            left = left.replace(tzinfo=timezone.utc)
        if right.tzinfo is None:
            right = right.replace(tzinfo=timezone.utc)
        return max(0.0, (right - left).total_seconds())
    except (TypeError, ValueError):
        return None


def render_data_collection(
    database: Database,
    settings: Settings,
    *,
    fotmob_service: FotMobService | None = None,
) -> None:
    """Render collector state without making any Tipico request."""

    st.title("Data Collection")
    status = _load_status(settings.collector_status_path)
    # Older/partially initialized databases may return an empty metrics
    # object.  The Data view must remain usable while the collector is still
    # warming up; storage diagnostics are not allowed to crash on a missing
    # optional ``date`` field.
    raw_coverage = database.collection_metrics_for_date()
    coverage = dict(raw_coverage) if isinstance(raw_coverage, Mapping) else {}
    today_default = datetime.now(timezone.utc).date().isoformat()
    coverage.setdefault("date", today_default)
    for key in (
        "outbox_pending", "snapshots_today", "matches_today", "paper_trades_today",
        "events_with_prematch_snapshot", "events_with_halftime_snapshot",
        "events_with_core_live_tracking", "events_with_final_result",
    ):
        coverage.setdefault(key, 0)
    canonical = database.canonical_metrics_for_date()
    snapshot_counts = status.get("snapshot_counts", {})
    feed = status.get("feed", {})
    prematch = status.get("prematch", {})
    detail = status.get("detail", {})
    archive = ParquetArchive(settings.archive_path)

    status_label = status.get("status", "NO_STATUS_FILE")
    if status_label == "RUNNING":
        st.info("Background Collector läuft.")
    elif status_label == "NO_STATUS_FILE":
        st.warning(
            "Noch kein Collector-Status vorhanden. Starte scripts/run_collector.py, "
            "um historische Daten zu sammeln."
        )
    else:
        st.caption(f"Collector-Status: {status_label}")

    runtime_warnings = status.get("runtime_warnings", [])
    if runtime_warnings:
        st.warning("Runtime-Warnungen: " + " · ".join(str(item) for item in runtime_warnings))
    matrix = status.get("feature_runtime_matrix", [])
    if matrix:
        with st.expander("Feature Runtime Matrix", expanded=bool(runtime_warnings)):
            st.dataframe(
                [
                    {
                        "Feature": item.get("feature"),
                        "Konfiguriert": "AN" if item.get("configured_enabled") else "AUS",
                        "Effektiv": "AN" if item.get("effective_enabled") else "AUS",
                        "Blocking Gate": item.get("blocking_gate") or "—",
                        "Grund": item.get("reason") or "—",
                    }
                    for item in matrix
                    if isinstance(item, dict)
                ],
                hide_index=True,
                width="stretch",
            )
    identity = status.get("runtime", {}) or {}
    if identity:
        st.caption(
            f"Version {identity.get('app_version', status.get('app_version', '—'))} · "
            f"Commit {str(identity.get('git_commit', status.get('git_commit', '—')))[:12]} · "
            f"Config {str(identity.get('config_fingerprint', status.get('config_fingerprint', '—')))[:19]}"
        )

    columns = st.columns(4)
    columns[0].metric("Events heute", coverage["football_events_seen"])
    columns[1].metric("Wettbewerbe", coverage["competitions"])
    columns[2].metric(
        "Pre-Match / Kickoff",
        f"{coverage['events_with_prematch_snapshot']} / {coverage['football_events_seen']}" ,
    )
    columns[3].metric(
        "Half-Time",
        f"{coverage['events_with_halftime_snapshot']} / {coverage['football_events_seen']}",
    )

    columns = st.columns(5)
    columns[0].metric("HT stabil", coverage["ht_stable_snapshots"])
    columns[1].metric("Minute 60 / 70", f"{coverage['minute_60_snapshots']} / {coverage['minute_70_snapshots']}")
    columns[2].metric("Minute 80 / 85 / 90", f"{coverage['minute_80_snapshots']} / {coverage['minute_85_snapshots']} / {coverage['minute_90_snapshots']}")
    columns[3].metric("HZ2-Reopen", coverage["goal_reopen_snapshots"])
    columns[4].metric("Final / Results", f"{coverage['final_snapshots']} / {coverage['events_with_final_result']}")
    columns = st.columns(2)
    columns[0].metric(
        "API-Fehler",
        int(feed.get("errors", 0))
        + int(prematch.get("errors", 0))
        + int(detail.get("errors", 0)),
    )
    columns[1].metric("Detail-Fehlerquote", f"{float(detail.get('error_rate', 0)) * 100:.1f}%")

    st.subheader("Canonical-Market-Coverage")
    canonical_columns = st.columns(5)
    canonical_columns[0].metric("Historische Outcomes", canonical["total"])
    canonical_columns[1].metric("Bekannt", canonical["known"])
    canonical_columns[2].metric("UNKNOWN", canonical["unknown"])
    canonical_columns[3].metric("Historische Events", canonical["events"])
    canonical_columns[4].metric("Current Outcomes", database.count_rows("current_canonical_outcomes"))
    if canonical["total"]:
        st.caption(
            f"Coverage: {canonical['known'] / canonical['total'] * 100:.1f}% · "
            f"letzte Normalisierung: "
            f"{format_local_datetime(canonical['latest_observed_at'])} · "
            "Normalizer v0.3.1"
        )
    unknown_rows = database.unknown_market_types()
    if unknown_rows:
        with st.expander("Unknown Market Types / Mapping-Debugger", expanded=False):
            st.dataframe(
                [
                    {
                        "Raw Type": row["raw_market_type"] or "—",
                        "Raw Caption": row["raw_market_caption"] or "—",
                        "Outcomes": row["outcome_count"],
                        "Letzte Beobachtung": format_local_datetime(row["latest_observed_at"]),
                    }
                    for row in unknown_rows
                ],
                hide_index=True,
                width="stretch",
            )

    st.subheader("Collector-Metriken")
    st.write(
        {
            "Snapshots": snapshot_counts,
            "Retries": status.get("retries", 0),
            "Reopens erkannt": status.get("reopens_detected", 0),
            "Queue": status.get("queue_depth", 0),
            "Letztes Update": format_local_datetime(status.get("updated_at")),
        }
    )
    request_rows = []
    for name, summary in (
        ("Livefeed", feed),
        ("Pre-Match-Feed", prematch),
        ("Eventdetail", detail),
    ):
        request_rows.append(
            {
                "Quelle": name,
                "Requests": summary.get("requests", 0),
                "Fehler": summary.get("errors", 0),
                "Median ms": summary.get("median_response_ms", 0),
                "P95 ms": summary.get("p95_response_ms", 0),
                "Max ms": summary.get("max_response_ms", 0),
                "Ø Payload Bytes": summary.get("average_payload_bytes", 0),
            }
        )
    st.dataframe(request_rows, hide_index=True, width="stretch")

    st.subheader("Storage Overview")
    db_size = database.database_size_bytes
    parquet_size = archive.total_size_bytes
    raw_size = _directory_size_bytes(settings.raw_storage_path)
    today = str(coverage.get("date") or today_default)
    archive_today = archive.size_for_date(today)
    raw_today = _directory_size_bytes(settings.raw_storage_path / today)
    growth_today = archive_today + raw_today
    mb_today = growth_today / 1024 / 1024
    estimated_gb_year = mb_today * 365 / 1024
    storage_columns = st.columns(4)
    storage_columns[0].metric("SQLite gesamt", _size_label(db_size))
    storage_columns[1].metric("Parquet gesamt", _size_label(parquet_size))
    storage_columns[2].metric("Raw-Archiv gesamt", _size_label(raw_size))
    storage_columns[3].metric("Outbox pending", coverage.get("outbox_pending", 0))
    storage_columns = st.columns(4)
    storage_columns[0].metric("Snapshots heute", coverage.get("snapshots_today", 0))
    storage_columns[1].metric("Matches heute", coverage.get("matches_today", 0))
    storage_columns[2].metric("Paper Trades heute", coverage.get("paper_trades_today", 0))
    storage_columns[3].metric(
        "Ø Snapshots / Finished Match",
        f"{coverage['average_snapshots_per_finished_match']:.2f}",
        help="Zielwert laut V0.4.2: maximal 10 historische Slots je beendetem Spiel.",
    )
    st.caption(
        f"Archivwachstum heute: {mb_today:.3f} MB · hochgerechnet: "
        f"{estimated_gb_year:.2f} GB/Jahr · letzte Parquet-Ausgabe: "
        f"{format_local_datetime(coverage['last_parquet_export'])} · "
        f"{archive.snapshot_root}"
    )
    st.caption(
        "Refreshes bleiben Current State. Historische Snapshots entstehen ausschließlich "
        "über die zehn fachlichen Collector-Slots."
    )
    if fotmob_service is not None:
        fotmob = fotmob_service.metrics()
        st.subheader("FotMob-Enrichment")
        fotmob_columns = st.columns(5)
        fotmob_columns[0].metric("Feature", "AN" if fotmob_service.enabled else "AUS")
        fotmob_columns[1].metric("Matches", fotmob["matches"])
        fotmob_columns[2].metric("Links", fotmob["links"])
        fotmob_columns[3].metric("Current", fotmob["current_state"])
        fotmob_columns[4].metric("Snapshots", fotmob["snapshots"])
        st.caption(
            f"FotMob HT-Stats: {fotmob['ht_stats']} · Outbox pending: "
            f"{fotmob['outbox_pending']} · Auto-Link-Rate: "
            f"{fotmob['automatic_match_rate'] * 100:.1f}% · getrennt von Tipico-Strategie und Paper Trading."
        )
        fotmob_access = fotmob.get("access", {}) or {}
        fotmob_rate = fotmob_access.get("rate_control", {}) or {}
        fotmob_config = fotmob.get("performance_configuration", {}) or {}
        performance_columns = st.columns(5)
        performance_columns[0].metric(
            "FotMob RPS",
            f"{float(fotmob_access.get('current_rps', fotmob_rate.get('current_rps', 0.0)) or 0.0):.2f}",
        )
        performance_columns[1].metric(
            "Effektiv RPS",
            f"{float(fotmob_access.get('effective_rps', 0.0) or 0.0):.2f}",
        )
        performance_columns[2].metric(
            "FotMob Requests",
            fotmob_access.get("requests", 0),
        )
        performance_columns[3].metric(
            "FotMob 429",
            fotmob_access.get("429", fotmob_access.get("rate_limit_responses", 0)),
        )
        performance_columns[4].metric(
            "Worker / Max",
            f"{fotmob_config.get('initial_workers', '—')} / {fotmob_config.get('max_workers', '—')}",
        )
    st.subheader("Persistenz")
    persistence_columns = st.columns(4)
    persistence_columns[0].metric("Historische Snapshots", database.count_rows("snapshots"))
    persistence_columns[1].metric("Match Results", database.count_rows("match_results"))
    persistence_columns[2].metric("Paper Trades", database.count_rows("paper_trades"))
    persistence_columns[3].metric("Current Events", database.count_rows("current_event_state"))
    result_backfill = database.result_backfill_status()
    result_finalization = database.result_finalization_status()
    result_queue = result_backfill.get("queue", {}) or {}
    result_sources = result_backfill.get("result_sources", {}) or {}
    st.subheader("Ergebnis-Nachpflege")
    result_columns = st.columns(6)
    result_columns[0].metric(
        "FT freigegeben",
        result_finalization.get("ft_ready", 0),
        help="Endstände mit ausreichender Ergebnisqualität für FT-Auswertungen.",
    )
    result_columns[1].metric("H2 freigegeben", result_finalization.get("h2_ready", 0))
    result_columns[2].metric("Offen", result_finalization.get("pending", 0))
    result_columns[3].metric("Konflikte", result_finalization.get("conflicts", 0))
    result_columns[4].metric("Durch FotMob ergänzt", result_sources.get("FOTMOB_BACKFILL", 0))
    result_columns[5].metric(
        "Backtest-fähig",
        result_finalization.get("backtest_ready", 0),
        help="Verifiziertes H2-Ergebnis plus gespeicherter HT/HT_STABLE-Einstieg.",
    )
    st.caption(
        f"Ergebnisstatus: {result_finalization.get('result_status', {})} · "
        f"Nicht verfügbar: {result_finalization.get('unavailable', 0)} · "
        f"Letzte Prüfung: {format_local_datetime(result_finalization.get('last_checked_at'))} · "
        f"Letzte Reparatur: {format_local_datetime(result_finalization.get('last_changed_at'))} · "
        "Nächster planmäßiger Lauf: täglich 03:15 Serverzeit"
    )
    retry_columns = st.columns(2)
    retry_columns[0].metric(
        "Retry / Backfill-Queue",
        sum(
            int(value or 0)
            for key, value in result_queue.items()
            if key not in {"APPLIED", "CONFIRMED", "CONFLICT", "CANCELLED", "EXCLUDED_NON_REGULATION"}
        ),
    )
    retry_columns[1].metric("Evidenz-Zeilen", result_backfill.get("evidence_rows", 0))
    st.caption(
        "Rohstatus und kanonischer Status bleiben getrennt. `no_longer_live` wird "
        "nicht allein durch Zeitablauf als beendet gewertet; Endstände, Quellen, "
        "H2-Freigabe und Konflikte bleiben je Event nachvollziehbar."
    )
    if result_queue:
        with st.expander("Backfill-Queue", expanded=False):
            st.write(result_queue)

    latest_run = result_finalization.get("last_run") or {}
    run_coverage = result_finalization.get("last_run_coverage") or {}
    if latest_run:
        st.subheader("Letzter Vollbestands-Lauf")
        run_columns = st.columns(6)
        run_columns[0].metric("Prüfliste", f"{run_coverage.get('n_all', 0):,}")
        run_columns[1].metric("Historisch", f"{run_coverage.get('n_historical', 0):,}")
        run_columns[2].metric("FT", f"{run_coverage.get('n_ft', 0):,}/{run_coverage.get('n_eligible', 0):,}")
        run_columns[3].metric("H2", f"{run_coverage.get('n_h2', 0):,}/{run_coverage.get('n_eligible', 0):,}")
        run_columns[4].metric("H2-Entry", f"{run_coverage.get('n_entry_h2', 0):,}/{run_coverage.get('n_entry', 0):,}")
        run_columns[5].metric(
            "Provider offen / blockiert",
            f"{run_coverage.get('provider_open', 0):,} / {run_coverage.get('provider_blocked', 0):,}",
        )
        st.caption(
            f"Run {latest_run.get('run_id', '—')} · Status {latest_run.get('run_status') or latest_run.get('status') or '—'} · "
            f"vollständig: {'Ja' if run_coverage.get('processing_complete') else 'Nein'} · "
            f"lokal geprüft: {run_coverage.get('n_local_checked', 0):,}/{run_coverage.get('n_historical', 0):,} · "
            f"nicht fällig: {run_coverage.get('n_not_due', 0):,} · Alter unbekannt: {run_coverage.get('n_age_unknown', 0):,} · "
            f"FT brutto: {run_coverage.get('n_ft', 0):,}/{run_coverage.get('n_historical', 0):,} · "
            f"FT-Ziel: {run_coverage.get('ft_target_status', '—')} · "
            f"H2-Entry-Ziel: {run_coverage.get('entry_h2_target_status', '—')} · "
            f"Fehlend bis 90 %: FT {run_coverage.get('missing_ft_to_90', '—')}, "
            f"H2-Entry {run_coverage.get('missing_entry_h2_to_90', '—')}"
        )
        run_stages = (result_finalization.get("last_run_items") or {}).get("stages", {}) or {}
        unprocessed = int(run_coverage.get("unprocessed", 0) or 0)
        historical_total = int(run_coverage.get("n_historical", 0) or 0)
        processed = max(0, historical_total - unprocessed)
        run_status = str(latest_run.get("run_status") or latest_run.get("status") or "")
        run_end = latest_run.get("finished_at") or latest_run.get("completed_at")
        if run_status == "RUNNING":
            run_end = datetime.now(timezone.utc).isoformat()
        elapsed = _duration_seconds(latest_run.get("started_at"), run_end)
        throughput = (processed / elapsed * 60.0) if elapsed and elapsed > 0 and processed else None
        eta_minutes = (unprocessed / throughput) if throughput and unprocessed else None
        progress_columns = st.columns(4)
        progress_columns[0].metric(
            "Fortschritt",
            f"{processed:,}/{historical_total:,}",
            help="Bearbeitete historische Prüflistenzeilen; Provider-No-Data bleibt fachlich ungelöst, aber nicht unversucht.",
        )
        progress_columns[1].metric(
            "Durchsatz",
            f"{throughput:,.0f}/min" if throughput is not None else "—",
        )
        if eta_minutes is not None:
            low = max(0.0, eta_minutes * 0.75)
            high = eta_minutes * 1.50
            eta_label = f"ca. {eta_minutes:.1f} min"
            eta_help = f"Unsicherheitsband: {low:.1f}–{high:.1f} min; basiert auf dem bisherigen Laufdurchsatz."
        elif unprocessed == 0:
            eta_label = "fertig"
            eta_help = "Keine unversuchten historischen Prüflistenzeilen."
        else:
            eta_label = "—"
            eta_help = "Noch kein belastbarer Durchsatz aus dem persistenten Laufstatus."
        progress_columns[2].metric("Restzeit", eta_label, help=eta_help)
        progress_columns[3].metric(
            "Heartbeat",
            format_local_datetime(latest_run.get("heartbeat_at")),
            help=f"Stages: {run_stages or '—'}",
        )

    st.subheader("Ergebnis-Explorer")
    st.caption(
        "Event-Grain: Filter werden in der vollständigen Datenbank ausgeführt; die Anzeige ist paginiert. "
        "Rohstatus, kanonischer Spielstatus, Endstandqualität und verwendbare H2-Zielvariable werden getrennt angezeigt."
    )
    filter_options = database.result_finalization_filter_options()

    def _options(name: str) -> list[str]:
        return ["Alle", *filter_options.get(name, [])]

    filter_columns = st.columns(4)
    selected_date = filter_columns[0].selectbox(
        "Kickoff-Datum", _options("date"), key="result_filter_date"
    )
    selected_country = filter_columns[1].selectbox(
        "Land", _options("country"), key="result_filter_country"
    )
    selected_competition = filter_columns[2].selectbox(
        "Liga", _options("competition"), key="result_filter_competition"
    )
    selected_raw_status = filter_columns[3].selectbox(
        "Rohstatus", _options("raw_status"), key="result_filter_raw_status"
    )
    filter_columns = st.columns(3)
    selected_canonical = filter_columns[0].selectbox(
        "Kanonischer Status", _options("canonical_status"), key="result_filter_canonical"
    )
    selected_source = filter_columns[1].selectbox(
        "Ergebnisquelle", _options("result_source"), key="result_filter_source"
    )
    selected_result_status = filter_columns[2].selectbox(
        "Ergebnisstatus", _options("result_status"), key="result_filter_result_status"
    )
    selected_filters = {
        "date_value": None if selected_date == "Alle" else selected_date,
        "country": None if selected_country == "Alle" else selected_country,
        "competition": None if selected_competition == "Alle" else selected_competition,
        "raw_status": None if selected_raw_status == "Alle" else selected_raw_status,
        "canonical_status": None if selected_canonical == "Alle" else selected_canonical,
        "result_source": None if selected_source == "Alle" else selected_source,
        "result_status": None if selected_result_status == "Alle" else selected_result_status,
    }
    filtered_total = database.result_finalization_count(**selected_filters)
    page_size = st.selectbox("Zeilen pro Seite", [25, 50, 100, 250], index=1, key="result_page_size")
    page_count = max(1, (filtered_total + page_size - 1) // page_size)
    page_number = st.number_input(
        "Seite", min_value=1, max_value=page_count, value=min(int(st.session_state.get("result_page_number", 1)), page_count), step=1,
        key="result_page_number",
    )
    result_rows = database.result_finalization_rows(
        limit=page_size,
        offset=(int(page_number) - 1) * page_size,
        **selected_filters,
    )
    st.caption(f"{filtered_total:,} Events im vollständigen Bestand · Seite {int(page_number)} von {page_count}")
    if result_rows:
        st.dataframe(
            [
                {
                    "Event": row["event_id"],
                    "Datum": str(row["kickoff_time"] or "—")[:10],
                    "Land": row["competition_country"] or "—",
                    "Liga": row["competition_name"] or "—",
                    "Spiel": f"{row['home_team']} – {row['away_team']}",
                    "Rohstatus": row["raw_status"] or "—",
                    "Kanonisch": row["canonical_status"] or "UNKNOWN",
                    "HT": (
                        f"{row['ht_home']}:{row['ht_away']}"
                        if row["ht_home"] is not None and row["ht_away"] is not None else "—"
                    ),
                    "FT": (
                        f"{row['ft_home']}:{row['ft_away']}"
                        if row["ft_home"] is not None and row["ft_away"] is not None else "—"
                    ),
                    "Qualität": row["result_status"] or "PENDING",
                    "FT frei": "Ja" if row["result_use_ft"] else "Nein",
                    "H2 frei": "Ja" if row["result_use_h2"] else "Nein",
                    "Quelle": row["result_source"] or "—",
                    "Grund": row["result_reason"] or "—",
                }
                for row in result_rows
            ],
            hide_index=True,
            width="stretch",
        )
        selected_result_id = st.selectbox(
            "Ergebnisdetail anzeigen",
            [str(row["event_id"]) for row in result_rows],
            format_func=lambda value: next(
                (
                    f"{row['home_team']} – {row['away_team']} · "
                    f"{row['competition_name']} · {value}"
                    for row in result_rows if str(row["event_id"]) == value
                ),
                value,
            ),
            key="result_detail_event",
        )
        selected_result = next(
            row for row in result_rows if str(row["event_id"]) == selected_result_id
        )
        detail_columns = st.columns(4)
        detail_columns[0].metric(
            "Letzter Stand",
            f"{selected_result['current_score_home']}:{selected_result['current_score_away']}"
            if selected_result["current_score_home"] is not None
            and selected_result["current_score_away"] is not None else "—",
        )
        detail_columns[1].metric(
            "HT / FT",
            (
                f"{selected_result['ht_home']}:{selected_result['ht_away']} / "
                f"{selected_result['ft_home']}:{selected_result['ft_away']}"
            ) if selected_result["ft_home"] is not None else "—",
        )
        detail_columns[2].metric("Quelle", selected_result["result_source"] or "—")
        detail_columns[3].metric("Revision", selected_result["result_revision"] or 0)
        st.caption(
            f"Beleg: {selected_result['evidence_type'] or '—'} / "
            f"{selected_result['evidence_record_id'] or '—'} · "
            f"Grund: {selected_result['result_reason'] or '—'} · "
            f"Scope: {selected_result['result_scope_status'] or '—'} · "
            f"zuletzt geprüft: {format_local_datetime(selected_result['result_last_checked_at'])}"
        )
        evidence_rows = database.result_evidence_for_event(selected_result_id)
        if evidence_rows:
            with st.expander("Evidenz und Herkunft", expanded=False):
                st.dataframe(
                    [
                        {
                            "Provider": row["provider"],
                            "Typ": row["source_record_type"] or "—",
                            "Referenz": row["source_record_id"] or "—",
                            "Status": row["validation_status"] or "—",
                            "Rohstatus": row["raw_status"] or "—",
                            "HT": f"{row['ht_home']}:{row['ht_away']}"
                            if row["ht_home"] is not None and row["ht_away"] is not None else "—",
                            "FT": f"{row['ft_home']}:{row['ft_away']}"
                            if row["ft_home"] is not None and row["ft_away"] is not None else "—",
                            "Regel": row["rule_version"] or "—",
                            "Beobachtet": format_local_datetime(row["observed_at"]),
                        }
                        for row in evidence_rows
                    ],
                    hide_index=True,
                    width="stretch",
                )
    elif filtered_total:
        st.info("Für diese Seite wurden keine Zeilen geladen. Seite zurücksetzen.")
    else:
        st.info("Noch keine Fußball-Events für den Ergebnis-Explorer vorhanden.")
    market_type_rows = database.market_type_counts()
    if market_type_rows:
        with st.expander("Beobachtete Market Types", expanded=False):
            st.dataframe(
                [
                    {
                        "Market Type": row["type"] or "—",
                        "Markets": row["market_count"],
                        "Zuletzt gesehen": row["latest_seen_at"],
                    }
                    for row in market_type_rows
                ],
                hide_index=True,
                width="stretch",
            )

    if status.get("errors"):
        with st.expander("Letzte Collector-Fehler"):
            st.code("\n".join(str(item) for item in status["errors"]))

    st.subheader("Event Data Inspector")
    events = database.list_events_for_inspector()
    if not events:
        st.info("Noch keine Events in der historischen Datenbank.")
        return

    event_ids = [str(row["event_id"]) for row in events]
    labels = {
        str(row["event_id"]): (
            f"{row['home_team']} – {row['away_team']} · "
            f"{row['competition_name']} · {row['competition_country'] or 'Land unbekannt'} · {row['event_id']}"
        )
        for row in events
    }
    selected_id = st.selectbox(
        "Event auswählen",
        event_ids,
        format_func=lambda value: labels.get(value, value),
    )
    selected = database.event_info(selected_id)
    if selected is None:
        return
    st.caption(
        f"{selected['home_team']} – {selected['away_team']} · "
        f"{selected['competition_name']} · {selected['competition_country'] or 'Land unbekannt'} · Event {selected['event_id']}"
    )

    snapshots = database.snapshots_for_event(selected_id)
    timeline = [
        {
            "Zeit": format_local_datetime(row["observed_at"]),
            "Phase": row["display_time"] or row["match_status"] or "—",
            "Score": _score(row),
            "Snapshot": row["snapshot_type"],
            "Trigger": row["trigger_reason"] or "—",
            "Markets": row["market_count"],
            "Outcomes": row["outcome_count"],
            "Quality": row["snapshot_quality"] or "—",
        }
        for row in snapshots
    ]
    if timeline:
        st.dataframe(timeline, hide_index=True, width="stretch")
        selected_snapshot_id = st.selectbox(
            "Snapshot-Märkte anzeigen",
            [int(row["snapshot_id"]) for row in snapshots],
            format_func=lambda value: next(
                (
                    f"{row['snapshot_type']} · "
                    f"{format_local_datetime(row['observed_at'])} · "
                    f"{row['market_count']} Märkte"
                    for row in snapshots
                    if int(row["snapshot_id"]) == value
                ),
                str(value),
            ),
        )
        presence = database.market_presence_for_snapshot(selected_snapshot_id)
        if presence:
            st.caption(f"Legacy-Markt-Präsenz: {len(presence)} Märkte")
            st.dataframe(
                [
                    {
                        "Market ID": row["market_id"],
                        "Type": row["market_type"] or "—",
                        "fixedParam": row["fixed_param"] or "—",
                        "Status": row["market_status"] or "—",
                    }
                    for row in presence
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            selected_snapshot = next(
                (row for row in snapshots if int(row["snapshot_id"]) == selected_snapshot_id),
                None,
            )
            relevant = []
            if selected_snapshot is not None:
                try:
                    parsed = json.loads(selected_snapshot["relevant_markets_json"] or "[]")
                    relevant = parsed if isinstance(parsed, list) else []
                except (TypeError, ValueError):
                    relevant = []
            st.caption(f"Relevante Märkte im Snapshot: {len(relevant)}")
            if relevant:
                st.dataframe(relevant, hide_index=True, width="stretch")
    else:
        st.info("Für dieses Event sind noch keine Snapshots gespeichert.")
