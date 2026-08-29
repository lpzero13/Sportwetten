# Tipico Market Intelligence Dashboard V0.4

Lokales, read-only Streamlit-Tool zur Beobachtung des öffentlichen Tipico-Live-Fußballfeeds
mit getrenntem Paper-Trading für die validierte `ZERO_OR_2PLUS`-Strategie.

Der aktuelle Projektumfang umfasst:

- alle aktuell gelieferten Live-Fußballspiele
- dynamische Wettbewerb-/Liga-Zuordnung
- Tipico-Land/Region je Wettbewerb, z. B. `Bundesliga · Deutschland` oder
  `Bundesliga · Österreich`, in Events und `competitions` persistiert
- Spielstand, Spielminute und Phase
- vollständige Märkte und Outcomes für genau ein geöffnetes Event
- Quoten-/Status-Historie in SQLite
- deduplizierte Raw-Tipico-JSON-Payloads
- REST-Polling und System-/Debugansicht
- unabhängiger Background Collector für historische Snapshots
- Pre-Match-Discovery über den verifizierten Tipico-Hour-Events-REST-Pfad
- Halbzeit-Phasen, Goal-/Final-Trigger und Core-Market-Tracking
- Data-Collection-Seite mit Coverage-Metriken und Event Inspector
- deterministische Canonical-Market-Normalisierung mit UNKNOWN-Fallback
- scoreabhängige Äquivalenzauflösung für 0 bzw. 2+ verbleibende Tore
- freshness-aware Best Odds und Tipico-implied P0/P1/P2+-Verteilung
- ZERO_OR_2PLUS-Szenarien, Cent-Einsatzoptimierung und struktureller P1-Puffer
- Upcoming-Ansicht, automatischer Halbzeit-Scanner und Odds-History-Tabs
- optionale, rein mathematische Dynamic-Middle-Rescue-Schadensprofile
- mehrere Paper-Portfolios mit festen oder prozentualen Einsätzen
- globaler Paper-Kill-Switch, Portfolio-Filter, Entry-Regeln und HT-Einstiegsfenster
- unveränderlicher Paper-Einstiegssnapshot, idempotentes Bankroll-Ledger und Settlement
- Paper-Trading-Analytics, Calibration nach Tipico-P1 und CSV-Export
- automatische Geräteerkennung über Browser-User-Agent plus responsive Mobilansicht

Nicht enthalten sind eigene ML-Wahrscheinlichkeiten, andere Anbieter, FotMob,
WebSocket/STOMP, Inhaltsfilter, automatische Optimierung und echte Wettabgabe.

## Voraussetzungen

- Python 3.11 oder neuer
- Netzwerkzugriff auf sports.tipico.de
- kein Tipico-Konto erforderlich

Die Entwicklung wurde mit Python 3.12.10 geprüft.

## Installation und Start

PowerShell:

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
streamlit run app.py
~~~

Danach öffnet Streamlit die lokale URL im Browser.

Zeitstempel in der Oberfläche werden als `TT.MM.JJJJ HH:MM:SS` in der
Münchner Zeitzone (`Europe/Berlin`) angezeigt. Die unveränderten Tipico-
Zeitstempel bleiben zusätzlich in den Raw-Daten erhalten.

### Ein-Klick-Start unter Windows

Für den normalen Start genügt ein Doppelklick auf `START_TIPICO.bat` im
Projektordner. Das Skript verwendet automatisch die vorhandene Umgebung unter
`work\\v01-venv`, startet den Server im Hintergrund, wartet auf die Bereitschaft
und öffnet den Browser unter `http://127.0.0.1:8506`.

Ein erneuter Klick öffnet nur die bereits laufende Instanz. Mit
`STOP_TIPICO.bat` kann sie beendet werden. Start- und Fehlermeldungen liegen
unter `logs\\streamlit.out.log` und `logs\\streamlit.err.log`.

## Installation auf Proxmox

Das Projekt läuft in einer Debian-/Ubuntu-VM oder einem Debian-/Ubuntu-LXC
unter Proxmox. Auf dem Proxmox-Host selbst wird es nicht installiert.

~~~bash
apt-get update
apt-get install -y git ca-certificates
git clone https://github.com/lpzero13/Sportwetten.git /opt/tipico-observer
cd /opt/tipico-observer
bash deploy/install_proxmox.sh
~~~

Das Installationsskript richtet eine virtuelle Python-Umgebung ein, installiert
die Abhängigkeiten und aktiviert drei getrennte systemd-Dienste: Dashboard,
historischen Collector und Paper-Worker. Danach ist die Oberfläche unter
`http://<LXC-ODER-VM-IP>:8506` erreichbar.

~~~bash
systemctl status wetten-ui wetten-collector wetten-paper
journalctl -u wetten-ui -u wetten-collector -u wetten-paper -f
~~~

Die optionalen Einstellungen liegen nach der Installation in
`/etc/default/tipico-observer`. Für ein Update genügt anschließend ein
`git pull` mit Root-Rechten und ein erneuter Aufruf von
`bash deploy/install_proxmox.sh`.

## Bedienung

Die Navigation besteht aus **Live**, **Upcoming**, **Halftime Scanner**,
**Paper Trading** und **Data / Debug**. Die Live-Seite lädt den öffentlichen Livefeed standardmäßig
alle 10 Sekunden. Alle Wettbewerbe bleiben sichtbar; die Expander sind
standardmäßig geschlossen. Das Suchfeld filtert nur die Darstellung in der UI
und verändert den Collector nicht.

Liga und Land werden getrennt angezeigt. Tipicos `sportCompetitionMap` liefert
dafür den `parentName`; dieser wird nicht in den Ligatitel eingemischt, sondern
als `competition_country` am Event und als `country_or_region` in
`competitions` gespeichert.

Mit **Quoten** wird genau ein Event geöffnet und mit **Analyse** dieselbe
Detailansicht mit der normalisierten Auswertung. Erst dann wird der
Event-Detail-Endpunkt abgerufen. Die Detailansicht enthält die Tabs Analyse,
Alle Tipico Märkte, Odds History und Raw / Debug. Auto Refresh ist
standardmäßig deaktiviert und ruft bei Aktivierung nur dieses Event alle
5 Sekunden ab.

Der Halftime Scanner betrachtet ausschließlich aktuell in Halbzeit befindliche
Events. Ein frischer Collector-HZ-Snapshot wird wiederverwendet; ansonsten
werden nur die aktuellen HZ-Eventdetails geladen. Das Ranking ist ein
**Tipico Market Structure Ranking** nach P1-Puffer und sekundär Win-ROI, keine
Wettempfehlung.

Der Background Collector läuft separat von Streamlit:

~~~powershell
python scripts/run_collector.py --root .
python scripts/run_collector.py --root . --duration-minutes 60
python scripts/run_collector.py --root . --once
~~~

Er pollt den Livefeed alle 10 Sekunden und den kommenden Fußballtag über den
verifizierten Hour-Events-Endpunkt. Detailabrufe laufen mit maximal drei
konfigurierbaren Workern über eine Queue. Halbzeitdetails werden bei Erkennung,
nach 20 Sekunden und nach 60 Sekunden geladen. Vor dem Anpfiff werden, soweit
der Zeitpunkt noch nicht verpasst ist, T−60, T−15, T−5 und T−1 geplant.

Die Seite **Data Collection** liest ausschließlich SQLite und
`data/collector_status.json`; sie startet keinen Collector und entscheidet
nicht über die Datensammlung.

### Paper Trading

Paper Trading arbeitet vollständig ohne Wettschein und ohne Wettabgabe. Im
Dashboard können mehrere Portfolios angelegt, pausiert, archiviert und mit
Liga-/Länderzuordnung versehen werden. Die Regeln prüfen frische 0-/2+-Quoten,
P1, P1-Puffer, Win-ROI und das HT-Einstiegsfenster. Der Worker läuft getrennt
von Streamlit:

~~~powershell
python scripts/run_paper.py --root .
python scripts/run_paper.py --root . --once
~~~

Pro Event und Portfolio wird höchstens ein Einstieg für die Strategie erzeugt.
Quoten, Quotenalter, Markt-/Outcome-IDs, Score, Bankroll und Strategieversion
werden im `entry_snapshot_json` eingefroren. Beim Settlement wird ausschließlich
die zweite Halbzeit aus dem bestätigten Endstand berechnet; fehlende Endstände,
unbekannter Extra-Time-Scope und abgebrochene Spiele werden nicht als Gewinn
gezählt.

Die App erkennt Mobilgeräte automatisch über den Browser-User-Agent und nutzt
zusätzlich eine responsive Ansicht. Für einen manuellen Test kann `?view=mobile`
oder `?view=desktop` an die URL angehängt werden.

Pausierte oder quotenlose Outcomes werden als nicht verfügbar angezeigt. Eine pausierte Quote mit quoteFloatValue = 1.0 wird niemals als spielbare Quote behandelt.

## Konfiguration

Die Defaults stehen in config.py und können per Umgebungsvariable überschrieben werden:

| Variable | Default | Zweck |
|---|---:|---|
| LIVE_EVENT_REFRESH_SECONDS | 10 | Übersichtspolling |
| EVENT_MARKET_REFRESH_SECONDS | 5 | Detailpolling bei Auto Refresh |
| STORE_RAW_RESPONSES | true | Raw-Payloads schreiben |
| STORE_ODDS_HISTORY | true | Quotenänderungen historisieren |
| TIPICO_LANGUAGE | de | API-Sprache |
| TIPICO_LICENSE_REGION | DE | Lizenzregion |
| REQUEST_TIMEOUT_SECONDS | 10 | HTTP-Timeout |
| STALE_OVERVIEW_SECONDS | 30 | STALE-Grenze Übersicht |
| STALE_DETAIL_SECONDS | 15 | STALE-Grenze Eventdetails |
| COLLECTOR_FEED_REFRESH_SECONDS | 10 | Livefeed-Intervall |
| COLLECTOR_PREMATCH_REFRESH_SECONDS | 60 | Upcoming-Feed-Intervall |
| COLLECTOR_DETAIL_WORKERS | 3 | maximale parallele Eventdetails, max. 5 |
| COLLECTOR_CORE_REFRESH_SECONDS | 30 | HZ2-Core-Tracking |
| COLLECTOR_HALFTIME_DELAYS_SECONDS | 0,20,60 | Halbzeit-Phasen |
| COLLECTOR_STRATEGIC_MINUTES | 55,60,65,70,75,80,85,90 | gezielte Zeitpunkte |
| COLLECTOR_RETRY_DELAYS_SECONDS | 1,3,10 | Retry-Verzögerungen |
| MAX_LIVE_ODDS_AGE_SECONDS | 10 | Freshness-Grenze für Live-Quoten |
| DEFAULT_TOTAL_STAKE_EUR | 30 | Default-Einsatz für Szenarien |

Die verifizierten Endpunkte und Discovery-Ergebnisse stehen in outputs/DISCOVERY.md.

## Daten und Logs

- SQLite: data/tipico.db
- Raw-JSON: data/raw/YYYY-MM-DD/live/ und data/raw/YYYY-MM-DD/events/<event_id>/
- Halbzeit-Raw-JSON: .../events/<event_id>/halftime/
- Logdatei: logs/tipico.log

Raw-Payloads werden kanonisch gehasht. Identische Antworten erzeugen keine zweite Datei. Normalisierte Markt- und Outcome-IDs bleiben Strings und werden nie als Float behandelt.

Die Datenbank enthält die Tabellen `events`, `event_states`, `markets`,
`outcomes`, `odds_history`, `competitions`, `snapshots`, `market_presence`,
`canonical_outcomes`, `strategy_evaluations`, `paper_portfolios`,
`paper_portfolio_competitions`, `paper_trades`,
`paper_bankroll_transactions`, `paper_signal_log`, `paper_runtime_settings`
und `paper_worker_runs`. Der Raw-Layer wird nie
überschrieben. Canonical Outcomes tragen `normalizer_version = v0.3.1`;
Strategiezeilen werden nur bei relevanter Quote-, Quellen-, P1- oder
Statusänderung gespeichert. `market_presence` unterscheidet einen nicht
angebotenen Markt von einer angebotenen, aber pausierten Quote.

## Tests

~~~powershell
pytest -q
~~~

Die fachlichen V0.3-Tests decken Normalisierung, dynamische Score-Linien,
Äquivalenz-Scope, Best Odds, Stale-/Paused-Quoten, Probability Engine,
Inkonsistenz-Markierung, Strategy Engine, Cent-Rundung und Rescue-Arithmetik
ab. Die V0.4-Tests decken Portfolio-Regeln, Einsatzmodi, Bankroll-Grenzen,
Signalfilter, alle Settlement-Klassen, HT1:1/FT2:2, Idempotenz und Snapshot-
Unveränderlichkeit ab.

Der reproduzierbare Live-Smoke-Test ruft den aktuellen Feed und genau ein Eventdetail ab:

~~~powershell
python scripts/validate_v01.py --root work/validation-runtime
~~~

Das Ergebnis kann anschließend in outputs/V01_VALIDATION.md festgehalten werden.
Die abgeschlossene V0.1-Verifikation ist dort mit `TIPICO_V01_STATUS = PASS`
dokumentiert. Für V0.2 ist ein vollständiger Spieltag erforderlich; ein kurzer
Collector-Smoke-Test beweist noch keinen V0.2-DONE-Status.

## Troubleshooting

- Tipico aktuell nicht erreichbar: Netzwerk-/HTTP-Problem prüfen. Die letzte erfolgreiche Übersicht bleibt sichtbar und wird als veraltet markiert.
- Keine Live-Fußballspiele: Der Feed kann momentan leer sein; es wird nichts künstlich ergänzt.
- Schema parse warning: unbekannte oder fehlende Felder werden geloggt; unbekannte Market Types bleiben sichtbar.
- Streamlit startet nicht: virtuelle Umgebung aktivieren und python -m pip install -r requirements.txt erneut ausführen.

Das Projekt liest ausschließlich öffentliche Daten. Es öffnet keinen Wettschein, fügt keine Wette hinzu und übermittelt keinen Einsatz.
