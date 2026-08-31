"""Streamlit rendering for the current half-time scanner."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from fotmob.service import FotMobService
from services.halftime_scanner import HalftimeScanItem, HalftimeScannerService


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _odds(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}".replace(".", ",")


def render_halftime_scanner(
    scanner: HalftimeScannerService,
    *,
    total_stake: float,
    fotmob_service: FotMobService | None = None,
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
    scan_started = (
        requested
        or not isinstance(state, dict)
        or state.get("event_ids") != event_ids
        or state_age is None
        or state_age >= scanner.settings.max_live_odds_age_seconds
    )
    if scan_started:
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

    fotmob_errors: list[str] = []
    if (
        fotmob_service is not None
        and fotmob_service.automated_worker_allowed
        and scan_started
    ):
        # This is the explicit Tipico-HZ -> FotMob coordination point.  It is
        # only active for an explicitly production-approved provider policy,
        # refreshes confirmed links and writes the idempotent HALFTIME slot;
        # the Tipico ranking below is not recalculated from these stats.
        for event in events:
            resolved = fotmob_service.resolver.resolve(event)
            if resolved.match_result.status not in {"EXACT", "HIGH_CONFIDENCE", "MANUALLY_CONFIRMED"}:
                continue
            result = fotmob_service.refresh_for_tipico_event(event, snapshot_type="HALFTIME")
            if not result.success and result.error:
                fotmob_errors.append(f"{event.home_team} – {event.away_team}: {result.error}")

    if not items:
        st.info("Keine analysierbaren Halbzeitdaten vorhanden.")
        return
    if fotmob_errors:
        st.caption("FotMob-HZ-Enrichment: " + " · ".join(fotmob_errors[:3]))
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
        fotmob_state = (
            fotmob_service.current_for_tipico_event(event)
            if fotmob_service is not None and fotmob_service.enabled
            else None
        )
        fotmob_stats = {}
        if fotmob_state is not None:
            try:
                import json

                fotmob_stats = json.loads(str(fotmob_state["ht_stats_json"]))
            except (KeyError, TypeError, ValueError):
                fotmob_stats = {}
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
                **(
                    {
                        "FotMob xG": (
                            f"{fotmob_stats.get('xg_home'):.2f} : {fotmob_stats.get('xg_away'):.2f}"
                            if fotmob_stats.get("xg_home") is not None and fotmob_stats.get("xg_away") is not None
                            else "—"
                        ),
                        "FotMob SOT": (
                            f"{fotmob_stats.get('shots_on_target_home'):g} : {fotmob_stats.get('shots_on_target_away'):g}"
                            if fotmob_stats.get("shots_on_target_home") is not None and fotmob_stats.get("shots_on_target_away") is not None
                            else "—"
                        ),
                        "FotMob State": fotmob_state["status"] if fotmob_state is not None else "—",
                    }
                    if fotmob_service is not None and fotmob_service.enabled
                    else {}
                ),
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
