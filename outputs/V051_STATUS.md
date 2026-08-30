# V0.5.1 Status – FotMob Final Validation & Provider Decision

Stand: 30.08.2026, Europe/Berlin

```text
FOTMOB_V051_STATUS = PASS
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
```

## Abnahme

| Bereich | Status |
|---|---|
| aktuelle Browser-/Nutzungsprüfung | PASS |
| sichtbare Liga-/Land-/Match-/HT-Evidenz | PASS für geprüfte Beispiele |
| Parser mit nullable HT-/Stats-Feldern | PASS |
| deterministisches Matching und Schutzregeln | PASS |
| Current-State / sieben Slots / Outbox / ZSTD-Parquet | PASS |
| Tipico-/Paper-Isolation | PASS |
| strukturierte Multi-Ligen-/Live-/Upcoming-Coverage | NOT_RUN_BY_POLICY |
| Provider-Entscheidung | PASS – LIMITED_USE |

## Bewusst nicht umgesetzt

Keine FotMob-Quoten, keine Strategie- oder Rankingänderung, keine
Profitbewertung, kein Bulk-Historical-Crawler, kein dauerhafter FotMob-
Collector, keine ML-/Dataset-Erweiterung und kein Vorziehen von V0.2-Themen.

## Abschluss

Der FotMob-Provider ist damit für V0.5.1 entschieden und geschlossen. Die
Anwendung nutzt FotMob standardmäßig nicht; ein ausdrücklich ausgewähltes
Einzelspiel kann – sofern die aktuellen Bedingungen das zulassen – manuell
als read-only Enrichment geprüft werden. Tipico und Paper Trading bleiben
unabhängig betriebsfähig.
