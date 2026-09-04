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
