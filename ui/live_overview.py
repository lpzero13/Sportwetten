"""Live overview rendering. This module never parses Tipico JSON."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import streamlit as st

from models.event import LiveEvent


def _select_event(event_id: str, intent: str) -> None:
    """Persist the clicked event before Streamlit reruns the page."""

    st.session_state["selected_event_id"] = event_id
    st.session_state["detail_intent"] = intent


def _fotmob_data_available(service: Any, event_id: str) -> bool:
    """Check for a confirmed link without starting a provider request."""

    if service is None:
        return False
    try:
        if hasattr(service, "has_confirmed_link_for_tipico_event"):
            return bool(service.has_confirmed_link_for_tipico_event(event_id))
        return bool(service.has_current_state_for_tipico_event(event_id))
    except (AttributeError, KeyError, TypeError):
        # The live overview must remain usable if the optional provider store
        # is unavailable or is being migrated.
        return False


def render_live_overview(
    events: list[LiveEvent],
    *,
    selected_event_id: str | None = None,
    fotmob_service: Any | None = None,
    fotmob_live_service: Any | None = None,
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
            header = st.columns([1.0, 3.0, 1.0, 0.9, 0.9, 0.9, 1.0])
            header[0].markdown("**Zeit**")
            header[1].markdown("**Spiel**")
            header[2].markdown("**Stand**")
            header[3].markdown("**Märkte**")
            header[4].markdown("**Quoten**")
            header[5].markdown("**Analyse**")
            header[6].markdown("**FotMob**")
            for event in competition_events:
                columns = st.columns([1.0, 3.0, 1.0, 0.9, 0.9, 0.9, 1.0])
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
                link_service = fotmob_live_service or fotmob_service
                fotmob_available = _fotmob_data_available(link_service, event.event_id)
                fotmob_live_usable = bool(
                    fotmob_live_service is not None
                    and getattr(fotmob_live_service, "enabled", False)
                    and getattr(fotmob_live_service, "manual_use_allowed", False)
                )
                if columns[6].button(
                    "FotMob Live",
                    key=f"fotmob-event-{event.event_id}",
                    width="stretch",
                    type="secondary",
                    # Open the selected-match panel even without a persisted
                    # link; the panel can then validate an explicitly
                    # supplied FotMob ID without scanning other events.
                    disabled=not (fotmob_available or fotmob_live_usable),
                    help=(
                        "Öffnet die flüchtigen FotMob-Live-Daten für dieses Spiel."
                        if fotmob_available
                        else (
                            "Für dieses Event ist noch keine bestätigte Zuordnung vorhanden. "
                            "Im Livepanel kann eine FotMob-Match-ID gezielt geprüft werden."
                            if fotmob_live_usable
                            else "FotMob-Live-Daten sind aktuell nicht verfügbar."
                        )
                    ),
                    on_click=_select_event,
                    args=(event.event_id, "fotmob_live"),
                ):
                    selected = event.event_id
    return selected
