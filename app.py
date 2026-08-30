"""Tipico Live Observer V0.1 – local Streamlit entry point."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import Settings, configure_logging
from fotmob.service import FotMobService
from intelligence.service import MarketIntelligenceService
from paper.service import PaperTradingService
from services.event_service import EventService
from services.halftime_scanner import HalftimeScannerService
from services.market_service import MarketService
from services.upcoming_service import UpcomingService
from storage.database import Database
from storage.raw_storage import RawStorage
from tipico.client import TipicoClient
from ui.debug import render_debug_page
from ui.data_collection import render_data_collection
from ui.device import apply_responsive_style, detect_device
from ui.event_detail import render_event_header
from ui.analysis import render_market_analysis
from ui.halftime_scanner import render_halftime_scanner
from ui.live_overview import render_live_overview
from ui.time_format import current_munich_time, format_local_datetime, parse_datetime
from ui.upcoming import render_upcoming
from ui.paper_trading import render_paper_trading
from ui.fotmob import render_fotmob_debug, render_fotmob_tab


@st.cache_resource(show_spinner=False)
def get_runtime(root_dir: str) -> tuple[
    Settings,
    EventService,
    MarketService,
    UpcomingService,
    Database,
    MarketIntelligenceService,
    PaperTradingService,
    FotMobService,
]:
    settings = Settings.from_env(Path(root_dir))
    logger = configure_logging(settings)
    client = TipicoClient(settings, logger=logger)
    database = Database(settings.database_path)
    raw_storage = RawStorage(
        settings.raw_storage_path,
        # The store is enabled as a sink, but callers decide explicitly when
        # to write. This keeps parser-error/debug payloads available without
        # reintroducing per-refresh persistence.
        enabled=True,
        compression=settings.raw_compression,
    )
    event_service = EventService(
        client,
        database,
        raw_storage,
        settings,
        logger=logger,
    )
    market_service = MarketService(
        client,
        database,
        raw_storage,
        settings,
        logger=logger,
    )
    upcoming_service = UpcomingService(
        client,
        database,
        settings,
        logger=logger,
    )
    intelligence_service = MarketIntelligenceService(
        database,
        settings,
        logger=logger,
    )
    paper_service = PaperTradingService(database, settings, logger=logger)
    fotmob_service = FotMobService(settings, database, logger=logger)
    return (
        settings,
        event_service,
        market_service,
        upcoming_service,
        database,
        intelligence_service,
        paper_service,
        fotmob_service,
    )


def _format_age(age: float | None) -> str:
    if age is None:
        return "—"
    return f"{age:.1f} s"


def _refresh_warning(event_service: EventService) -> None:
    if event_service.last_error:
        age = _format_age(event_service.data_age_seconds)
        st.warning(
            "⚠ Tipico aktuell nicht erreichbar. "
            f"Letzter erfolgreicher Abruf: "
            f"{format_local_datetime(event_service.last_success_at)}; "
            f"angezeigte Daten sind {age} alt."
        )
    elif event_service.is_stale:
        st.warning(
            f"⚠ STALE – angezeigte Übersicht ist "
            f"{_format_age(event_service.data_age_seconds)} alt."
        )


def _load_selected_detail(
    settings: Settings,
    event_service: EventService,
    market_service: MarketService,
    database: Database,
    intelligence_service: MarketIntelligenceService,
    fotmob_service: FotMobService,
) -> None:
    selected_id = st.session_state.get("selected_event_id")
    if not selected_id:
        return

    st.divider()
    st.subheader("Eventdetails")
    control_columns = st.columns([1, 1, 5])
    auto_refresh = control_columns[0].checkbox(
        "Auto Refresh",
        value=False,
        key="detail_auto_refresh",
        help=f"Detailfeed alle {settings.event_market_refresh_seconds} Sekunden abrufen.",
    )
    manual_refresh = control_columns[1].button(
        "Quoten aktualisieren",
        key=f"manual-detail-refresh-{selected_id}",
        width="stretch",
        help="Fragt die aktuellen Märkte und Quoten für dieses Event sofort neu ab.",
    )
    if control_columns[2].button(
        "Event schließen",
        key=f"close-detail-{selected_id}",
    ):
        st.session_state.pop("selected_event_id", None)
        st.session_state.pop("detail_state", None)
        st.session_state.pop("detail_intent", None)
        st.rerun()

    if auto_refresh:
        st_autorefresh(
            interval=settings.event_market_refresh_seconds * 1000,
            key=f"detail-autorefresh-{selected_id}",
        )

    detail_state = st.session_state.get("detail_state")
    loaded_at = None
    if isinstance(detail_state, dict):
        loaded_at = detail_state.get("loaded_at")
    should_load = (
        not isinstance(detail_state, dict)
        or detail_state.get("event_id") != selected_id
        or manual_refresh
    )
    if auto_refresh and loaded_at:
        try:
            loaded_dt = datetime.fromisoformat(loaded_at.replace("Z", "+00:00"))
            should_load = should_load or (
                datetime.now(timezone.utc) - loaded_dt.astimezone(timezone.utc)
            ).total_seconds() >= settings.event_market_refresh_seconds
        except ValueError:
            should_load = True

    overview_event = next(
        (event for event in event_service.events if event.event_id == selected_id),
        None,
    )
    if should_load:
        result = market_service.load_event_details(
            selected_id,
            overview_event=overview_event,
        )
        if result.details is not None:
            st.session_state.detail_state = {
                "event_id": selected_id,
                "details": result.details,
                "metrics": result.metrics,
                "loaded_at": (
                    result.metrics.response_received_at
                    if result.metrics
                    else datetime.now(timezone.utc).isoformat()
                ),
                "error": result.error,
            }
        if manual_refresh and result.success and result.metrics is not None and result.details is not None:
            st.success(
                f"Quoten aktualisiert: "
                f"{format_local_datetime(result.metrics.response_received_at)} · "
                f"{result.details.market_count} Märkte / {result.details.outcome_count} Outcomes"
            )
        elif result.error:
            st.error(f"Quoten konnten nicht aktualisiert werden: {result.error}")

    detail_state = st.session_state.get("detail_state")
    if not isinstance(detail_state, dict) or detail_state.get("event_id") != selected_id:
        st.info("Eventdetails werden geladen …")
        return

    if detail_state.get("error"):
        st.warning(f"Letzter Detailabruf fehlgeschlagen: {detail_state['error']}")
    details = detail_state["details"]
    render_event_header(
        details,
        metrics=detail_state.get("metrics"),
        stale=_detail_is_stale(detail_state, settings),
    )
    # Der Intent wird nur für den initialen Tab nach dem Klick verwendet. So
    # bleibt ein späteres Widget-Rerun innerhalb der Analyse stabil und setzt
    # nicht versehentlich wieder den Quoten-Tab zurück.
    opening_intent = st.session_state.pop("detail_intent", None)
    if opening_intent:
        st.caption(
            "Ansicht geöffnet über: "
            + ("Analyse" if opening_intent == "analysis" else "Quoten")
        )

    if opening_intent == "quotes":
        quotes_tab, analysis_tab, fotmob_tab, history_tab, raw_tab = st.tabs(
            ["Alle Tipico Märkte", "Analyse", "FotMob", "Odds History", "Raw / Debug"]
        )
    else:
        analysis_tab, fotmob_tab, quotes_tab, history_tab, raw_tab = st.tabs(
            ["Analyse", "FotMob", "Alle Tipico Märkte", "Odds History", "Raw / Debug"]
        )

    with analysis_tab:
        analysis = detail_state.get("analysis")
        if analysis is None:
            analysis = intelligence_service.analyze(
                details,
                observed_at=str(detail_state.get("loaded_at") or datetime.now(timezone.utc).isoformat()),
                persist=settings.persist_ui_refresh,
            )
            detail_state["analysis"] = analysis
            st.session_state.detail_state = detail_state
        render_market_analysis(details, analysis, intelligence_service)
    with quotes_tab:
        from ui.market_view import render_markets

        render_markets(details)
    with fotmob_tab:
        render_fotmob_tab(fotmob_service, details.event)
    with history_tab:
        history = database.odds_history_for_event(selected_id)
        if history:
            st.dataframe(
                [
                    {
                        "Zeit": format_local_datetime(row["observed_at"]),
                        "Markt": row["market_caption"] or row["market_id"],
                        "Type": row["market_type"] or "—",
                        "Auswahl": row["outcome_caption"] or row["outcome_id"],
                        "Quote": (
                            f"{row['odds']:.2f}" if row["odds"] is not None else "—"
                        ),
                        "Status": row["status"],
                        "Verfügbar": bool(row["available"]),
                        "Snapshot": str(row["snapshot_id"]) if row["snapshot_id"] else "—",
                    }
                    for row in history
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("Für dieses Event gibt es noch keine Odds-History.")
    with raw_tab:
        st.caption(
            f"Raw-Payload bleibt unverändert. {details.market_count} Märkte / "
            f"{details.outcome_count} Outcomes."
        )
        with st.expander("Raw Tipico Data", expanded=False):
            st.json(details.raw_data)
        canonical = database.canonical_outcomes_for_event(selected_id, limit=300)
        if canonical:
            st.subheader("Canonical Outcomes")
            st.dataframe(
                [
                    {
                        "Zeit": format_local_datetime(row["observed_at"]),
                        "Type": row["canonical_type"],
                        "Scope": row["scope"],
                        "Period": row["period"],
                        "Side": row["side"] or "—",
                        "Line": (
                            f"{row['line']:g}" if row["line"] is not None else "—"
                        ),
                        "Quote": (
                            f"{row['odds']:.2f}" if row["odds"] is not None else "—"
                        ),
                        "Status": row["status"],
                        "Raw Type": row["raw_market_type"],
                    }
                    for row in canonical
                ],
                hide_index=True,
                width="stretch",
            )


def _detail_is_stale(detail_state: dict, settings: Settings) -> bool:
    loaded_at = detail_state.get("loaded_at")
    if not loaded_at:
        return False
    try:
        loaded = parse_datetime(loaded_at)
        if loaded is None:
            return True
    except (TypeError, ValueError):
        return True
    return (
        datetime.now(timezone.utc) - loaded.astimezone(timezone.utc)
    ).total_seconds() > settings.stale_detail_seconds


def main() -> None:
    st.set_page_config(
        page_title="Tipico Live Observer",
        page_icon="⚽",
        layout="wide",
    )
    apply_responsive_style()
    device = detect_device()
    root_dir = Path(__file__).resolve().parent
    (
        settings,
        event_service,
        market_service,
        upcoming_service,
        database,
        intelligence_service,
        paper_service,
        fotmob_service,
    ) = get_runtime(str(root_dir))

    st.sidebar.title("Tipico Live Observer")
    page = st.sidebar.radio(
        "Ansicht",
        ["Live", "Upcoming", "Halftime Scanner", "Paper Trading", "Data / Debug"],
    )
    st.sidebar.caption("V0.5 Dashboard · Paper Trading · REST/Polling")
    st.sidebar.caption(f"Gerät: {device.label}")
    st.sidebar.caption(f"Münchner Zeit: {current_munich_time()}")

    if page == "Upcoming":
        st_autorefresh(interval=60_000, key="upcoming-autorefresh")
        render_upcoming(upcoming_service, market_service)
        return

    if page == "Halftime Scanner":
        st_autorefresh(interval=10_000, key="halftime-autorefresh")
        event_service.refresh_if_due()
        scanner = HalftimeScannerService(
            event_service,
            market_service,
            intelligence_service,
            database,
            settings,
        )
        render_halftime_scanner(
            scanner,
            total_stake=float(settings.default_total_stake_eur),
            fotmob_service=fotmob_service,
        )
        return

    if page == "Paper Trading":
        st_autorefresh(interval=30_000, key="paper-autorefresh")
        render_paper_trading(paper_service, database, mobile=device.is_mobile)
        return

    if page == "Data / Debug":
        st_autorefresh(interval=30_000, key="collection-autorefresh")
        section = st.radio(
            "Bereich",
            ["Data Collection", "Debug / System", "FotMob"],
            horizontal=True,
        )
        if section == "Data Collection":
            render_data_collection(database, settings, fotmob_service=fotmob_service)
        elif section == "FotMob":
            render_fotmob_debug(fotmob_service)
        else:
            event_service.refresh_if_due()
            render_debug_page(
                event_service,
                market_service,
                database,
                database_path=settings.database_path,
                raw_storage_enabled=settings.store_raw_responses,
                fotmob_service=fotmob_service,
            )
        return

    st_autorefresh(
        interval=settings.live_event_refresh_seconds * 1000,
        key="live-overview-autorefresh",
    )

    st.title("Tipico Live Football")
    force_refresh = st.button("Jetzt aktualisieren", type="primary")
    result = event_service.refresh() if force_refresh else event_service.refresh_if_due()
    _refresh_warning(event_service)

    overview_metrics = st.columns(4)
    overview_metrics[0].metric("Live-Spiele", len(event_service.events))
    overview_metrics[1].metric(
        "Wettbewerbe",
        len({(event.competition_name, event.competition_country) for event in event_service.events}),
    )
    overview_metrics[2].metric(
        "Letztes Update",
        format_local_datetime(event_service.last_success_at),
    )
    overview_metrics[3].metric(
        "API",
        "ONLINE"
        if event_service.last_error is None and event_service.last_metrics is not None
        else "FEHLER / UNBEKANNT",
    )

    search = st.text_input(
        "Suche Liga oder Team",
        placeholder="z. B. Bayern, Bundesliga oder U19",
    )
    events = event_service.filtered_events(search)
    st.write(f"Live-Spiele: {len(events)}")

    # Die Detailansicht steht bewusst vor der langen Wettbewerbsliste. Der
    # Button-Callback schreibt die Auswahl vor dem Streamlit-Rerun in den
    # Session-State; dadurch ist die Reaktion nach dem Klick sofort sichtbar.
    _load_selected_detail(
        settings,
        event_service,
        market_service,
        database,
        intelligence_service,
        fotmob_service,
    )

    selected = render_live_overview(
        events,
        selected_event_id=st.session_state.get("selected_event_id"),
    )
    if selected:
        st.session_state.selected_event_id = selected


if __name__ == "__main__":
    main()
