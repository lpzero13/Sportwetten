"""Streamlit rendering for the current half-time scanner."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from services.halftime_scanner import HalftimeScanItem, HalftimeScannerService


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _odds(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}".replace(".", ",")


def render_halftime_scanner(
    scanner: HalftimeScannerService,
    *,
    total_stake: float,
) -> None:
    st.title("Halftime Scanner")
    st.caption(
        "Alle aktuell als HZ erkannten Fußballspiele. Ranking nur bei frischen, "
        "vollständigen und settlement-kompatiblen Daten."
    )
    controls = st.columns([1, 2, 5])
    if controls[0].button("HZ-Scan", type="primary", width="stretch"):
        st.session_state["ht_scan_requested"] = True
    controls[1].number_input(
        "Einsatz (€)",
        min_value=1,
        max_value=1000,
        value=int(total_stake),
        step=1,
        key="ht-scanner-stake",
    )
    events = scanner.current_events()
    controls[2].write(f"Aktuelle Halbzeit-Events: {len(events)}")
    if not events:
        st.info("Aktuell meldet Tipico keine Halbzeit-Events.")
        return

    requested = bool(st.session_state.pop("ht_scan_requested", False))
    state = st.session_state.get("ht_scan_state")
    state_age = None
    if isinstance(state, dict) and state.get("scanned_at"):
        try:
            scanned_at = datetime.fromisoformat(
                str(state["scanned_at"]).replace("Z", "+00:00")
            )
            if scanned_at.tzinfo is None:
                scanned_at = scanned_at.replace(tzinfo=timezone.utc)
            state_age = (
                datetime.now(timezone.utc) - scanned_at.astimezone(timezone.utc)
            ).total_seconds()
        except ValueError:
            state_age = None
    event_ids = [event.event_id for event in events]
    if (
        requested
        or not isinstance(state, dict)
        or state.get("event_ids") != event_ids
        or state_age is None
        or state_age >= scanner.settings.max_live_odds_age_seconds
    ):
        with st.spinner("Halbzeitmärkte werden gelesen …"):
            items = scanner.scan(
                events=events,
                total_stake=float(st.session_state["ht-scanner-stake"]),
            )
        st.session_state["ht_scan_state"] = {
            "event_ids": event_ids,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }
    else:
        items = state.get("items", [])

    if not items:
        st.info("Keine analysierbaren Halbzeitdaten vorhanden.")
        return
    rankable = [item for item in items if isinstance(item, HalftimeScanItem) and item.rankable]
    incomplete = [item for item in items if item not in rankable]
    summary = st.columns(3)
    summary[0].metric("HZ-Events", len(items))
    summary[1].metric("Rankbar", len(rankable))
    summary[2].metric("Unvollständig", len(incomplete))

    rows = []
    for index, item in enumerate(rankable, start=1):
        analysis = item.analysis
        assert analysis is not None
        strategy = analysis.strategy
        event = item.event
        rows.append(
            {
                "Rang": index,
                "Spiel": f"{event.home_team} – {event.away_team}",
                "Liga": event.competition_name,
                "Land": event.competition_country or "—",
                "HZ-Stand": event.score_label,
                "Quote 0": _odds(strategy.q_zero),
                "Quote 2+": _odds(strategy.q_two_plus),
                "P(0)": _pct(analysis.probability.p0),
                "P1": _pct(analysis.probability.p1),
                "P(2+)": _pct(analysis.probability.p2_plus),
                "P1-Puffer": _pct(strategy.p1_buffer),
                "Win-ROI": _pct(strategy.win_roi),
                "Quelle": item.source,
            }
        )
    st.subheader("Transparenter Marktstruktur-Ranking")
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("Sortierung: zuerst P1-Puffer absteigend, dann Win-ROI absteigend. Tabelle ist benutzersortierbar.")
    else:
        st.info("Noch kein HZ-Event erfüllt alle Ranking-Voraussetzungen.")

    if incomplete:
        with st.expander("Nicht rankbare Halbzeit-Events", expanded=False):
            st.dataframe(
                [
                    {
                        "Spiel": f"{item.event.home_team} – {item.event.away_team}",
                        "Liga": item.event.competition_name,
                        "Land": item.event.competition_country or "—",
                        "Stand": item.event.score_label,
                        "Quelle": item.source,
                        "Status": (
                            item.error
                            or item.analysis.probability.status
                            if item.analysis
                            else item.error or "Keine Analyse"
                        ),
                    }
                    for item in incomplete
                ],
                hide_index=True,
                width="stretch",
            )
    st.info(
        "Das Ranking beschreibt Marktstruktur und ist keine Wettempfehlung. "
        "Es gibt keine Wettabgabe."
    )
