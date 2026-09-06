"""Stable, stateful detail navigation with lazy content on old/new Streamlit."""
import inspect

import streamlit as st


VIEWS = ("Analyse", "Alle Tipico Märkte", "FotMob Live", "FotMob HT", "Odds History", "Debug")
INTENT_VIEWS = {"analysis": VIEWS[0], "quotes": VIEWS[1], "fotmob_live": VIEWS[2], "fotmob": VIEWS[3]}


def render_detail_navigation(event_id: str, intent: str | None = None):
    key = f"detail-view-{event_id}"
    if intent in INTENT_VIEWS:
        st.session_state[key] = INTENT_VIEWS[intent]
    elif st.session_state.get(key) not in VIEWS:
        st.session_state[key] = VIEWS[0]
    # Older container installations lack stateful tabs. Their radio fallback
    # has the same explicit selection and never computes hidden panels either.
    if "on_change" in inspect.signature(st.tabs).parameters:
        tabs = st.tabs(VIEWS, key=key, on_change="rerun")
        selected = st.session_state[key]
        return selected, tabs[VIEWS.index(selected)]
    selected = st.radio("Spielansicht", VIEWS, key=key, horizontal=True)
    return selected, st.container()
