"""Upcoming event and optional pre-match quote view."""

from __future__ import annotations

import streamlit as st

from models.event import LiveEvent
from services.market_service import MarketService
from services.upcoming_service import UpcomingService
from ui.market_view import render_markets
from ui.time_format import format_local_datetime


def _kickoff(event: LiveEvent) -> str:
    if not event.kickoff_time:
        return "—"
    return format_local_datetime(
        event.kickoff_time,
        include_seconds=False,
        fallback=event.kickoff_time,
    )


def render_upcoming(
    upcoming_service: UpcomingService,
    market_service: MarketService,
) -> None:
    st.title("Upcoming")
    st.caption("Kommende Fußballspiele aus dem Tipico-Feed; ohne Content-Filter.")
    if st.button("Upcoming aktualisieren", type="primary"):
        upcoming_service.refresh()
    else:
        upcoming_service.refresh_if_due()
    if upcoming_service.last_error:
        st.warning(upcoming_service.last_error)
    events = sorted(
        upcoming_service.events,
        key=lambda event: (event.kickoff_time or "", event.competition_name.casefold()),
    )
    st.metric("Kommende Fußballspiele", len(events))
    if not events:
        st.info("Keine kommenden Fußballspiele im aktuellen Feed.")
        return

    rows = []
    for event in events:
        rows.append(
            {
                "Start": _kickoff(event),
                "Liga": event.competition_name,
                "Land": event.competition_country or "—",
                "Spiel": f"{event.home_team} – {event.away_team}",
                "Market Count": (
                    str(event.bet_markets_count)
                    if event.bet_markets_count is not None
                    else "—"
                ),
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
    st.subheader("Pre-Match Quoten")
    event_options = [event.event_id for event in events]
    labels = {
        event.event_id: (
            f"{event.home_team} – {event.away_team} · {event.competition_name} · "
            f"{event.competition_country or 'Land unbekannt'}"
        )
        for event in events
    }
    selected_id = st.selectbox(
        "Event für Detailansicht",
        event_options,
        format_func=lambda value: labels.get(value, value),
    )
    if st.button("Pre-Match Quoten laden", key=f"upcoming-detail-{selected_id}"):
        event = next(event for event in events if event.event_id == selected_id)
        result = market_service.load_event_details(selected_id, overview_event=event)
        if result.details is not None:
            st.session_state["upcoming_detail"] = {
                "event_id": selected_id,
                "details": result.details,
                "metrics": result.metrics,
                "error": result.error,
            }
        elif result.error:
            st.error(result.error)
    detail = st.session_state.get("upcoming_detail")
    if isinstance(detail, dict) and detail.get("event_id") == selected_id:
        details = detail["details"]
        st.caption(
            f"{details.event.home_team} – {details.event.away_team} · "
            f"{details.market_count} Märkte · "
            f"letzte Antwort "
            f"{format_local_datetime(detail['metrics'].response_received_at) if detail.get('metrics') else '—'}"
        )
        render_markets(details)
