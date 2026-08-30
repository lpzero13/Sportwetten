# FotMob Bundesliga Historical Sample – V0.5.2

Stand: 30.08.2026, Europe/Berlin

```text
FOTMOB_V052_STATUS = PARTIAL
FOTMOB_EXTERNAL_SAMPLE = NOT_EXECUTED_BY_POLICY
TARGET = 5 finished matches per season for 2025/26 and 2024/25
```

## Sample-Regel

Nach dem Indexieren werden nur als beendet erkannte Matches berücksichtigt.
Die Sortierung ist stabil nach Kickoff und Provider-Match-ID. Bei mindestens
fünf abgeschlossenen Matches werden die Positionen ungefähr bei 0 %, 25 %,
50 %, 75 % und 100 % gewählt; die IDs werden in `fotmob_history_samples`
persistiert. Bei weniger als fünf Matches wird der Sample-Status `PARTIAL`.

Die zehn echten FotMob-Detailantworten für die beiden Bundesliga-Saisons
wurden in diesem Milestone nicht abgerufen. Es werden daher keine realen IDs,
Scores, Statistiken oder Coverage-Zahlen erfunden. Die lokale End-to-End-
Regression bestätigt die Auswahlregel mit synthetischen Indexdaten und fünf
Detailpayloads; das ist kein Provider-Sample.

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
python scripts/fotmob_history.py sample --league 54 --season 2025/26 --matches 5 --root .
python scripts/fotmob_history.py sample --league 54 --season 2024/25 --matches 5 --root .
python scripts/scan_fotmob_history.py --league 54 --season 2025/26 --sample-only --root .
python scripts/scan_fotmob_history.py --league 54 --season 2024/25 --sample-only --root .
~~~

Die Detail-Queue arbeitet mit maximal konfigurierten Versuchen, speichert
Fehler und Worker-ID, setzt stale `IN_PROGRESS`-Claims zurück, schreibt
Parquet batchesicher und dedupliziert mit Provider-ID plus Schema-Version.
Der Default bleibt `workers=1`, `0.5` Requests/Sekunde und
`STORE_FOTMOB_HISTORICAL_RAW=false`.
