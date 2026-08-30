# V0.5 Status – FotMob Discovery, Matching & Live Enrichment

Stand: 30.08.2026

## Gesamtstatus

Die V0.5-Basisimplementierung ist lokal verifiziert. Der Abschluss von
V0.5.1 ist **PASS**: Die reale strukturierte Serienvalidierung wurde wegen
`AUTOMATED_USAGE=UNCLEAR` nicht erzwungen, und die Provider-Entscheidung ist
`LIMITED_USE`.

V0.4.2 bleibt fachlich unverändert. FotMob ist ein optionales Enrichment und
hat keinen Pfad in Tipico-Marktlogik, Strategie, Ranking, Paper-Entry oder
Settlement.

## Umgesetzt

- separates `fotmob/`-Modul mit Client, Parser, Models, Matching, Service und
  Storage
- feature-flagged Client mit Default `FOTMOB_ENABLED=false`, Timeout,
  Retries, Intervall, Metriken und Fehlerisolierung
- provider-neutrale `matches` und `match_provider_links`
- Teams, Provider-Aliase und Competition-Aliase in der bestehenden SQLite-Datei
- deterministisches Matching mit ±15-Minuten-Default, Länderabgleich,
  Heim/Auswärts-Schutz, Reserve-/Jugendschutz und manueller Bestätigung
- Current State als eine ersetzbare Zeile je Match
- genau sieben mögliche FotMob-Historien-Slots mit SQLite-Outbox und
  ZSTD-Parquet-Archiv unter `archive/fotmob/snapshots`
- nullable Full-Time-/Half-Time-Statistik, Ereignistimeline, Extra-Stats und
  Result-/HT-Consistency-Flags
- FotMob-Tab im Eventdetail, read-only Statsdarstellung ohne Wettsignal,
  Matching-Debugger und informative HZ-Scanner-Spalten
- optionaler `wetten-fotmob.service`, standardmäßig installiert aber nicht
  aktiviert; er refresh’t nur bestätigte Links
- lokale Tests und Parser-Smoke-Checks

## Bewusst nicht vorgezogen

Keine FotMob-Quoten, keine beste Quote, keine normalisierte Wettsemantik,
keine Strategieänderung, keine automatische HZ2-Wette, kein ROI-/Ranking-
Einfluss, kein Bulk-Historical-Import und keine Schätzung fehlender HT-Stats.

## V0.5.1-Abschluss

Die vollständige Abschlussprüfung, Coverage-Matrix und Entscheidung stehen in:

- [FOTMOB_FINAL_VALIDATION.md](FOTMOB_FINAL_VALIDATION.md)
- [FOTMOB_HISTORICAL_COVERAGE.md](FOTMOB_HISTORICAL_COVERAGE.md)
- [FOTMOB_PROVIDER_DECISION.md](FOTMOB_PROVIDER_DECISION.md)
- [V051_STATUS.md](V051_STATUS.md)

```text
FOTMOB_V051_STATUS = PASS
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
```

## Historischer Validierungsstand der V0.5-Basis

| Bereich | Status |
|---|---|
| Browser-Seite/Liga/Land/Match sichtbar | PASS für geprüfte Beispiele |
| Historische HT-Stats im Browser sichtbar | PASS für geprüfte Bundesliga-Partie |
| stabiler automatisierter JSON-Vertrag | OPEN / nicht zugesichert |
| Live-/Upcoming-Sampling mehrerer Wettbewerbe | OPEN |
| Langzeit-/Rate-Limit-Test | OPEN |
| automatische produktive Matching-Coverage | 0 in dieser Discovery, kein Bulk-Lauf |
| Tipico-Ausfall ohne FotMob-Ausfall | PASS durch Trennung und Tests |
| FotMob-Ausfall ohne Tipico/Paper-Ausfall | PASS durch Trennung und Tests |

Die externe Zugriffsgrenze und die konkreten Browserbefunde sind in
`outputs/FOTMOB_DISCOVERY.md` dokumentiert. Bei der V0.5.1-Entscheidung bleibt
die Nutzung auf manuelle Einzelspiele beschränkt; der periodische Worker ist
bei der aktuellen Policy deaktiviert.
