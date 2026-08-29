# Tipico Market Intelligence & Strategy Dashboard V0.3 – Status

Stand: 29.08.2026 14:23 UTC  
Scope: read-only Tipico-Liveanalyse, ohne Wettabgabe

## Status

TIPICO_V03_STATUS = PASS

Der V0.3-Funktionsumfang ist implementiert und gegen den aktuellen Tipico-
Livefeed verifiziert. V0.2 bleibt bewusst separat `TIPICO_V02_STATUS = PARTIAL`,
weil die vollständige Spieltagshistorie noch nicht gesammelt wurde.

## Implementiert

- Vier Bereiche: **Live**, **Upcoming**, **Halftime Scanner** und
  **Data / Debug**.
- Live-Fußballfeed ohne Wettbewerbs- oder Länderfilter; Wettbewerbe sind
  einklappbar. Pro Event gibt es getrennte Aktionen für **Quoten** und
  **Analyse**.
- Normalisierung mit `normalizer_version = v0.3.1` anhand von Tipico-Typ,
  `fixedParam`, `choiceParam` und Marktstruktur. Unbekannte Marktformen bleiben
  vollständig als Raw-Daten erhalten und werden als `UNKNOWN` markiert.
- Kanonische Zielmärkte für Resttore, Match-Totals, Next Goal, Team-Resttore,
  BTTS und Rest-1X2.
- Äquivalenzauflösung für `ZERO_REMAINING_GOALS` und
  `TWO_OR_MORE_REMAINING_GOALS`, inklusive dynamischer Match-Totallinien und
  Settlement-Scope-Prüfung.
- Best Odds mit Freshness-Grenze von standardmäßig 10 Sekunden, Statusfilter,
  Alternativen und Quellen-Provenienz.
- Wahrscheinlichkeitsverteilung `P(0)`, `P(exakt 1)`, `P(2+)` aus den
  reziproken U/O-Paaren; inkonsistente Märkte werden nicht still korrigiert.
- Strategie `ZERO_OR_2PLUS` mit Cent-Rundung, gleichmäßiger Auszahlung,
  Einsatz-Slider, Szenario-P/L, Win-ROI, P1-Maximum und strukturellem Puffer.
- Halbzeit-Scanner mit frischem HZ-Snapshot oder gezieltem Detail-Fallback,
  Rangfolge `P1_BUFFER` absteigend und `WIN_ROI` sekundär; unvollständige
  Events werden getrennt angezeigt.
- Optionales Dynamic-Middle-Schadensprofil für genau ein HZ2-Tor; keine
  Wett- oder Hedge-Empfehlung und keine Wettabgabe.
- Persistenz in `canonical_outcomes` und `strategy_evaluations`; Rohdaten und
  ursprüngliche Quoten bleiben unverändert erhalten.

## Reproduzierbare Tests

```text
python -m pytest -q
22 passed in 0.91s

python scripts/validate_v03.py --root work/v03-validation-final --max-events 10
```

Zusätzlich wurde der gesamte relevante Python-Code erfolgreich kompiliert
(`compileall`).

## Echter Tipico-Live-Smoke-Test

Ausgeführt am 29.08.2026 um 14:23 UTC mit dem aktuellen REST-Livefeed:

| Messgröße | Ergebnis |
|---|---:|
| Live-Fußball-Events | 142 |
| aktuelle Halbzeit-Events gesehen/getestet | 9 / 9 |
| Äquivalenzgruppen | 18 |
| Best-Odds-Prüfungen | 18 |
| Probability `OK` | 9 |
| Strategy-Quote-Sets | 9 |
| gespeicherte kanonische Outcomes | 1.046 |
| gespeicherte Strategy-Evaluations | 9 |

Alle neun getesteten Events lieferten `EQUIVALENT` für beide Zielgruppen,
`OK` für die Wahrscheinlichkeitsverteilung und `OK` für die Strategiequote.
Die Detailantworten enthielten je nach Event 23–46 Märkte und 51–153
Outcomes. Nicht erkannte Marktformen wurden nicht verworfen, sondern mit den
Rohdaten persistiert.

## Browser-Smoke-Test

Mit dem laufenden Streamlit-Server wurden verifiziert:

- Live-Liste mit 142 Events, Suche nach `Jong Almere`, Wettbewerb aufklappen
  und Analyse öffnen.
- Eventdetail mit 30 Märkten / 103 Outcomes, Datenalter unter einer Sekunde,
  Äquivalenz, Provenienz, Verteilung und Strategie im Tab **Analyse**.
- Tabs **Alle Tipico Märkte**, **Odds History** und **Raw / Debug**.
- **Upcoming** mit 260 geladenen Pre-Match-Events.
- **Halftime Scanner** mit 7–9 aktuellen HZ-Events und vollständig rankbaren
  Ergebnissen im jeweiligen Smoke-Fenster.
- **Data / Debug** mit API-Status, Coverage-, Marktform- und Persistenzdaten.
- Dynamic-Middle-Expander mit Originalquoten, aktuellem No-More-Goal-Markt,
  Hedge-Slider und Schadensprofil.

## Abnahme-Matrix

| Bereich | Status | Beleg |
|---|---|---|
| Schichtenmodell / Raw-Erhalt | PASS | `intelligence/`, DB-Migration, Live-Smoke |
| Normalizer / UNKNOWN | PASS | Unit-Tests und reale Detailantworten |
| Äquivalenz / dynamische Linien | PASS | 18 reale Gruppen, Unit-Tests |
| Freshness / Best Odds | PASS | 18 reale Checks, Unit-Tests |
| Probability | PASS | 9 reale `OK`-Verteilungen, Inconsistency-Test |
| `ZERO_OR_2PLUS` | PASS | 9 reale Quote-Sets, Rounding-Test |
| Persistenz | PASS | 1.046 kanonische Outcomes / 9 Evaluations |
| Streamlit-Navigation und Detailtabs | PASS | Browser-Smoke-Test |
| HZ-Scanner / Ranking | PASS | aktuelle HZ-Events im Browser-Smoke |
| FotMob, ML, Wettabgabe | bewusst nicht enthalten | außerhalb V0.3 |

## Bewusste Grenzen

- Es gibt weiterhin `UNKNOWN`-Marktformen. Das ist korrektes Verhalten für
  V0.3: sie werden sichtbar gemacht und raw historisiert, aber nicht ohne
  getestete Semantik in kanonische Märkte gezwungen.
- Der aktuelle Live-Smoke beweist die Funktionsfähigkeit gegen den Feed, nicht
  die bereits abgeschlossene vollständige V0.2-Spieltagshistorie.
- Die Oberfläche ist read-only. Es existieren keine Buttons oder Flows für
  Wettschein, Wettabgabe oder Wett-Empfehlungen.

## Nächster Milestone

V0.2 bleibt der historische Collector-Milestone: Collector über einen ganzen
Spieltag laufen lassen und Pre-Match-, Halbzeit-, HZ2- und Finalabdeckung
nachweisen. Die in V0.3 implementierte Strategie- und Dashboard-Logik wird
dafür bereits automatisch bei erfolgreichen Snapshots persistiert.
