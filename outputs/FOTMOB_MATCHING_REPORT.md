# FotMob Matching Report – V0.5.1

Stand: 30.08.2026, Europe/Berlin

## Aktueller Verifikationsstand

Die Matching-Engine und die persistente Storage-Struktur sind implementiert.
Die reale Browser-Stichprobe umfasst **1 manuell geprüftes FotMob-Match**:
Bayern München – VfB Stuttgart, Bundesliga, Deutschland, 28.08.2026.
Für dieses Beispiel wurde in der Abschlussprüfung keine automatische Tipico-
Serienpaarung ausgeführt. Automatische Coverage ist daher
`NOT_RUN_BY_POLICY`, nicht ein als Produktionswert misszuverstehendes 0/0.

| Messgröße | Ergebnis |
|---|---:|
| reale manuelle FotMob-Identitätsstichprobe | 1 |
| automatisch verlinkte Tipico-Events | NOT_RUN_BY_POLICY |
| manuell bestätigte Tipico-Links aus dieser Stichprobe | NOT_RUN_BY_POLICY |
| Bulk-Candidates | NOT_RUN_BY_POLICY |
| Browser-Crosscheck | 1 |

Die Begrenzung folgt `AUTOMATED_USAGE=UNCLEAR` und der in der Quelle sichtbaren
Einschränkung gegen systematische oder regelmäßige automatische Nutzung.

## Pipeline

1. Bekannte Provider-ID bzw. bereits gespeicherter Alias.
2. Exakte normalisierte Heim-/Auswärtsnamen.
3. Kickoff-Abgleich im konfigurierbaren Fenster, Default ±15 Minuten.
4. Exakter oder persistierter Wettbewerbs-/Länderabgleich.
5. Kontrolliertes Fuzzy Matching nur als Discovery-Hilfe.
6. `UNMATCHED`, wenn eine Bedingung fehlt oder eine Schutzregel greift.

Die Engine akzeptiert nie nur denselben Kalendertag. Heim- und Auswärtsteam
werden nicht vertauscht. Reserven, U-Mannschaften und Frauenmarker werden
nicht gegen eine normale Mannschaft zusammengeführt. Mehrere gleich starke
Kandidaten werden `AMBIGUOUS` und nicht automatisch verknüpft.

## Status und Auto-Link-Regel

| Status | Bedeutung | Automatischer Link |
|---|---|---:|
| `EXACT` | Namen/IDs, Wettbewerb und Zeit passen | Ja |
| `HIGH_CONFIDENCE` | kontrollierte Alias/Fuzzy-Kombination mit ausreichender Konfidenz | Ja |
| `AMBIGUOUS` | mehrere Kandidaten im gleichen Konfidenzband | Nein |
| `UNMATCHED` | außerhalb Zeitfenster, Länder-/Teamkonflikt oder zu wenig Evidenz | Nein |
| `MANUALLY_CONFIRMED` | vom Nutzer bestätigter Kandidat | Ja |
| `REJECTED` | dauerhaft abgelehnter Kandidat | Nein |

Konfidenz liegt zwischen 0 und 1 und wird zusammen mit den Gründen im
`match_provider_links`-Datensatz gespeichert. Nur `EXACT` und
`HIGH_CONFIDENCE` werden automatisch verlinkt. Manuelle Bestätigung und
Ablehnung bleiben persistent.

## Alias- und Länderlogik

`teams`, `team_provider_aliases`, `competition_provider_aliases` und
`matches` liegen in derselben SQLite-Datei wie Tipico. Bestätigte Aliaspaare
werden mit Provider-ID und normalisiertem Namen abgelegt. Die Bundesliga
Deutschland und Bundesliga Österreich bleiben durch den Länderabgleich
getrennt; ein gleicher Ligatitel reicht nicht aus.

## Testabdeckung

Automatisierte Tests decken Alias-/Akzentvarianten, Heim/Auswärts-Reversal,
Reserve-Schutz, Länder- und Zeitkonflikt, Ambiguität, nullable Stats,
persistenten Alias, Current-State-Refresh ohne Historienwachstum,
idempotente Halbzeitslots, Provider-Ausfall und Ergebnisvergleich ab.

Die geforderten Serien aus Live, Upcoming, Top-/Second-/Small-Leagues sowie
Frauen und Jugend wurden deshalb nicht künstlich erzwungen. Sie sind in
V0.5.1 `NOT_SAMPLED_BY_POLICY`. Die technische und fachliche Entscheidung
steht in [FOTMOB_FINAL_VALIDATION.md](FOTMOB_FINAL_VALIDATION.md) und
[FOTMOB_PROVIDER_DECISION.md](FOTMOB_PROVIDER_DECISION.md); es wird kein
weiterer FotMob-Discovery-Milestone eröffnet.
