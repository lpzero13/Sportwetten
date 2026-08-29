"""Debug/system page for PoC verification."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from services.event_service import EventService
from services.market_service import MarketService
from storage.database import Database
from ui.time_format import format_local_datetime


def render_debug_page(
    event_service: EventService,
    market_service: MarketService,
    database: Database,
    *,
    database_path: Path,
    raw_storage_enabled: bool,
) -> None:
    st.title("Debug / System")
    metrics = event_service.last_metrics or market_service.last_metrics
    api_online = event_service.last_error is None and metrics is not None
    st.metric("Tipico API Status", "ONLINE" if api_online else "UNBEKANNT / FEHLER")

    columns = st.columns(4)
    columns[0].metric("Live-Events", len(event_service.events))
    columns[1].metric("Feed Requests", event_service.request_count)
    columns[2].metric("Detail Requests", market_service.request_count)
    columns[3].metric("Odds Changes heute", database.odds_changes_today())

    if event_service.last_error:
        st.warning(event_service.last_error)
    if metrics:
        st.write(
            {
                "Last request": format_local_datetime(metrics.response_received_at),
                "Response": f"HTTP {metrics.status_code}",
                "Response time": f"{metrics.response_time_ms} ms",
                "Payload": f"{metrics.payload_size} bytes",
                "Endpoint": metrics.endpoint,
            }
        )

    st.write(
        {
            "Database": str(database_path),
            "Raw payload storage": "ON" if raw_storage_enabled else "OFF",
            "Events rows": database.count_rows("events"),
            "Event states rows": database.count_rows("event_states"),
            "Markets rows": database.count_rows("markets"),
            "Outcomes rows": database.count_rows("outcomes"),
            "Odds history rows": database.count_rows("odds_history"),
            "Canonical outcomes rows": database.count_rows("canonical_outcomes"),
            "Strategy evaluations rows": database.count_rows("strategy_evaluations"),
        }
    )

    canonical = database.canonical_metrics_for_date()
    st.subheader("V0.3 Normalizer")
    st.write(canonical)

    st.subheader("Letzte 50 Quoten-/Statusänderungen")
    rows = []
    for row in database.recent_odds_changes(50):
        rows.append(
            {
                "Zeit": format_local_datetime(row["observed_at"]),
                "Spiel": f"{row['home_team'] or '?'} – {row['away_team'] or '?'}",
                "Markt": row["market_caption"] or row["market_id"],
                "Auswahl": row["outcome_caption"] or row["outcome_id"],
                "Quote": row["odds"],
                "Status": row["status"],
                "Verfügbar": bool(row["available"]),
            }
        )
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Noch keine Quotenänderungen gespeichert.")
