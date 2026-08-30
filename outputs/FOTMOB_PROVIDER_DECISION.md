# FotMob Provider Decision – V0.5.1

Stand: 30.08.2026, Europe/Berlin

```text
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
FOTMOB_V051_STATUS = PASS
HT_FEATURE_USEFULNESS = PARTIALLY
```

## Begründung

FotMob ist im Browser ohne Login fachlich nützlich: Liga/Land, Match-
Metadaten, Endstand, Halbzeitstand, Ereignisse und für das geprüfte Spiel
auch eigenständige Halbzeitstatistiken waren sichtbar. Gleichzeitig weist die
öffentliche Seite auf ein Verbot automatischer, systematischer oder
regelmäßiger Nutzung hin. Ein stabiler, ausdrücklich freigegebener
strukturierter API-Vertrag wurde nicht bestätigt.

Die Entscheidung lautet deshalb **LIMITED_USE** und nicht
`PRODUCTION_READY`. `NOT_SUITABLE` wäre zu streng für die nachweisbare
manuelle Einzelspiel-Recherche; eine produktive Freigabe wäre durch die
Nutzungs- und Vertragsunsicherheit nicht begründet.

## Betriebsregeln

| Regel | Umsetzung |
|---|---|
| Default | `FOTMOB_ENABLED=false` |
| Einzelspiel | nur ausdrücklich ausgewählte Match-ID, sofern zulässig |
| Periodischer Worker | bei `LIMITED_USE`/`UNCLEAR` automatisch deaktiviert |
| Bulk-Crawler | nicht vorhanden / nicht ausführen |
| Login, Token- oder CAPTCHA-Umgehung | nicht vorhanden |
| Tipico/Paper Trading | keinerlei Abhängigkeit von FotMob |
| Datenlücken | `NULL`/`None`, keine Schätzung als 0 |
| weitere FotMob-Discovery | mit V0.5.1 geschlossen |

Die technische Sperre für den Worker verlangt sowohl
`FOTMOB_PROVIDER_DECISION=PRODUCTION_READY` als auch
`FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT`. Diese Werte sind absichtlich
nicht der Default und werden durch die vorliegende Entscheidung nicht gesetzt.

## Konsequenz

FotMob bleibt ein optionaler, read-only Enrichment-/Research-Pfad. Es gibt
keine Aussage über Profitabilität, Wettvorteil oder Strategiequalität. Eine
spätere Änderung auf `PRODUCTION_READY` erfordert eine neue, ausdrücklich
zulässige Provider-Freigabe und eine erneute Validierung außerhalb dieses
abgeschlossenen Meilensteins.
