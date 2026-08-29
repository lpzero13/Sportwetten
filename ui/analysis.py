"""Read-only rendering of the V0.3 deterministic market analysis."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from intelligence.models import MarketAnalysis, StrategyResult
from intelligence.rescue import calculate_rescue_profile
from intelligence.service import MarketIntelligenceService
from models.market import EventDetails
from ui.time_format import format_local_datetime, parse_datetime


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _odds(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}".replace(".", ",")


def _eur(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} €".replace(".", ",")


def _age(observed_at: str) -> str:
    try:
        moment = parse_datetime(observed_at)
        if moment is None:
            return "—"
        seconds = max(
            0.0,
            (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds(),
        )
        return f"{seconds:.1f} s"
    except ValueError:
        return "—"


def _best_row(label: str, best: object | None) -> dict[str, str]:
    selected = getattr(best, "selected", None)
    if selected is None:
        return {
            "Ziel": label,
            "Beste Quote": "—",
            "Quelle": "—",
            "Alternativen": "—",
            "Status": getattr(best, "status", "nicht vorhanden") if best else "nicht vorhanden",
        }
    alternatives = getattr(best, "alternatives", [])
    alt_text = ", ".join(_odds(item.odds) for item in alternatives[:5]) or "—"
    return {
        "Ziel": label,
        "Beste Quote": _odds(selected.odds),
        "Quelle": selected.source_label,
        "Alternativen": alt_text,
        "Status": getattr(best, "status", "OK"),
    }


def _scenario_rows(strategy: StrategyResult) -> list[dict[str, str]]:
    return [
        {
            "Szenario": "0 verbleibende Tore",
            "Einsatz": _eur(strategy.stake_zero),
            "Quote": _odds(strategy.q_zero),
            "Auszahlung": _eur(strategy.payout_zero),
            "Netto P/L": _eur(
                strategy.payout_zero - strategy.total_stake
                if strategy.payout_zero is not None
                else None
            ),
        },
        {
            "Szenario": "exakt 1 verbleibendes Tor",
            "Einsatz": "—",
            "Quote": "—",
            "Auszahlung": "0,00 €",
            "Netto P/L": _eur(strategy.loss_exact_one),
        },
        {
            "Szenario": "2+ verbleibende Tore",
            "Einsatz": _eur(strategy.stake_two_plus),
            "Quote": _odds(strategy.q_two_plus),
            "Auszahlung": _eur(strategy.payout_two_plus),
            "Netto P/L": _eur(
                strategy.payout_two_plus - strategy.total_stake
                if strategy.payout_two_plus is not None
                else None
            ),
        },
    ]


def _one_second_half_goal(event: object) -> bool:
    score_home = getattr(event, "score_home", None)
    score_away = getattr(event, "score_away", None)
    ht_home = getattr(event, "ht_score_home", None)
    ht_away = getattr(event, "ht_score_away", None)
    if None in {score_home, score_away, ht_home, ht_away}:
        return False
    current = int(score_home) + int(score_away)
    halftime = int(ht_home) + int(ht_away)
    period = str(getattr(event, "period", "")).upper()
    return current - halftime == 1 and period not in {"HALF_TIME", "HALFTIME", "HT"}


def _render_rescue(
    details: EventDetails,
    analysis: MarketAnalysis,
) -> None:
    """Render the optional post-one-goal Schadensprofil."""

    selected = (
        analysis.zero_equivalence.best_odds.selected
        if analysis.zero_equivalence.best_odds
        else None
    )
    if selected is None or selected.odds is None:
        st.warning("Rescue nicht berechenbar: keine frische offene No-More-Goal-Quote.")
        return
    event_id = str(details.event.event_id)
    strategy = analysis.strategy
    defaults = {
        "total": max(0.0, float(strategy.total_stake or 30)),
        "zero_stake": max(0.0, float(strategy.stake_zero or 0)),
        "zero_odds": max(1.01, float(strategy.q_zero or 2)),
        "two_stake": max(0.0, float(strategy.stake_two_plus or 0)),
        "two_odds": max(1.01, float(strategy.q_two_plus or 2)),
    }
    with st.expander("Optional: Dynamic Middle Rescue · Schadensprofil", expanded=False):
        st.caption(
            "Voraussetzung: exakt ein HZ2-Tor. Die Ansicht rechnet Szenarien "
            "für eine hypothetische bestehende Position; sie ist keine Hedge-Empfehlung."
        )
        input_columns = st.columns(5)
        total = input_columns[0].number_input(
            "Original gesamt (€)",
            min_value=0.0,
            value=defaults["total"],
            step=1.0,
            key=f"rescue-total-{event_id}",
        )
        zero_stake = input_columns[1].number_input(
            "Original 0-Tore (€)",
            min_value=0.0,
            value=defaults["zero_stake"],
            step=0.01,
            key=f"rescue-zero-stake-{event_id}",
        )
        zero_odds = input_columns[2].number_input(
            "Original q0",
            min_value=1.01,
            value=defaults["zero_odds"],
            step=0.01,
            key=f"rescue-zero-odds-{event_id}",
        )
        two_stake = input_columns[3].number_input(
            "Original 2+ (€)",
            min_value=0.0,
            value=defaults["two_stake"],
            step=0.01,
            key=f"rescue-two-stake-{event_id}",
        )
        two_odds = input_columns[4].number_input(
            "Original q2+",
            min_value=1.01,
            value=defaults["two_odds"],
            step=0.01,
            key=f"rescue-two-odds-{event_id}",
        )
        st.caption(
            f"Aktuelle No-More-Goal-Quelle: {selected.source_label} · "
            f"Quote {_odds(selected.odds)}"
        )
        hedge_key = f"rescue-hedge-{event_id}"
        if hedge_key not in st.session_state:
            st.session_state[hedge_key] = 0.0
        zero_profile = calculate_rescue_profile(
            original_total_stake=float(total),
            original_zero_stake=float(zero_stake),
            original_zero_odds=float(zero_odds),
            original_two_plus_stake=float(two_stake),
            original_two_plus_odds=float(two_odds),
            hedge_odds=float(selected.odds),
            hedge_stake=0.0,
        )
        button_columns = st.columns([1, 4])
        if button_columns[0].button(
            "Verlust gleichstellen",
            key=f"rescue-equalize-{event_id}",
            help="Setzt den Slider auf die mathematisch ausgleichende Hedge-Höhe, soweit möglich.",
        ):
            st.session_state[hedge_key] = round(
                max(0.0, zero_profile.equalizing_hedge_stake or 0.0),
                2,
            )
        button_columns[1].caption(
            "Die Rechnung berücksichtigt keine neuen Wettlimits, Steuer oder Empfehlung."
        )
        hedge_max = max(25.0, float(total))
        hedge = st.slider(
            "Hedge-Einsatz (€)",
            min_value=0.0,
            max_value=hedge_max,
            step=0.01,
            key=hedge_key,
        )
        profile = calculate_rescue_profile(
            original_total_stake=float(total),
            original_zero_stake=float(zero_stake),
            original_zero_odds=float(zero_odds),
            original_two_plus_stake=float(two_stake),
            original_two_plus_odds=float(two_odds),
            hedge_odds=float(selected.odds),
            hedge_stake=float(hedge),
        )
        st.dataframe(
            [
                {
                    "Hedge": f"{hedge:.2f} €",
                    "Kein weiteres Tor": _eur(profile.pnl_no_more_goal),
                    "Weiteres Tor": _eur(profile.pnl_another_goal),
                    "No-More-Goal-Quote": _odds(profile.hedge_odds),
                    "Status": profile.status,
                }
            ],
            hide_index=True,
            width="stretch",
        )
        if profile.equalizing_hedge_stake is not None:
            st.caption(
                f"Mathematisch gleichstellender Hedge: "
                f"{profile.equalizing_hedge_stake:.2f} € · "
                f"beide Szenarien dann ca. {_eur(profile.equalized_pnl)}"
            )
        else:
            st.warning("Kein nicht-negativer Hedge kann die beiden Szenarien gleichstellen.")


def render_market_analysis(
    details: EventDetails,
    analysis: MarketAnalysis,
    intelligence_service: MarketIntelligenceService,
) -> None:
    """Render analysis and allow a local, non-persistent stake scenario."""

    event_id = str(details.event.event_id)
    stake = st.slider(
        "Szenario-Einsatz (€)",
        min_value=1,
        max_value=1000,
        value=int(round(analysis.strategy.total_stake or 30)),
        step=1,
        key=f"analysis-stake-{event_id}",
        help="Ändert nur die Anzeige der Einsatzverteilung; es wird keine Wette abgegeben.",
    )
    if float(stake) != analysis.strategy.total_stake:
        analysis = intelligence_service.analyze(
            details,
            observed_at=analysis.observed_at,
            snapshot_id=analysis.snapshot_id,
            total_stake=float(stake),
            persist=False,
        )

    probability = analysis.probability
    strategy = analysis.strategy
    selected_zero = (
        analysis.zero_equivalence.best_odds.selected
        if analysis.zero_equivalence.best_odds
        else None
    )
    selected_two = (
        analysis.two_plus_equivalence.best_odds.selected
        if analysis.two_plus_equivalence.best_odds
        else None
    )
    metrics = st.columns(5)
    metrics[0].metric("Quote 0", _odds(selected_zero.odds if selected_zero else None))
    metrics[1].metric("Quote 2+", _odds(selected_two.odds if selected_two else None))
    metrics[2].metric("P(0)", _pct(probability.p0))
    metrics[3].metric("P(exakt 1)", _pct(probability.p1))
    metrics[4].metric("P(2+)", _pct(probability.p2_plus))

    st.caption(
        f"Beobachtet: {format_local_datetime(analysis.observed_at)} · "
        f"Datenalter: {_age(analysis.observed_at)} · "
        f"Normalizer: v0.3.1"
    )

    st.subheader("Äquivalente Zielmärkte")
    st.dataframe(
        [
            _best_row("0 verbleibende Tore", analysis.zero_equivalence.best_odds),
            _best_row("2+ verbleibende Tore", analysis.two_plus_equivalence.best_odds),
        ],
        hide_index=True,
        width="stretch",
    )
    for market in (analysis.zero_equivalence, analysis.two_plus_equivalence):
        if market.status != "EQUIVALENT":
            st.warning(f"{market.label}: {market.status} · {market.explanation}")
        else:
            st.caption(f"{market.label}: {market.explanation}")
    provenance = []
    for label, market in (
        ("0 verbleibende Tore", analysis.zero_equivalence),
        ("2+ verbleibende Tore", analysis.two_plus_equivalence),
    ):
        for candidate in market.candidates:
            provenance.append(
                {
                    "Ziel": label,
                    "Quote": _odds(candidate.odds),
                    "Market ID": candidate.market_id,
                    "Outcome ID": candidate.outcome_id,
                    "Observed at": format_local_datetime(candidate.observed_at),
                    "Age": _age(candidate.observed_at),
                    "Status": candidate.status,
                    "Verfügbar": "OPEN" if candidate.available else "NO",
                }
            )
    if provenance:
        with st.expander("Quote-Provenienz / Mapping-Details", expanded=False):
            st.dataframe(provenance, hide_index=True, width="stretch")

    st.subheader("Tipico-Wahrscheinlichkeitsverteilung")
    if probability.status == "OK":
        st.dataframe(
            [
                {"Bucket": "0", "Wahrscheinlichkeit": _pct(probability.p0)},
                {"Bucket": "exakt 1", "Wahrscheinlichkeit": _pct(probability.p1)},
                {"Bucket": "2+", "Wahrscheinlichkeit": _pct(probability.p2_plus)},
            ],
            hide_index=True,
            width="stretch",
        )
        st.caption(f"Quelle: {probability.source or '—'} · Summe: 100,0%")
    else:
        st.warning(f"Wahrscheinlichkeit nicht rankbar: {probability.status}")
        if probability.source:
            st.caption(f"Teilquelle: {probability.source}")

    st.subheader("Strategie ZERO_OR_2PLUS")
    strategy_columns = st.columns(5)
    strategy_columns[0].metric("Status", strategy.status)
    strategy_columns[1].metric("P1-Maximum", _pct(strategy.p1_max))
    strategy_columns[2].metric("P1 Tipico", _pct(strategy.p1_tipico))
    strategy_columns[3].metric("Struktureller Puffer", _pct(strategy.p1_buffer))
    strategy_columns[4].metric("Win-ROI", _pct(strategy.win_roi))
    st.caption(
        f"Label: {strategy.label} · Gesamt-Einsatz: {_eur(strategy.total_stake)} · "
        "P1-Maximum ist kein eigener Edge-Schätzer."
    )
    st.dataframe(_scenario_rows(strategy), hide_index=True, width="stretch")
    if strategy.payout_difference is not None:
        st.caption(
            f"Auszahlungsdifferenz nach Cent-Rundung: {_eur(strategy.payout_difference)}"
        )
    if strategy.status != "OK":
        st.warning("Kein positiver gedeckter Auszahlungspuffer oder unvollständige Quoten.")
    elif probability.status != "OK":
        st.warning("Die Strategie wird wegen der nicht validen Tipico-Verteilung nicht gerankt.")

    if analysis.warnings:
        with st.expander("Datenqualität / Hinweise", expanded=False):
            for warning in analysis.warnings:
                st.write(f"• {warning}")
    st.info(
        "Read-only Analyse: keine Wettempfehlung, keine eigene ML-Wahrscheinlichkeit "
        "und keine Wettabgabe."
    )
    if _one_second_half_goal(details.event):
        _render_rescue(details, analysis)
