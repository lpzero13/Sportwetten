# FotMob Bundesliga Discovery – V0.5.2

Stand: 30.08.2026, Europe/Berlin

```text
TARGET_LEAGUE_ID = 54
FOTMOB_V052_STATUS = PARTIAL
FOTMOB_EXTERNAL_DISCOVERY = NOT_RUN_BY_POLICY
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
```

## Ergebnis

Die Discovery-Grundlage ist implementiert und offline verifiziert. Der Job
verwendet die konfigurierbaren League-/Season-Pfade, liest die Season-Liste
aus der Providerantwort und speichert `season_id`, normalisiertes
`season_label`, Liga und Land in `fotmob_seasons`. Season-IDs werden nie aus
dem Label konstruiert und es gibt keinen Brute-Force-Lauf über eine globale
Match-ID-Spanne.

Die echten Antworten für Liga 54 wurden in diesem Milestone nicht erneut
systematisch abgerufen. Deshalb werden hier bewusst keine echten Season-IDs,
Fixture-Zahlen oder Match-IDs behauptet. Die externe Discovery bleibt
`NOT_RUN_BY_POLICY`; die lokalen Tests verwenden klar gekennzeichnete
synthetische Payloads.

## Ausführbare Jobs

~~~powershell
python scripts/discover_fotmob_league.py --league-id 54 --root .
python scripts/index_fotmob_matches.py --league-id 54 --season 2025/26 --root .
~~~

Der erste Befehl schreibt den Season-Katalog. Der zweite Befehl liest die
aufgelöste echte `season_id` und schreibt pro Provider-Match eine Zeile in
`fotmob_match_index`. Bei fehlender ausdrücklicher Providerfreigabe endet der
Netzwerkpfad mit `BLOCKED_BY_POLICY` und führt keinen HTTP-Request aus.

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

Die Abnahme gilt erst als Discovery-PASS, wenn die echte Providerantwort für
die Ziel-Liga sichtbar gespeichert und reproduzierbar wiederverwendbar ist.
Das ist hier nicht der Fall; daher `PARTIAL` statt eines künstlichen PASS.

Die öffentliche FotMob-Seite weist außerdem darauf hin, dass automatische,
systematische oder regelmäßige Nutzung nicht erlaubt ist:
[FotMob](https://www.fotmob.com/de). Diese bestehende V0.5.1-Entscheidung
bleibt für V0.5.2 wirksam.
