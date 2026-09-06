"""Provider name pairs observed in the 2026-09-05 live/day feeds."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fotmob.matching import MatchIdentity, MatchMatcher
from fotmob.models import FotMobMatch


def identities():
    event = SimpleNamespace(event_id="720968910", home_team="TSG Hoffenheim",
        away_team="Borussia Dortmund", competition_name="Bundesliga",
        competition_country="Deutschland", kickoff_time="2026-09-05T13:30:01+00:00")
    match = FotMobMatch(provider_match_id="5881156", home_team="Hoffenheim",
        away_team="Dortmund", competition_id="54", competition_name="Bundesliga", competition_country="GER",
        kickoff_at="2026-09-05T13:30:00+00:00")
    return MatchIdentity.from_tipico_event(event), match


@pytest.mark.parametrize("home,away,provider_home,provider_away", [
    ("TSG Hoffenheim", "Borussia Dortmund", "Hoffenheim", "Dortmund"),
    ("Bayer Leverkusen", "Union Berlin", "Leverkusen", "Union Berlin"),
])
def test_live_day_feed_short_names_match(home, away, provider_home, provider_away):
    event, match = identities()
    result = MatchMatcher().match(replace(event, home_team=home, away_team=away),
        [replace(match, home_team=provider_home, away_team=provider_away)])
    assert result.status == "EXACT"


@pytest.mark.parametrize("change", [
    {"home_team": "Dortmund", "away_team": "Hoffenheim"},
    {"home_team": "Hoffenheim II"},
    {"away_team": "Dortmund U19"},
    {"home_team": "Hoffenheim Frauen"},
    {"competition_country": "AUT"},
    {"competition_name": "Premier League"},
    {"kickoff_at": "2026-09-05T18:30:00+00:00"},
])
def test_short_names_never_bypass_identity_guards(change):
    event, match = identities()
    assert not MatchMatcher().match(event, [replace(match, **change)]).auto_linkable


def test_duplicate_short_name_candidates_remain_ambiguous():
    event, match = identities()
    result = MatchMatcher().match(event, [match, replace(match, provider_match_id="other")])
    assert result.status == "AMBIGUOUS"
    assert result.provider_match_id is None
