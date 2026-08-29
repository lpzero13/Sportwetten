"""Market and outcome rendering for one normalized event."""

from __future__ import annotations

from collections import OrderedDict

import streamlit as st

from models.market import EventDetails, Market, Outcome


def _format_odds(outcome: Outcome) -> str:
    if not outcome.is_available:
        if outcome.status == "paused":
            return "🔒 PAUSED"
        return "— nicht verfügbar"
    if outcome.quote_float_value is None:
        return "—"
    return f"{outcome.quote_float_value:.2f}".replace(".", ",")


def _market_rows(market: Market) -> list[dict[str, str]]:
    return [
        {
            "Auswahl": outcome.caption,
            "Quote": _format_odds(outcome),
            "Status": (
                "OPEN"
                if outcome.is_available
                else (outcome.status.upper() if outcome.status else "UNAVAILABLE")
            ),
        }
        for outcome in market.outcomes
    ]


def _group_markets(details: EventDetails) -> OrderedDict[str, list[Market]]:
    categories = OrderedDict(
        (str(category.get("id")), str(category.get("name") or category.get("id")))
        for category in details.categories
        if category.get("id") is not None
    )
    groups: OrderedDict[str, list[Market]] = OrderedDict(
        (name, []) for name in categories.values()
    )
    groups.setdefault("Weitere Märkte", [])
    assigned: set[str] = set()

    for market in details.markets:
        category_name = next(
            (name for name in market.category_names if name in groups),
            "Weitere Märkte",
        )
        if market.market_id in assigned:
            continue
        groups.setdefault(category_name, []).append(market)
        assigned.add(market.market_id)

    for markets in groups.values():
        markets.sort(key=lambda item: (item.caption.casefold(), item.market_id))
    return OrderedDict((name, markets) for name, markets in groups.items() if markets)


def render_markets(details: EventDetails) -> None:
    """Render all markets, including unknown types and unavailable outcomes."""

    for category_name, markets in _group_markets(details).items():
        with st.expander(category_name, expanded=True):
            for market in markets:
                fixed = f" · fixedParam={market.fixed_param}" if market.fixed_param else ""
                status = market.status.upper()
                st.markdown(
                    f"**{market.caption}**  \n"
                    f"{market.type}{fixed} · {status} · "
                    f"{len(market.outcomes)} Outcomes"
                )
                if market.outcomes:
                    st.dataframe(
                        _market_rows(market),
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    st.caption("Tipico liefert aktuell keine Outcomes für diesen Markt.")
