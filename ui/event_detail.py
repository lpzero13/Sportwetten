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
    from ui.components import text
    st.markdown(
        '<section class="w-match"><div>'
        f'<p class="w-eyebrow">{text(event.competition_country or "Land unbekannt")} · {text(event.competition_name)}</p>'
        f'<h2>{text(event.home_team)} – {text(event.away_team)}</h2>'
        f'<p>{details.market_count} Märkte · {details.outcome_count} Auswahlen · Tipico</p></div>'
        f'<div class="w-score"><strong>{text(event.score_label)}</strong><small>{text(event.display_minute)} · {text(event.period)}</small></div></section>',
        unsafe_allow_html=True,
    )

    if stale:
        st.warning("⚠ STALE – die angezeigten Eventdetails sind älter als der Grenzwert.")

def _age_label(age: float | None) -> str:
    if age is None:
        return "—"
    if age < 10:
        return f"{age:.1f} s"
    return f"{age:.0f} s"
