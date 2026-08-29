"""Event-detail rendering. Data has already been normalized by the service."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from models.market import EventDetails
from tipico.client import RequestMetrics
from ui.time_format import format_local_datetime


def _age_seconds(metrics: RequestMetrics | None) -> float | None:
    if metrics is None:
        return None
    try:
        timestamp = datetime.fromisoformat(
            metrics.response_received_at.replace("Z", "+00:00")
        )
    except ValueError:
        return None
    return max(
        0.0,
        (
            datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
        ).total_seconds(),
    )


def render_event_detail(
    details: EventDetails,
    *,
    metrics: RequestMetrics | None,
    stale: bool = False,
) -> None:
    event = details.event
    render_event_header(details, metrics=metrics, stale=stale)

    st.divider()
    st.subheader("Wettmärkte")
    from ui.market_view import render_markets

    render_markets(details)

    with st.expander("Raw Tipico Data anzeigen", expanded=False):
        st.json(details.raw_data)


def render_event_header(
    details: EventDetails,
    *,
    metrics: RequestMetrics | None,
    stale: bool = False,
) -> None:
    """Render the shared event context above the detail tabs."""

    event = details.event
    st.subheader(f"{event.home_team} – {event.away_team}")
    st.caption(
        f"{event.competition_name} · {event.display_minute} · "
        f"{event.score_label} · Phase: {event.period}"
    )

    columns = st.columns(4)
    columns[0].metric("Tipico Event ID", event.event_id)
    columns[1].metric("Märkte", details.market_count)
    columns[2].metric("Outcomes", details.outcome_count)
    columns[3].metric("Datenalter", _age_label(_age_seconds(metrics)))

    if stale:
        st.warning("⚠ STALE – die angezeigten Eventdetails sind älter als der Grenzwert.")

    if metrics is not None:
        st.caption(
            f"Letzte Aktualisierung: "
            f"{format_local_datetime(metrics.response_received_at)} · "
            f"HTTP {metrics.status_code} · {metrics.response_time_ms} ms · "
            f"{metrics.payload_size} Bytes"
        )


def _age_label(age: float | None) -> str:
    if age is None:
        return "—"
    if age < 10:
        return f"{age:.1f} s"
    return f"{age:.0f} s"
