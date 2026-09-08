"""Country-scoped provider labels observed during September 2026 recovery.

These are explicit display-label equivalences, not fuzzy cross-league links.
Group numbers and stages are retained. Unknown labels still use the strict
shared matcher. Real-canary source responses are retained outside Git.
"""

from functools import lru_cache

COMPETITION_LABEL_GROUPS = {
    "usa": [("MLS", "Major League Soccer")],
    "nigeria": [("Professional Football League", "NPFL")],
    "israel": [("National League", "Leumit League"), ("Premier League", "Ligat ha'Al")],
    "luxemburg": [("Division Nationale", "National Division")],
    "schweden": [("Division 1, Södra", "Ettan Soedra"), ("Division 1, Norra", "Ettan Norra")],
    "norwegen": [("2. Division, Gruppe 1", "2. Divisjon Avd. 1"), ("2. Division, Gruppe 2", "2. Divisjon Avd. 2")],
    "spanien": [("Primera Division RFEF, Gruppe 1", "Primera Federacion - Group 1"),
                ("Primera Division RFEF, Gruppe 2", "Primera Federacion - Group 2")],
    "saudi arabien": [("Saudi Professional Liga", "Saudi Pro League"), ("Division 1", "Saudi First Division")],
    "osterreich": [("ÖFB-Cup", "Cup")],
    "japan": [("J-League Pokal", "League Cup")],
    "zypern": [("1. Division", "Cyprus League")],
    "belgien": [("Challenger Pro League", "First Division B")],
    "indonesien": [("Liga 1", "Super League")],
    "irak": [("Iraqi League", "Stars League")],
    "slowakei": [("Superliga", "1. Liga")],
    "belarus": [("Vysshaya League", "Premier League")],
    "chile": [("Primera Division", "Chile Primera")],
    "lettland": [("Virsliga", "Latvian Virsliga")],
    "island": [("Besta deild", "Besta deildin", "Icelandic Besta deild")],
    "sudafrika": [("Premier League", "Premier Soccer League", "South Africa PSL")],
    "katar": [("2. Division", "Qatar Stars League 2")],
    "sudkorea": [("K-League 2", "K League 2", "Korean K-League 2")],
    "turkei": [("1.Lig", "1. Lig", "Turkish 1. Lig")],
}


@lru_cache(maxsize=8192)
def competition_label_key(country, name, normalize):
    normalized = normalize(name)
    for index, labels in enumerate(COMPETITION_LABEL_GROUPS.get(country, ())):
        if normalized in {normalize(label) for label in labels}:
            return f"country-label:{country}:{index}"
    return normalized
