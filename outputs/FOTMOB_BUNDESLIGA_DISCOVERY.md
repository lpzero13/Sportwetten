# FotMob Bundesliga Discovery – V0.5.2

Stand: 30.08.2026, Europe/Berlin

```text
TARGET_LEAGUE_ID = 54
FOTMOB_V052_STATUS = PASS
FOTMOB_NETWORK_MODE = manual
FOTMOB_EXTERNAL_DISCOVERY = PASS
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
```

## Ergebnis

Die Discovery wurde mit einem begrenzten manuellen Lauf gegen die aktuelle
öffentliche FotMob-Seite verifiziert. Der alte API-Pfad
`/api/leagues?id=54` liefert aktuell HTTP 404. Die funktionierende aktuelle
Quelle ist die HTML-Seite `GET /leagues/54`, deren Next.js-
`__NEXT_DATA__`-Payload die Liga-Metadaten, `stats.seasonStatLinks` und alle
Fixtures enthält. Für eine historische Season wird derselbe öffentliche Pfad
mit dem sichtbaren vollständigen Label aufgerufen:

```text
GET https://www.fotmob.com/leagues/54
GET https://www.fotmob.com/leagues/54?season=2025/2026
GET https://www.fotmob.com/leagues/54?season=2024/2025
```

Die echte Discoveryantwort für Liga 54 enthält unter anderem:

| Provider-Season-ID | Label | League | Country-Code |
|---:|---|---|---|
| `26891` | `2025/2026` | Bundesliga | `GER` |
| `23794` | `2024/2025` | Bundesliga | `GER` |

Zusätzlich wurde `40040` für `2026/2027` entdeckt. Season-IDs werden nie aus
dem Label konstruiert. Der Indexlauf hat für beide Zielseasons jeweils `306`
Matches erkannt und mit Provider-ID in `fotmob_match_index` persistiert; es
gab keinen Brute-Force-Lauf über eine globale Match-ID-Spanne.

## Ausführbare Jobs

~~~powershell
$env:FOTMOB_ENABLED="true"
$env:FOTMOB_HISTORY_ENABLED="true"
$env:FOTMOB_NETWORK_MODE="manual"
python scripts/fotmob_history.py seasons --league 54 --root .
python scripts/fotmob_history.py index --league 54 --season 2025/26 --root .
~~~

Der erste Befehl schreibt den Season-Katalog. Der zweite Befehl liest die
aufgelöste echte `season_id` und schreibt pro Provider-Match eine Zeile in
`fotmob_match_index`. Der manuelle Modus ist nur für bewusst gestartete CLI-
Jobs aktiv; bei fehlenden drei manuellen Einstellungen bleibt der Netzwerkpfad
mit `BLOCKED_BY_POLICY` ohne HTTP-Request. Ein permanenter Worker verwendet
stattdessen `FOTMOB_NETWORK_MODE=worker` und benötigt zusätzlich die
Provider-Gates aus der Architektur.

Für Offline-Regressionen können beide Jobs mit `--payload` auf eine lokale
JSON-Datei zeigen. Bei einem Index ohne Katalog ist dann entweder eine Season
mit enthalten oder `--season-id` zusammen mit `--season-label` erforderlich.

## Speicherung

| Katalog | Zweck |
|---|---|
| `fotmob_seasons` | echte Provider-Season-ID und Label je Liga |
| `fotmob_match_index` | Match-ID, Teams, Kickoff, Runde, Status und Queuezustand |
| `fotmob_history_samples` | persistierte deterministische Sample-Ränge |
| `fotmob_historical_archive_index` | Deduplizierung nach Provider-Match-ID und Schema-Version |

Die Abnahme ist erfüllt: die echte Antwort wurde unter
`work/v052-real-validation-final/data/tipico.db` gespeichert, die beiden
Season-IDs wurden daraus aufgelöst und die Indexzahlen sind reproduzierbar.
Ein erneuter Indexlauf auf demselben Katalog ist dedupliziert.

Die bestehende V0.5.1-Entscheidung `LIMITED_USE`/`UNCLEAR` bleibt für
regelmäßige Automation wirksam. Die PASS-Aussage bezieht sich ausschließlich
auf den begrenzten manuellen technischen Validierungslauf.
