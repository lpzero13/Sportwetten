from __future__ import annotations

import json
from pathlib import Path

from tipico.parser import parse_event_details, parse_live_feed, parse_upcoming_feed


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_live_feed_resolves_competition_and_score() -> None:
    events = parse_live_feed(load_fixture("live_feed.json"))

    assert len(events) == 1
    event = events[0]
    assert event.event_id == "721621110"
    assert isinstance(event.event_id, str)
    assert event.competition_id == "104124301"
    assert event.competition_name == "We-League, Frauen"
    assert event.home_team == "Nagano Parceiro (F)"
    assert event.away_team == "NTV Beleza (F)"
    assert event.display_minute == "50'"
    assert event.score_home == 1
    assert event.score_away == 1
    assert event.ht_score_home == 1
    assert event.ht_score_away == 1
    assert event.red_cards_home == 0
    assert event.bet_markets_count == 16


def test_event_detail_uses_id_graph_and_deduplicates_market() -> None:
    details = parse_event_details(load_fixture("event_detail.json"))

    assert details.event.event_id == "721621110"
    assert details.event.section_number == 2
    assert details.market_count == 3
    assert details.outcome_count == 8

    shared = next(market for market in details.markets if market.market_id == "86623140510")
    assert shared.category_names == ["Hauptwetten", "Über/Unter"]
    assert shared.fixed_param == "3:0.5"
    assert shared.type == "points-more-less-rest"

    standard = next(market for market in details.markets if market.type == "standard")
    assert [outcome.caption for outcome in standard.outcomes] == ["1", "X", "2"]
    assert standard.outcomes[1].quote_float_value == 2.9
    assert standard.outcomes[1].is_available is True
    assert isinstance(standard.market_id, str)
    assert isinstance(standard.outcomes[0].outcome_id, str)


def test_paused_outcome_is_not_available_even_with_float_value_one() -> None:
    details = parse_event_details(load_fixture("event_paused_market.json"))
    outcome = details.markets[0].outcomes[1]

    assert outcome.status == "paused"
    assert outcome.quote_raw is None
    assert outcome.quote_float_value == 1.0
    assert outcome.odds is None
    assert outcome.is_available is False


def test_stopped_outcome_is_not_available_even_with_numeric_quote() -> None:
    payload = load_fixture("event_paused_market.json")
    payload["results"]["233210274710"]["quote"] = "1.95"
    payload["results"]["233210274710"]["quoteFloatValue"] = 1.95
    payload["results"]["233210274710"]["status"] = "stopped"

    details = parse_event_details(payload)
    outcome = details.markets[0].outcomes[1]

    assert outcome.status == "stopped"
    assert outcome.quote_raw == "1.95"
    assert outcome.quote_float_value == 1.95
    assert outcome.odds is None
    assert outcome.is_available is False


def test_upcoming_feed_parses_prematch_events_and_region() -> None:
    payload = {
        "UPCOMING": {
            "sportCompetitionMap": {
                "soccer": [
                    {
                        "groupId": 123,
                        "groupIdString": "123",
                        "name": "Testliga",
                        "parentName": "Testland",
                    }
                ]
            },
            "events": {
                "9001": {
                    "id": "9001",
                    "status": "pre_match",
                    "team1": "Heim",
                    "team2": "Gast",
                    "competitionId": 123,
                    "eventStartTime": 1788015600000,
                    "date": "Heute",
                }
            },
            "eventsBySport": {"soccer": ["9001"]},
            "scores": {},
        }
    }

    events = parse_upcoming_feed(payload)

    assert len(events) == 1
    assert events[0].status == "pre_match"
    assert events[0].competition_name == "Testliga"
    assert events[0].raw_data["groups"] == ["Testliga", "Testland", "Fußball"]
