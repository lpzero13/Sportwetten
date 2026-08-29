# Tipico Paper Trading V0.4 – Status

Stand: 29.08.2026 16:36 UTC
Scope: Tipico-Landpersistenz, Paper Trading und mobile Dashboard-Ansicht

## Status

TIPICO_V04_STATUS = PASS

V0.3 bleibt enthalten. FotMob, weitere Anbieter, ML, automatische Optimierung
und echte Wettabgabe wurden nicht vorgezogen.

## Implementiert

- `sportCompetitionMap.parentName` wird als `competition_country` am Event und
  als `country_or_region` in `competitions` gespeichert.
- Gleichnamige Ligen werden in der UI getrennt angezeigt, zum Beispiel
  `Bundesliga · Deutschland` und `Bundesliga · Österreich`.
- Eine additive Datenbankmigration ergänzt alte V0.3-Datenbanken ohne Reset.
- Das Backfill-Skript `scripts/backfill_competition_countries.py` kann bereits
  gespeicherte Live-Payloads nachträglich auswerten.
- Mehrere Paper-Portfolios mit EUR-Startkapital, Fixed-/Bankroll-%-Einsatz,
  Min-/Max-Einsatz, Liga-Filter, Quote-Alter, P1-, P1-Puffer-, Win-ROI- und
  Quote-Schwellen sowie HT-Einstiegsfenster.
- Globaler Paper-Kill-Switch, Pause/Archivierung, manuelle Ledger-Anpassung,
  Signal-Log und CSV-Export.
- Ein Trade wird pro Portfolio/Event/Strategie höchstens einmal eröffnet.
  Reservierung und Settlement laufen in SQLite-Transaktionen mit WAL,
  `BEGIN IMMEDIATE` und eindeutigen Idempotenzschlüsseln.
- Einstiegsdaten (Quoten, Markt-/Outcome-IDs, Score, Zeit, Versionen,
  Einsatzverteilung, P1 und Bankroll) werden als immutable Snapshot erhalten.
- Settlement verwendet ausschließlich `FT-Gesamttore – HT-Gesamttore` und
  kennt `WIN_ZERO`, `LOSS_MIDDLE`, `WIN_TWO_PLUS`, `VOID` und `UNRESOLVED`.
- `scripts/run_paper.py` läuft als unabhängiger Worker; der Collector und die
  Streamlit-Oberfläche bleiben getrennte Prozesse.
- Proxmox installiert jetzt `wetten-ui.service`, `wetten-collector.service`
  und `wetten-paper.service`.
- Mobilgeräte werden automatisch über den Browser-User-Agent erkannt. Die
  responsive Ansicht ist zusätzlich mit `?view=mobile` und `?view=desktop`
  testbar.

## Verifikation

```text
python -m pytest -q
28 passed in 1.38s

python scripts/validate_v04.py --root . --max-events 8
```

Der V0.4-Live-Smoke-Test lieferte 98 aktuelle Tipico-Fußball-Events. In der
lokalen Datenbank waren 155 Wettbewerbe mit Länderfeld vorhanden; die
Bundesliga wurde getrennt für Deutschland und Österreich gefunden. Das
Backfill aktualisierte 256 bestehende Eventzeilen aus 790 gespeicherten
Live-Payloads.

Die Paper-Integrationstests decken feste und prozentuale Einsätze,
Min-/Max-/Insufficient-Bankroll, Signalfilter, alle Settlement-Klassen,
HT 1:1 → FT 2:2, doppelte Worker-Läufe und unveränderte Einstiegsquoten ab.

Die laufende Streamlit-Oberfläche wurde geprüft mit:

- Navigation zu **Paper Trading** und sichtbarem globalem Kill-Switch;
- `Bundesliga · Deutschland` und `Bundesliga · Österreich` in der Live-Liste;
- automatischer Mobilkennzeichnung bei 390×844 Pixeln und URL-Override
  `?view=mobile`;
- Paper-Portfolio-Formular ohne Browser-/Streamlit-Fehler.

## Bedienhinweis

Nach dem Update Portfolio im Bereich **Paper Trading** anlegen und den
globalen Schalter nur für die gewünschte Simulation aktivieren. Der Proxmox-
Worker eröffnet erst Trades, wenn ein aktives Portfolio die Signalkriterien
erfüllt. Für den Dauerbetrieb:

```bash
systemctl status wetten-ui wetten-collector wetten-paper
journalctl -u wetten-paper -f
```
