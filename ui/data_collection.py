"""Read-only Streamlit view for collector coverage and event timelines."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from config import Settings
from storage.database import Database
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


def render_data_collection(database: Database, settings: Settings) -> None:
    """Render collector state without making any Tipico request."""

    st.title("Data Collection")
    status = _load_status(settings.collector_status_path)
    coverage = database.collection_metrics_for_date()
    canonical = database.canonical_metrics_for_date()
    snapshot_counts = status.get("snapshot_counts", {})
    feed = status.get("feed", {})
    prematch = status.get("prematch", {})
    detail = status.get("detail", {})

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
    columns[0].metric("Periodic", coverage["periodic_snapshots"])
    columns[1].metric("Goal Trigger", coverage["goal_triggers"])
    columns[2].metric("Final", coverage["events_with_final_result"])
    columns[3].metric("Core-Tracking", coverage["events_with_core_live_tracking"])
    columns[4].metric(
        "API-Fehler",
        int(feed.get("errors", 0))
        + int(prematch.get("errors", 0))
        + int(detail.get("errors", 0)),
    )

    st.subheader("Canonical-Market-Coverage")
    canonical_columns = st.columns(4)
    canonical_columns[0].metric("Normalisierte Outcomes", canonical["total"])
    canonical_columns[1].metric("Bekannt", canonical["known"])
    canonical_columns[2].metric("UNKNOWN", canonical["unknown"])
    canonical_columns[3].metric("Events analysiert", canonical["events"])
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

    st.subheader("Persistenz")
    persistence_columns = st.columns(3)
    persistence_columns[0].metric("Snapshots gesamt", database.count_rows("snapshots"))
    persistence_columns[1].metric(
        "Canonical Outcomes gesamt",
        database.count_rows("canonical_outcomes"),
    )
    persistence_columns[2].metric(
        "DB-Größe",
        f"{database.database_size_bytes / 1024:.1f} KB",
    )
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
            f"{row['competition_name']} · {row['event_id']}"
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
        f"{selected['competition_name']} · Event {selected['event_id']}"
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
        st.caption(f"Markt-Präsenz: {len(presence)} Märkte")
        if presence:
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
        st.info("Für dieses Event sind noch keine Snapshots gespeichert.")
