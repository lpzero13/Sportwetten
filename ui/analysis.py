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
    """Summary first; source data and portfolio rules are never changed here."""
    from ui.analysis_summary import analysis_summary
    from ui.components import text, table

    event_id = str(details.event.event_id)
    heading, control = st.columns([3, 1])
    heading.markdown('<p class="w-eyebrow">Analyse · 0 oder 2+ Tore</p>', unsafe_allow_html=True)
    heading.caption("Ein Szenario mit zwei Einzelwetten. Genau ein Tor verliert den gesamten Einsatz.")
    stake = control.number_input(
        "Szenario-Einsatz (€)", min_value=1, max_value=1000,
        value=max(1, min(1000, int(round(analysis.strategy.total_stake or 30)))),
        step=1, key=f"analysis-stake-{event_id}",
        help="Nur diese Berechnung. Ändert weder Paper-Portfolios noch gespeicherte Einstiegsquoten.",
    )
    model = analysis_summary(details, analysis, stake=float(stake),
                             max_age=intelligence_service.settings.max_live_odds_age_seconds)
    strategy = model["strategy"]
    probability = analysis.probability
    age_text = f'{model["age"]:.1f} s'.replace(".", ",") if model["age"] is not None else "unbekannt"
    freshness = "Veraltet" if model["stale"] else "Frisch"
    badge = "warning" if model["stale"] else ""
    st.markdown(
        f'<section class="w-hero {text(model["tone"])}"><div>'
        f'<h3>{text(model["title"])}</h3><p>{text(model["description"])}</p></div>'
        f'<span class="w-badge {badge}">{freshness} · {age_text}</span></section>',
        unsafe_allow_html=True,
    )
    basis = "Zweite Halbzeit" if model["halftime"] else "Verbleibende Spielzeit ab dieser Beobachtung · kein HZ-Einstieg"
    st.caption(f'{basis} · Tipico: {format_local_datetime(analysis.observed_at)}')

    def percent(value):
        return _pct(value).replace(".", ",")

    def signed_money(value):
        return ("+" if value is not None and value > 0 else "") + _eur(value)

    buffer = strategy.p1_buffer if model["probability_ok"] else None
    buffer_text = "—" if buffer is None else f"{buffer * 100:+.1f} pp".replace(".", ",")
    metrics = [
        ("Gewinnfall · Rendite", percent(strategy.win_roi), "Nur bei 0 oder mindestens 2 Toren", False),
        ("Marktmodell · Erwartungswert", signed_money(model["ev"]),
         f'{percent(model["ev_roi"])} des Einsatzes · Schätzung, keine Garantie', model["ev"] is not None and model["ev"] < 0),
        ("Markt-Puffer", buffer_text, "Break-even minus P(genau 1 Tor)", buffer is not None and buffer < 0),
    ]
    cards = "".join(
        f'<div class="w-kpi"><label>{text(label)}</label><div class="w-number {"negative" if negative else ""}">'
        f'{text(value)}</div><small>{text(note)}</small></div>' for label, value, note, negative in metrics
    )
    st.markdown(f'<div class="w-grid">{cards}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="w-section"><h3>Was wird aus deinen {_eur(float(stake))}?</h3>'
                '<span>Auszahlung inklusive Einsatz · Netto nach beiden Wetten</span></div>', unsafe_allow_html=True)
    scenarios = [
        ("0 Tore", strategy.stake_zero, strategy.q_zero, strategy.payout_zero, probability.p0, False),
        ("Genau 1 Tor", None, None, 0 if model["quotes_ok"] else None, probability.p1, True),
        ("2+ Tore", strategy.stake_two_plus, strategy.q_two_plus, strategy.payout_two_plus, probability.p2_plus, False),
    ]
    cards = []
    for label, allocation, odds, payout, likelihood, loss in scenarios:
        pnl = payout - stake if payout is not None else None
        cards.append(
            f'<article class="w-scenario {"loss" if loss or pnl is not None and pnl < 0 else ""}"><h4>{label}</h4>'
            f'<div class="w-number">{text(signed_money(pnl))}</div><small>Gewinn / Verlust insgesamt</small>'
            f'<dl><dt>Auszahlung</dt><dd>{text(_eur(payout))}</dd>'
            f'<dt>{"Abdeckung" if loss else "Dein Teileinsatz"}</dt><dd>{text("Nicht abgedeckt" if loss else _eur(allocation))}</dd>'
            f'<dt>Quote</dt><dd>{text(_odds(odds))}</dd>'
            f'<dt>Markt-Schätzung</dt><dd>{text(percent(likelihood) if model["probability_ok"] else "nicht verfügbar")}</dd></dl></article>'
        )
    st.markdown('<div class="w-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)
    st.caption(
        f'Verlustschwelle P1: {percent(strategy.p1_max)} · '
        f'Markt-Schätzung für genau 1 Tor: {percent(probability.p1) if model["probability_ok"] else "nicht verfügbar"}. '
        'Die Schätzung stammt aus Tipico-Quoten, nicht aus einem unabhängigen ML-Modell. '
        'Gebühren, Steuer und reale Ausführung sind nicht simuliert.'
    )

    with st.expander("Quellen & Marktvergleich", expanded=False):
        table([_best_row("0 Tore", analysis.zero_equivalence.best_odds),
               _best_row("2+ Tore", analysis.two_plus_equivalence.best_odds)])
        for market in (analysis.zero_equivalence, analysis.two_plus_equivalence):
            st.caption(f"{market.label}: {market.explanation} · {market.status}")
        provenance = []
        for label, market in (("0 Tore", analysis.zero_equivalence), ("2+ Tore", analysis.two_plus_equivalence)):
            for candidate in market.candidates:
                provenance.append({
                    "Ziel": label, "Quote": _odds(candidate.odds),
                    "Markt / Auswahl": candidate.source_label, "Market ID": candidate.market_id,
                    "Outcome ID": candidate.outcome_id,
                    "Beobachtet": format_local_datetime(candidate.observed_at),
                    "Status": candidate.status, "Offen": "Ja" if candidate.is_open else "Nein",
                })
        table(provenance)
    with st.expander("Rechenweg & Datenqualität", expanded=False):
        table([
            {"Kennzahl": "P1-Break-even", "Wert": percent(strategy.p1_max), "Definition": "1 − 1/Quote 0 − 1/Quote 2+; vor Cent-Rundung"},
            {"Kennzahl": "Markt-Puffer", "Wert": buffer_text, "Definition": "P1-Break-even − geschätztes P1, in Prozentpunkten"},
            {"Kennzahl": "Erwartungswert", "Wert": signed_money(model["ev"]), "Definition": "P(0) × Auszahlung 0 + P(2+) × Auszahlung 2+ − Gesamteinsatz"},
            {"Kennzahl": "Win-ROI", "Wert": percent(strategy.win_roi), "Definition": "Gewinn im abgedeckten Fall / Einsatz; vor Cent-Rundung, ohne Verlustfälle"},
        ])
        st.caption(f'Wahrscheinlichkeitsquelle: {probability.source or "nicht verfügbar"} · Status: {probability.status}')
        st.caption(f'Strategie: {strategy.strategy_version} · Quotenalter-Grenze: {intelligence_service.settings.max_live_odds_age_seconds} s · Rundungsdifferenz: {_eur(strategy.payout_difference)}')
        for warning in analysis.warnings:
            st.write(warning)
    if _one_second_half_goal(details.event):
        _render_rescue(details, analysis)
