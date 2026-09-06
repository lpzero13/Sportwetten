"""First-click regression: don't let an automatic rerun hide navigation failures."""
import pytest
from streamlit.testing.v1 import AppTest


def test_scroll_helper_does_not_abort_page_rendering():
    app = AppTest.from_string('''
import streamlit as st
from ui.components import scroll_to_top
scroll_to_top()
st.write("Navigation completed")
''').run()
    assert not app.exception
    assert app.markdown[-1].value == "Navigation completed"


HARNESS = '''
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
import app as application
from ui.live_overview import render_live_overview
from ui.components import scroll_to_top
from tests.test_local_runtime_ui import example, NOW

details, analysis, intelligence = example()
st.session_state["calls"] = []
def record(name, value=None):
    st.session_state["calls"].append(name)
    return value
settings = SimpleNamespace(event_market_refresh_seconds=10, stale_detail_seconds=30,
                           persist_ui_refresh=False)
events = SimpleNamespace(events=[details.event])
market = SimpleNamespace(load_event_details=lambda *a, **k:
    record("tipico", SimpleNamespace(details=details, metrics=None, success=True, error=None)))
database = SimpleNamespace(odds_history_for_event=lambda *a: record("history", []),
                          canonical_outcomes_for_event=lambda *a, **k: record("debug", []))
real_analyze = intelligence.analyze
intelligence.analyze = lambda *a, **k: record("analysis", real_analyze(*a, **k))
provider = SimpleNamespace(enabled=True, manual_use_allowed=True,
                          has_confirmed_link_for_tipico_event=lambda *a: record("link_lookup", True))
# Only provider I/O is stubbed. Real selection, scroll, header, tabs, quotes,
# analysis, refresh and back-button code must run on the very first click.
st.session_state.setdefault("detail_auto_refresh", False)
if st.session_state.pop("navigation_scroll_top", False):
    scroll_to_top()
with patch.object(application, "st_autorefresh", lambda **kwargs: None), \
     patch.object(application, "render_fotmob_live_panel", lambda *a: record("fotmob", st.info("FotMob live fixture loaded"))), \
     patch.object(application, "render_fotmob_tab", lambda *a: record("ht", st.info("FotMob HT fixture loaded"))):
    if st.session_state.get("selected_event_id"):
        application._load_selected_detail(settings, events, market, database,
                                          intelligence, provider, provider)
    else:
        render_live_overview(events.events, fotmob_service=provider,
                             fotmob_live_service=provider)
'''


@pytest.mark.parametrize("button,first_tab", [
    ("open-event-event-1", "Alle Tipico Märkte"),
    ("analyse-event-event-1", "Analyse"),
    ("fotmob-event-event-1", "FotMob Live"),
])
def test_first_click_all_entrypoints_and_back_without_auto_refresh(button, first_tab):
    app = AppTest.from_string(HARNESS).run(timeout=20)
    assert not app.exception
    app.button(key=button).click().run(timeout=20)
    assert not app.exception
    assert app.session_state["detail-view-event-1"] == first_tab
    assert len(app.tabs) == 6
    assert any("w-scenario" in item.value for item in app.markdown) == (first_tab == "Analyse")
    assert any(item.value == "FotMob live fixture loaded" for item in app.info) == (first_tab == "FotMob Live")
    expected = {"Analyse": ["tipico", "analysis"], "Alle Tipico Märkte": ["tipico"], "FotMob Live": ["fotmob"]}
    assert app.session_state["calls"] == expected[first_tab]
    app.button(key="manual-detail-refresh-event-1").click().run(timeout=20)
    assert not app.exception
    assert app.session_state["detail-view-event-1"] == first_tab
    app.button(key="close-detail-event-1").click().run(timeout=20)
    assert not app.exception
    assert len(app.tabs) == 0
    assert app.button(key=button)


def test_reopen_same_game_uses_requested_view_and_overview_has_no_link_queries():
    app = AppTest.from_string(HARNESS).run(timeout=20)
    assert app.session_state["calls"] == []
    for intent, view in [("analyse", "Analyse"), ("open", "Alle Tipico Märkte"),
                         ("fotmob", "FotMob Live"), ("open", "Alle Tipico Märkte")]:
        app.button(key=f"{intent}-event-event-1").click().run(timeout=20)
        assert not app.exception
        assert app.session_state["detail-view-event-1"] == view
        app.button(key="close-detail-event-1").click().run(timeout=20)
        assert not app.exception


def test_old_streamlit_navigation_fallback_is_stateful_and_lazy():
    app = AppTest.from_string('''
from unittest.mock import patch
from ui.detail_navigation import render_detail_navigation
import streamlit as st
with patch("ui.detail_navigation.inspect.signature") as signature:
    signature.return_value.parameters = {}
    view, content = render_detail_navigation("example", st.session_state.pop("intent", "quotes") if "initialized" not in st.session_state else None)
    st.session_state["initialized"] = True
    with content:
        st.write("Visible: " + view)
''').run()
    assert not app.exception
    assert app.radio[0].value == "Alle Tipico Märkte"
    app.radio[0].set_value("FotMob Live").run()
    assert not app.exception
    assert app.markdown[-1].value == "Visible: FotMob Live"
