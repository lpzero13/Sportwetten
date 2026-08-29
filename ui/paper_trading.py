"""Paper Trading dashboard with a compact mobile rendering."""

from __future__ import annotations

import csv
import io
from typing import Any

import streamlit as st

from paper.analytics import calibration_rows, grouped_trade_rows, portfolio_analytics
from paper.models import PaperPortfolio
from paper.service import PaperTradingService
from storage.database import Database
from ui.time_format import format_local_datetime


def _money(value: Any, currency: str = "EUR") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f} {currency}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


def _percent(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _trade_row(row: Any, currency: str) -> dict[str, Any]:
    status = str(row["status"] or "")
    return {
        "Zeit": format_local_datetime(row["created_at"]),
        "Spiel": f"{row['home_team']} – {row['away_team']}",
        "Liga": row["competition_name"],
        "Land": row["competition_country"] or "—",
        "HZ": f"{row['ht_score_home'] if row['ht_score_home'] is not None else '-'}:{row['ht_score_away'] if row['ht_score_away'] is not None else '-'}",
        "P/L": _money(row["pnl"], currency),
        "Status": status,
        "P1": _percent(row["p1_tipico"]),
        "Puffer": _percent(row["p1_buffer"]),
        "Trade-ID": row["paper_trade_id"],
    }


def _render_trade_cards(rows: list[Any], currency: str) -> None:
    for row in rows:
        status = str(row["status"] or "")
        st.markdown(
            f"""
            <div class="paper-card">
              <strong>{row['home_team']} – {row['away_team']}</strong><br>
              <small>{row['competition_name']} · {row['competition_country'] or 'Land unbekannt'} · {format_local_datetime(row['created_at'])}</small><br>
              Status: <strong>{status}</strong> · Einsatz: {_money(row['stake_total'], currency)} · P/L: {_money(row['pnl'], currency)}<br>
              HZ {row['ht_score_home'] if row['ht_score_home'] is not None else '-'}:{row['ht_score_away'] if row['ht_score_away'] is not None else '-'} · Q0 {row['q_zero']:.2f} · Q2+ {row['q_two_plus']:.2f}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _portfolio_summary(service: PaperTradingService, portfolios: list[PaperPortfolio], mobile: bool) -> None:
    rows: list[dict[str, Any]] = []
    for portfolio in portfolios:
        trades = service.database.paper_trade_rows(portfolio.portfolio_id, limit=5000)
        metrics = portfolio_analytics(
            portfolio,
            trades,
            available_bankroll=service.database.paper_balance(portfolio.portfolio_id),
        )
        rows.append(
            {
                "Portfolio": portfolio.name,
                "Status": portfolio.status,
                "Kapital": _money(metrics["capital"], portfolio.currency),
                "Verfügbar": _money(metrics["available"], portfolio.currency),
                "Exposure": _money(metrics["exposure"], portfolio.currency),
                "P/L": _money(metrics["pnl"], portfolio.currency),
                "ROI": _percent(metrics["roi"]),
                "Trades": metrics["total_trades"],
            }
        )
    st.subheader("Portfolios")
    if mobile:
        for row in rows:
            st.markdown(
                f"<div class='paper-card'><strong>{row['Portfolio']}</strong> · {row['Status']}<br>"
                f"Kapital {row['Kapital']} · verfügbar {row['Verfügbar']} · P/L {row['P/L']}<br>"
                f"{row['Trades']} Trades · ROI {row['ROI']}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.dataframe(rows, hide_index=True, width="stretch")


def _render_create_form(service: PaperTradingService, database: Database) -> None:
    competitions = database.list_competitions()
    competition_ids = [str(row["competition_id"]) for row in competitions]
    competition_labels = {
        str(row["competition_id"]): (
            f"{row['competition_name']} · {row['country_or_region'] or 'Land unbekannt'}"
        )
        for row in competitions
    }
    with st.expander("Neues Paper-Portfolio", expanded=not bool(service.portfolios(include_archived=True))):
        with st.form("create-paper-portfolio"):
            name = st.text_input("Name", value="Tipico HZ Test")
            first = st.columns(2)
            bankroll = first[0].number_input("Startkapital (€)", min_value=1.0, value=1000.0, step=50.0)
            mode = first[1].selectbox("Einsatzmodus", ["FIXED", "BANKROLL_PERCENTAGE"], format_func=lambda value: "Fester Einsatz" if value == "FIXED" else "% vom verfügbaren Kapital")
            second = st.columns(2)
            fixed_stake = second[0].number_input("Fester Einsatz (€)", min_value=0.01, value=10.0, step=1.0, disabled=mode != "FIXED")
            percentage = second[1].number_input("Bankroll (%)", min_value=0.01, value=2.0, step=0.5, disabled=mode != "BANKROLL_PERCENTAGE")
            third = st.columns(3)
            min_stake = third[0].number_input("Min. Einsatz (€)", min_value=0.0, value=0.0, step=1.0)
            max_stake = third[1].number_input("Max. Einsatz (€)", min_value=0.0, value=0.0, step=1.0)
            max_age = third[2].number_input("Max. Quotenalter (s)", min_value=1, value=10, step=1)
            fourth = st.columns(3)
            min_roi = fourth[0].number_input("Min. Win-ROI (%)", min_value=-100.0, value=0.0, step=0.5) / 100
            min_buffer = fourth[1].number_input("Min. P1-Puffer (%)", min_value=-100.0, value=0.0, step=0.5) / 100
            max_p1 = fourth[2].number_input("Max. Tipico-P1 (%)", min_value=0.0, max_value=100.0, value=100.0, step=1.0) / 100
            fifth = st.columns(2)
            min_q_zero = fifth[0].number_input("Min. Quote 0", min_value=1.01, value=1.01, step=0.05)
            min_q_two = fifth[1].number_input("Min. Quote 2+", min_value=1.01, value=1.01, step=0.05)
            all_competitions = st.checkbox("Alle Wettbewerbe zulassen", value=True)
            selected = st.multiselect(
                "Wettbewerbe",
                competition_ids,
                default=[],
                format_func=lambda value: competition_labels.get(value, value),
                disabled=all_competitions,
            )
            submitted = st.form_submit_button("Portfolio anlegen", type="primary", width="stretch")
        if submitted:
            try:
                service.create_portfolio(
                    name=name,
                    starting_bankroll=bankroll,
                    stake_mode=mode,
                    fixed_stake=fixed_stake if mode == "FIXED" else None,
                    bankroll_percentage=percentage if mode == "BANKROLL_PERCENTAGE" else None,
                    min_stake=min_stake or None,
                    max_stake=max_stake or None,
                    minimum_win_roi=min_roi,
                    minimum_p1_buffer=min_buffer,
                    maximum_tipico_p1=max_p1,
                    minimum_q_zero=min_q_zero,
                    minimum_q_two_plus=min_q_two,
                    max_quote_age_seconds=int(max_age),
                    allow_all_competitions=all_competitions,
                    selected_competition_ids=selected,
                )
            except (TypeError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.success("Paper-Portfolio angelegt.")
                st.rerun()


def _render_signals(database: Database, portfolio: PaperPortfolio) -> None:
    with database._lock:  # noqa: SLF001 - read-only dashboard query
        rows = database.connection.execute(
            """
            SELECT signal_id, observed_at, event_id, evaluation_id,
                   decision, reason, details_json
            FROM paper_signal_log
            WHERE portfolio_id = ?
            ORDER BY signal_id DESC LIMIT 300
            """,
            (portfolio.portfolio_id,),
        ).fetchall()
    if not rows:
        st.info("Noch keine Paper-Signale protokolliert.")
        return
    st.dataframe(
        [
            {
                "Zeit": format_local_datetime(row["observed_at"]),
                "Event": row["event_id"],
                "Entscheidung": row["decision"],
                "Grund": row["reason"],
                "Details": row["details_json"],
            }
            for row in rows
        ],
        hide_index=True,
        width="stretch",
    )


def _csv_export(rows: list[Any], currency: str) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "paper_trade_id", "portfolio_id", "event_id", "competition_name",
        "competition_country", "created_at", "status", "q_zero", "q_two_plus",
        "stake_total", "stake_zero", "stake_two_plus", "payout_zero",
        "payout_two_plus", "p1_tipico", "p1_buffer", "win_roi", "settled_at",
        "final_score_home", "final_score_away", "second_half_goals", "return_amount", "pnl",
    ])
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row[key] for key in writer.fieldnames})
    return output.getvalue()


def _render_selected_portfolio(service: PaperTradingService, database: Database, portfolio: PaperPortfolio, mobile: bool) -> None:
    st.subheader(f"{portfolio.name} · {portfolio.status}")
    status_columns = st.columns(4)
    if portfolio.status != "ACTIVE" and status_columns[0].button("Aktivieren", key=f"activate-{portfolio.portfolio_id}"):
        service.set_portfolio_status(portfolio.portfolio_id, "ACTIVE")
        st.rerun()
    if portfolio.status == "ACTIVE" and status_columns[1].button("Pausieren", key=f"pause-{portfolio.portfolio_id}"):
        service.set_portfolio_status(portfolio.portfolio_id, "PAUSED")
        st.rerun()
    if portfolio.status != "ARCHIVED" and status_columns[2].button("Archivieren", key=f"archive-{portfolio.portfolio_id}"):
        service.set_portfolio_status(portfolio.portfolio_id, "ARCHIVED")
        st.rerun()
    with status_columns[3].form("manual-adjustment-" + portfolio.portfolio_id):
        amount = st.number_input("Manuell (€)", value=0.0, step=10.0, key=f"manual-amount-{portfolio.portfolio_id}")
        if st.form_submit_button("Buchen") and amount:
            service.record_manual_adjustment(portfolio.portfolio_id, amount)
            st.success("Ledger angepasst.")
            st.rerun()

    competitions = database.list_competitions()
    competition_ids = [str(row["competition_id"]) for row in competitions]
    competition_labels = {
        str(row["competition_id"]): (
            f"{row['competition_name']} · {row['country_or_region'] or 'Land unbekannt'}"
        )
        for row in competitions
    }
    with st.expander("Portfolio-Regeln bearbeiten", expanded=False):
        with st.form("edit-paper-portfolio-" + portfolio.portfolio_id):
            name = st.text_input("Name", value=portfolio.name)
            mode = st.selectbox(
                "Einsatzmodus",
                ["FIXED", "BANKROLL_PERCENTAGE"],
                index=0 if portfolio.stake_mode == "FIXED" else 1,
                format_func=lambda value: "Fester Einsatz" if value == "FIXED" else "% vom verfügbaren Kapital",
            )
            rule_columns = st.columns(3)
            fixed_stake = rule_columns[0].number_input(
                "Fester Einsatz (€)", min_value=0.01,
                value=float(portfolio.fixed_stake or 10), step=1.0,
            )
            percentage = rule_columns[1].number_input(
                "Bankroll (%)", min_value=0.01,
                value=float(portfolio.bankroll_percentage or 2), step=0.5,
            )
            max_age = rule_columns[2].number_input(
                "Max. Quotenalter (s)", min_value=1,
                value=int(portfolio.max_quote_age_seconds), step=1,
            )
            threshold_columns = st.columns(3)
            min_roi = threshold_columns[0].number_input("Min. Win-ROI (%)", value=float(portfolio.minimum_win_roi * 100), step=0.5) / 100
            min_buffer = threshold_columns[1].number_input("Min. P1-Puffer (%)", value=float(portfolio.minimum_p1_buffer * 100), step=0.5) / 100
            max_p1 = threshold_columns[2].number_input("Max. Tipico-P1 (%)", min_value=0.0, max_value=100.0, value=float(portfolio.maximum_tipico_p1 * 100), step=1.0) / 100
            stake_columns = st.columns(2)
            min_stake = stake_columns[0].number_input("Min. Einsatz (€)", min_value=0.0, value=float(portfolio.min_stake or 0), step=1.0)
            max_stake = stake_columns[1].number_input("Max. Einsatz (€)", min_value=0.0, value=float(portfolio.max_stake or 0), step=1.0)
            all_competitions = st.checkbox("Alle Wettbewerbe zulassen", value=portfolio.allow_all_competitions)
            selected = st.multiselect(
                "Wettbewerbe",
                competition_ids,
                default=[item for item in portfolio.selected_competition_ids if item in competition_ids],
                format_func=lambda value: competition_labels.get(value, value),
                disabled=all_competitions,
            )
            if st.form_submit_button("Änderungen speichern", type="primary", width="stretch"):
                service.update_portfolio(
                    portfolio.portfolio_id,
                    name=name,
                    stake_mode=mode,
                    fixed_stake=fixed_stake if mode == "FIXED" else None,
                    bankroll_percentage=percentage if mode == "BANKROLL_PERCENTAGE" else None,
                    min_stake=min_stake or None,
                    max_stake=max_stake or None,
                    minimum_win_roi=min_roi,
                    minimum_p1_buffer=min_buffer,
                    maximum_tipico_p1=max_p1,
                    max_quote_age_seconds=int(max_age),
                    allow_all_competitions=all_competitions,
                    selected_competition_ids=selected,
                )
                st.success("Portfolio-Regeln gespeichert.")
                st.rerun()

    trades = database.paper_trade_rows(portfolio.portfolio_id, limit=5000)
    metrics = portfolio_analytics(
        portfolio,
        trades,
        available_bankroll=database.paper_balance(portfolio.portfolio_id),
    )
    cards = [
        ("Kapital", _money(metrics["capital"], portfolio.currency)),
        ("Verfügbar", _money(metrics["available"], portfolio.currency)),
        ("Exposure", _money(metrics["exposure"], portfolio.currency)),
        ("P/L", _money(metrics["pnl"], portfolio.currency)),
        ("ROI", _percent(metrics["roi"])),
        ("Trefferquote", _percent(metrics["hit_rate"])),
        ("Max. Drawdown", _money(metrics["max_drawdown"], portfolio.currency)),
        ("Profit Factor", f"{metrics['profit_factor']:.2f}" if metrics["profit_factor"] is not None else "—"),
    ]
    columns = st.columns(2 if mobile else 4)
    for index, (label, value) in enumerate(cards):
        columns[index % len(columns)].metric(label, value)

    tabs = st.tabs(["Übersicht", "Trades", "Nach Wettbewerb", "Nach P1/Puffer/ROI", "Calibration", "Signale", "CSV"])
    with tabs[0]:
        st.caption(
            f"Strategie {portfolio.strategy_type} · Einsatz {portfolio.stake_mode} · "
            f"HT-Fenster {portfolio.entry_window_start_seconds}–{portfolio.entry_window_end_seconds} s · "
            f"Quote max. {portfolio.max_quote_age_seconds} s"
        )
        if metrics["bankroll_curve"]:
            st.line_chart(metrics["bankroll_curve"], height=220)
        st.write({
            "Abgerechnet": metrics["settled_trades"],
            "Offen": metrics["open_trades"],
            "Wins": metrics["wins"],
            "Verluste (genau 1 HZ2-Tor)": metrics["losses"],
            "Void": metrics["void"],
            "Unresolved": metrics["unresolved"],
            "Längste Siegesserie": metrics["longest_win_streak"],
            "Längste Verlustserie": metrics["longest_loss_streak"],
        })
    with tabs[1]:
        filter_value = st.selectbox("Trade-Filter", ["Alle", "Offen", "Abgerechnet", "Void/Unresolved"])
        if filter_value == "Offen":
            visible = [row for row in trades if row["status"] == "OPEN"]
        elif filter_value == "Abgerechnet":
            visible = [row for row in trades if row["status"] in {"WIN_ZERO", "WIN_TWO_PLUS", "LOSS_MIDDLE"}]
        elif filter_value == "Void/Unresolved":
            visible = [row for row in trades if row["status"] in {"VOID", "UNRESOLVED", "INVALIDATED"}]
        else:
            visible = trades
        if mobile:
            _render_trade_cards(visible, portfolio.currency)
        elif visible:
            st.dataframe([_trade_row(row, portfolio.currency) for row in visible], hide_index=True, width="stretch")
        else:
            st.info("Keine Trades für diesen Filter.")
    with tabs[2]:
        st.dataframe(grouped_trade_rows(trades, "competition"), hide_index=True, width="stretch")
    with tabs[3]:
        for dimension, title in (("p1", "Nach Tipico P1"), ("p1_buffer", "Nach P1-Puffer"), ("win_roi", "Nach Win-ROI")):
            st.markdown(f"**{title}**")
            st.dataframe(grouped_trade_rows(trades, dimension), hide_index=True, width="stretch")
    with tabs[4]:
        rows = calibration_rows(trades)
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
            st.caption("Nur abgearbeitete Trades; genau ein Tor in Halbzeit 2 entspricht dem Verlustfall der Strategie.")
        else:
            st.info("Noch keine geeigneten abgeschlossenen Trades für die Calibration.")
    with tabs[5]:
        _render_signals(database, portfolio)
    with tabs[6]:
        st.download_button(
            "Trades als CSV herunterladen",
            data=_csv_export(trades, portfolio.currency),
            file_name=f"paper_trades_{portfolio.portfolio_id}.csv",
            mime="text/csv",
            width="stretch",
        )


def render_paper_trading(
    service: PaperTradingService,
    database: Database,
    *,
    mobile: bool = False,
) -> None:
    st.title("Paper Trading")
    st.caption(
        "Simulierte Einsätze auf Basis der eingefrorenen Tipico-Halbzeitquoten. "
        "Es werden keine echten Wetten platziert."
    )
    current_enabled = service.is_enabled()
    enabled = st.checkbox(
        "Paper-Trading global aktiv",
        value=current_enabled,
        key="paper-global-enabled",
        help="Kill-Switch für den unabhängigen Paper-Worker.",
    )
    if enabled != current_enabled:
        service.set_enabled(enabled)
        st.rerun()
    if enabled:
        st.success("Paper-Worker darf Signale in Trades umwandeln.")
    else:
        st.warning("Paper-Trading ist global pausiert; es werden keine neuen Trades eröffnet.")
    worker_seen = database.get_paper_runtime_setting("worker_last_seen_at")
    st.caption(
        "Paper-Worker letzter Lauf: "
        + (format_local_datetime(worker_seen) if worker_seen else "noch nicht ausgeführt")
    )
    _render_create_form(service, database)
    portfolios = service.portfolios(include_archived=False)
    if not portfolios:
        st.info("Lege zuerst ein Paper-Portfolio an.")
        return
    _portfolio_summary(service, portfolios, mobile)
    options = [portfolio.portfolio_id for portfolio in portfolios]
    labels = {portfolio.portfolio_id: f"{portfolio.name} · {portfolio.status}" for portfolio in portfolios}
    selected_id = st.selectbox("Portfolio auswählen", options, format_func=lambda value: labels.get(value, value))
    portfolio = service.portfolio(selected_id)
    if portfolio is not None:
        _render_selected_portfolio(service, database, portfolio, mobile)
