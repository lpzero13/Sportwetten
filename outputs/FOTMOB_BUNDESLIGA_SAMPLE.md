# FotMob Bundesliga Historical Sample – V0.5.2

Stand: 30.08.2026, Europe/Berlin

```text
FOTMOB_V052_STATUS = PASS
FOTMOB_NETWORK_MODE = manual
FOTMOB_EXTERNAL_SAMPLE = PASS (10/10)
TARGET = 5 finished matches per season for 2025/26 and 2024/25
```

## Sample-Regel

Nach dem Indexieren werden nur als beendet erkannte Matches berücksichtigt.
Die Sortierung ist stabil nach Kickoff und Provider-Match-ID. Bei mindestens
fünf abgeschlossenen Matches werden die Positionen ungefähr bei 0 %, 25 %,
50 %, 75 % und 100 % gewählt; die IDs werden in `fotmob_history_samples`
persistiert. Bei weniger als fünf Matches wird der Sample-Status `PARTIAL`.

Die deterministische Auswahl und der Detailabruf wurden gegen echte FotMob-
Antworten ausgeführt. Der öffentliche Detailpfad ist
`https://www.fotmob.com/match/{match_id}`; die Antwort enthält die Next.js-
Payload mit `general`, `header`, `content.matchFacts.events.events` und
`content.stats.Periods`.

### Reale 2025/2026-Sample

| Rang | Match-ID | Spiel | HT | FT | 2H-Tore | Klasse |
|---:|---:|---|---:|---:|---:|---|
| 1 | `4824901` | Bayern München – RB Leipzig | 3:0 | 6:0 | 3 | `2_PLUS` |
| 2 | `4829393` | St. Pauli – Borussia Mönchengladbach | 0:2 | 0:4 | 2 | `2_PLUS` |
| 3 | `4829471` | Hamburger SV – Borussia Mönchengladbach | 0:0 | 0:0 | 0 | `0` |
| 4 | `4829546` | Hoffenheim – Wolfsburg | 0:0 | 1:1 | 2 | `2_PLUS` |
| 5 | `4829620` | Werder Bremen – Borussia Dortmund | 0:0 | 0:2 | 2 | `2_PLUS` |

### Reale 2024/2025-Sample

| Rang | Match-ID | Spiel | HT | FT | 2H-Tore | Klasse |
|---:|---:|---|---:|---:|---:|---|
| 1 | `4534540` | Borussia Mönchengladbach – Bayer Leverkusen | 0:2 | 2:3 | 3 | `2_PLUS` |
| 2 | `4534618` | Wolfsburg – Augsburg | 0:1 | 1:1 | 1 | `1` |
| 3 | `4534696` | Eintracht Frankfurt – Borussia Dortmund | 1:0 | 2:0 | 1 | `1` |
| 4 | `4534771` | Union Berlin – Bayern München | 0:0 | 1:1 | 2 | `2_PLUS` |
| 5 | `4534845` | St. Pauli – Bochum | 0:1 | 0:2 | 1 | `1` |

Alle zehn Zeilen sind `data_quality=COMPLETE` und `ml_eligible=1`. Für jede
Zeile sind HT-/FT-xG und Schusswerte vorhanden; die Timeline umfasst die
Provider-Ereignisse. Der berechnete Zielwert erfüllt überall
`second_half_goals = FT total - HT total`.

## Detail-Normalisierung

Jede erfolgreiche Detailantwort wird als flache Zeile mit
`fotmob_historical_v1` und `fotmob_historical_parser_v1` geschrieben. Die
Zeile enthält getrennte `ht_*`- und `ft_*`-Scores/Statistikfelder,
`timeline_json`, unbekannte Statistikfelder in JSON sowie:

- `COMPLETE`: gültige HT-/FT-Scores und explizite HT-Statistiken
- `PARTIAL`: Detailantwort ohne vollständige Score-/HT-Statistikbasis
- `SCORE_ONLY`: gültige Scores ohne Statistikdaten
- `INVALID`: FT-Gesamttore kleiner als HT-Gesamttore

`second_half_goals` wird nur bei vier vorhandenen Scores als
`FT total - HT total` berechnet. `ml_eligible` ist nur bei gültigen HT-/FT-
Scores wahr. Fehlende Werte werden nicht als 0 ergänzt.

## Ausführung nach einer neuen Providerfreigabe

~~~powershell
$env:FOTMOB_ENABLED="true"
$env:FOTMOB_HISTORY_ENABLED="true"
$env:FOTMOB_NETWORK_MODE="manual"
python scripts/fotmob_history.py sample --league 54 --season 2025/26 --matches 5 --root .
python scripts/fotmob_history.py sample --league 54 --season 2024/25 --matches 5 --root .
python scripts/scan_fotmob_history.py --league 54 --season 2025/26 --sample-only --workers 1 --root .
python scripts/scan_fotmob_history.py --league 54 --season 2024/25 --sample-only --workers 1 --root .
~~~

Die Detail-Queue arbeitet mit maximal konfigurierten Versuchen, speichert
Fehler und Worker-ID, setzt stale `IN_PROGRESS`-Claims zurück, schreibt
Parquet batchesicher und dedupliziert mit Provider-ID plus Schema-Version.
Der Resume-Test für beide Seasons ergab `processed=0`, `requests=0` und
`remaining=0`; ein erneuter Lauf schreibt bereits archivierte Zeilen nicht
noch einmal. Der begrenzte Lauf verwendete fünf HTTP-200-Requests je Season,
`workers=1`, `0.5` Requests/Sekunde und
`STORE_FOTMOB_HISTORICAL_RAW=true`.
Der Default bleibt `workers=1`, `0.5` Requests/Sekunde und
`STORE_FOTMOB_HISTORICAL_RAW=false`.
