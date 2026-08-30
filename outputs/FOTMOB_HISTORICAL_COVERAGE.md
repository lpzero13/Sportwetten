# FotMob Historical Coverage – V0.5.1

Stand: 30.08.2026, Europe/Berlin

Diese Matrix trennt sichtbare Browser-Evidenz von automatisiert validierter
strukturierter Abdeckung. Nicht geprüfte Kategorien werden nicht mit NULL-
oder Nullwerten als Datenabdeckung dargestellt.

## Tatsächlich geprüfte Stichprobe

| Liga | Land | Saison | Matches | Metadaten | HT-Score | HT xG | HT Schüsse | HT SOT | HT Großchancen | HT Ecken | HT Ballbesitz | HT Karten | Strukturierter Serienstatus |
|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|
| Bundesliga | Deutschland | 2026/2027 | 1 | AVAILABLE (UI) | AVAILABLE (UI) | AVAILABLE (UI) | AVAILABLE (UI) | AVAILABLE (UI) | AVAILABLE (UI) | AVAILABLE (UI) | AVAILABLE (UI) | AVAILABLE (UI) | NOT_VALIDATED |

Beispiel: Bayern München – VfB Stuttgart, 5:1, Halbzeit 1:0. Der erste
Halbzeitfilter zeigte xG 1,34:0,40, Schüsse 9:4, Schüsse aufs Tor 5:0,
Großchancen 3:1, Ecken 5:2 und Ballbesitz 56:44. Die gelben Karten waren in
der ersten Halbzeit mit 0:0 sichtbar.

## Geforderte Abdeckung, die nicht freigegeben wurde

| Kategorie | Vorgabe aus V0.5.1 | Ergebnis |
|---|---|---|
| Top-Ligen | Bundesliga, Premier League, La Liga, Serie A, Ligue 1 | nur Bundesliga mit einem UI-Beispiel geprüft |
| Zweite Ligen | mindestens zwei, z. B. 2. Bundesliga/Championship | NOT_SAMPLED_BY_POLICY |
| Kleinere/internationale Ligen | mindestens zwei | NOT_SAMPLED_BY_POLICY |
| Frauen | mindestens drei Matches | NOT_SAMPLED_BY_POLICY |
| Jugend/Reserve | mindestens drei Matches | NOT_SAMPLED_BY_POLICY; keine Reserveverwechslung |
| Saisons | 2026/27, 2025, 2024, 2022, 2020 soweit verfügbar | nur 2026/27 als UI-Beispiel |
| Live | 10 Matches, sofern zulässig | NOT_SAMPLED_BY_POLICY |
| Upcoming | 10 Tipico-Matches | NOT_SAMPLED_BY_POLICY |

## Interpretation

Die Stichprobe beweist, dass FotMob auf der geprüften Matchseite historische
HT-Daten anzeigen kann. Sie beweist nicht, dass alle Felder für jede Liga,
Saison oder Live-Phase existieren, gleich benannt sind oder über einen
stabilen öffentlichen JSON-Vertrag automatisiert bezogen werden dürfen.

Daher ist die HT-Abdeckung für eine mögliche spätere manuelle Recherche
**PARTIALLY** ausreichend, für einen automatischen historischen oder Live-
Collector aber **nicht validiert**. Fehlende Werte bleiben im Code `NULL`/
`None`; sie werden nicht als 0 interpretiert.
