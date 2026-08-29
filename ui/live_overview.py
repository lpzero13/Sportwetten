"""Live overview rendering. This module never parses Tipico JSON."""

from __future__ import annotations

from collections import defaultdict

import streamlit as st

from models.event import LiveEvent


def _select_event(event_id: str, intent: str) -> None:
    """Persist the clicked event before Streamlit reruns the page."""

    st.session_state["selected_event_id"] = event_id
    st.session_state["detail_intent"] = intent


def render_live_overview(
    events: list[LiveEvent],
    *,
    selected_event_id: str | None = None,
) -> str | None:
    """Render every supplied event grouped by Tipico competition."""

    if not events:
        st.info("Keine Live-Fußballspiele im aktuellen Tipico-Feed.")
        return None

    grouped: dict[tuple[str, str], list[LiveEvent]] = defaultdict(list)
    for event in events:
        grouped[
            (
                event.competition_name or "Unbekannter Wettbewerb",
                event.competition_country or "Land unbekannt",
            )
        ].append(event)

    selected = selected_event_id
    st.caption("Alle vom Tipico-Livefeed gelieferten Fußball-Events werden angezeigt.")
    for competition_name, country in sorted(grouped, key=lambda item: (item[0].casefold(), item[1].casefold())):
        competition_events = grouped[(competition_name, country)]
        with st.expander(
            f"{competition_name} · {country} ({len(competition_events)})",
            expanded=False,
        ):
            header = st.columns([1.0, 3.0, 1.0, 0.9, 0.9, 1.2])
            header[0].markdown("**Zeit**")
            header[1].markdown("**Spiel**")
            header[2].markdown("**Stand**")
            header[3].markdown("**Märkte**")
            header[4].markdown("**Quoten**")
            header[5].markdown("**Analyse**")
            for event in competition_events:
                columns = st.columns([1.0, 3.0, 1.0, 0.9, 0.9, 1.2])
                columns[0].write(event.display_minute)
                columns[1].write(f"{event.home_team} – {event.away_team}")
                columns[2].write(event.score_label)
                columns[3].write(
                    event.bet_markets_count
                    if event.bet_markets_count is not None
                    else "—"
                )
                if columns[4].button(
                    "Quoten",
                    key=f"open-event-{event.event_id}",
                    width="stretch",
                    type="secondary",
                    on_click=_select_event,
                    args=(event.event_id, "quotes"),
                ):
                    selected = event.event_id
                if columns[5].button(
                    "Analyse",
                    key=f"analyse-event-{event.event_id}",
                    width="stretch",
                    type="primary",
                    on_click=_select_event,
                    args=(event.event_id, "analysis"),
                ):
                    selected = event.event_id
    return selected
