# Tipico Market Intelligence Dashboard V0.5

Lokales, read-only Streamlit-Tool zur Beobachtung des öffentlichen Tipico-Live-Fußballfeeds
mit getrenntem Paper-Trading für die validierte `ZERO_OR_2PLUS`-Strategie.

Der aktuelle Projektumfang umfasst:

- alle aktuell gelieferten Live-Fußballspiele
- dynamische Wettbewerb-/Liga-Zuordnung
- Tipico-Land/Region je Wettbewerb, z. B. `Bundesliga · Deutschland` oder
  `Bundesliga · Österreich`, in Events und `competitions` persistiert
- Spielstand, Spielminute und Phase
- vollständige Märkte und Outcomes für genau ein geöffnetes Event
- flüchtiger Current State für Events, Markets und aktuelle Analyse
- lean historische Snapshots über eine SQLite-Outbox nach Parquet
- Raw-Tipico-JSON nur für Paper-Entries und Parser-/Mapping-Debug
- REST-Polling und System-/Debugansicht
- unabhängiger Background Collector für historische Snapshots
- Pre-Match-Discovery über den verifizierten Tipico-Hour-Events-REST-Pfad
- zehn fachliche Snapshot-Slots: PRE, HT, HT_STABLE, 60, 70, 80,
  FIRST_H2_GOAL_REOPEN, 85, 90 und FINAL
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
- optionales FotMob-Discovery, provider-neutrales Match-Matching und
  informatives Live-Enrichment ohne Einfluss auf Quoten, Strategie oder Paper Trading

Nicht enthalten sind eigene ML-Wahrscheinlichkeiten, FotMob-Quoten,
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
historischen Collector und Paper-Worker. Zusätzlich läuft ein täglicher
`wetten-cleanup.timer` für Debug-Raw, temporäre Archivdateien und exportierte
Outbox-Zeilen. Danach ist die Oberfläche unter
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
Detailansicht mit der normalisierten Auswertung. **FotMob** ist im
Live-Überblick als Schaltfläche vorhanden und wird aktiv, sobald für dieses
Event ein FotMob-Current-State gespeichert ist; dann öffnet sie direkt die
FotMob-Ansicht. Erst beim Öffnen des Events wird der Tipico-Detail-Endpunkt abgerufen. Die Detailansicht enthält
die Tabs Analyse, Alle Tipico Märkte, FotMob HT, Odds History und Raw / Debug.
Auto Refresh ist
standardmäßig deaktiviert und ruft bei Aktivierung nur dieses Event alle
5 Sekunden ab.
Dieser Refresh aktualisiert ausschließlich Current State; er erzeugt keinen
historischen Snapshot.

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
konfigurierbaren Workern über eine Queue. Historische Detailabrufe werden nur
bei den fachlichen Slots ausgelöst: einmal T−1, sofort bei Halbzeit, einmal
nach der HT-Stabilisierung, beim ersten Überschreiten von 60/70/80/85/90,
beim ersten wieder handelbaren Markt nach dem ersten HZ2-Tor und bei FINAL.
Die zehn Slots sind pro Event hart idempotent.

Die Seite **Data Collection** liest ausschließlich Current State, SQLite,
Parquet-Metadaten und `data/collector_status.json`; sie startet keinen Collector
und entscheidet nicht über die Datensammlung.

### FotMob-Enrichment (V0.5.3)

FotMob ist in der privaten Standardkonfiguration aktiviert, aber ausschließlich
für bewusst ausgelöste manuelle Läufe. Der
Tipico-Collector kann bei einer bestätigten Liga-/Team-/Kickoff-Verknüpfung
Pre-Match nur den kleinen Match-Index nutzen und beim ersten Tipico-HZ-Signal
genau einen öffentlichen `/api/data/matchDetails?matchId={id}`-Abruf auslösen. Der FotMob-Tab zeigt
die FirstHalf-Werte daneben. FotMob-Werte werden weder für das Tipico-
Market-Ranking noch für Paper-Trades oder Settlement verwendet.

Die Providerentscheidung bleibt `LIMITED_USE` bei `AUTOMATED_USAGE=UNCLEAR`.
Der automatische FotMob-Worker bleibt aus. Eine automatisierte HZ-Anreicherung benötigt
zusätzlich `FOTMOB_NETWORK_MODE=worker`,
`FOTMOB_PROVIDER_DECISION=PRODUCTION_READY` und
`FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT`. Ohne diese Freigabe läuft
der Tipico-Collector unverändert weiter. Für ein einzelnes ausdrücklich
gewünschtes Match gibt es `scripts/discover_fotmob.py`:

~~~powershell
$env:FOTMOB_ENABLED="true"
$env:FOTMOB_NETWORK_MODE="manual"
python scripts/discover_fotmob.py --root . --match-id 5881143
python scripts/run_fotmob.py --root . --once
~~~

Für einen bewusst freigegebenen Collector-HZ-Lauf:

~~~powershell
$env:FOTMOB_ENABLED="true"
$env:FOTMOB_NETWORK_MODE="worker"
$env:FOTMOB_PROVIDER_DECISION="PRODUCTION_READY"
$env:FOTMOB_AUTOMATED_USAGE="ACCEPTABLE_FOR_PROJECT"
python scripts/run_collector.py --root .
~~~

Für Proxmox ist `wetten-fotmob.service` vorhanden, wird vom Installationsskript
nicht aktiviert. Bei `LIMITED_USE`/`UNCLEAR` bleiben automatische FotMob-
Requests blockiert; die manuelle Datumsbereich-Auswahl und die Anzeige bereits
gespeicherter Daten bleiben verfügbar.

### FotMob Historical Foundation (V0.5.2)

V0.5.2 ergänzt die getrennte, resumierbare Historical-Schicht. Sie entdeckt
Liga-/Season-Metadaten, übernimmt ausschließlich echte Provider-Season-IDs,
indexiert Fixtures/Results und kann anschließend Matchdetails in eine
SQLite-Queue und ein flaches, zstd-komprimiertes Parquet-Archiv schreiben.
Die Queue kennt `NOT_FETCHED`, `IN_PROGRESS`, `FETCHED`, `PARTIAL` und
`FAILED`, führt Versuchs-/Fehlerdaten und setzt verwaiste Claims nach 30
Minuten zurück. Halbzeit- und Endstand werden getrennt gespeichert; fehlende
Werte bleiben `NULL`, und `second_half_goals` wird ausschließlich aus
`FT total - HT total` für gültige Scores berechnet.

Die Jobs sind bewusst getrennt. Standardmäßig ist
`FOTMOB_NETWORK_MODE=manual`; Netzwerkzugriff entsteht nur durch einen bewusst
manuell gestarteten Discovery-/Index-/Fetch-Job beziehungsweise den Button im
Dashboard. Der Modus aktiviert keinen dauerhaften Worker:

~~~powershell
# Bei einer expliziten Deaktivierung bleibt der Lauf ohne Netzwerkzugriff:
# FOTMOB_ENABLED=false, FOTMOB_HISTORY_ENABLED=false oder NETWORK_MODE=off.
python scripts/fotmob_history.py seasons --league 54 --root .
python scripts/fotmob_history.py index --league 54 --season 2025/26 --root .

# Die Variablen sind in der privaten Standardkonfiguration bereits gesetzt.
# Bei einer temporären Deaktivierung können sie für den Lauf wieder gesetzt werden.
$env:FOTMOB_ENABLED="true"
$env:FOTMOB_HISTORY_ENABLED="true"
$env:FOTMOB_NETWORK_MODE="manual"

# expliziter manueller Katalog-/Index-/Sample-/Detail-Lauf
python scripts/fotmob_history.py seasons --league 54 --root .
python scripts/fotmob_history.py index --league 54 --season 2025/26 --root .
python scripts/fotmob_history.py index --league 54 --season 2024/25 --root .
python scripts/fotmob_history.py sample --league 54 --season 2025/26 --matches 5 --root .
python scripts/fotmob_history.py fetch --league 54 --season 2025/26 --sample-only --root .
python scripts/fotmob_history.py status --league 54 --season 2025/26 --root .
~~~

Für reproduzierbare Offline-Tests akzeptieren die Discovery- und Indexjobs
zusätzlich `--payload <lokale-datei.json>`. Ein echter historischer PASS wird
nicht aus Fixtures simuliert; der aktuelle manuelle Real-Lauf ist in
`outputs/V052_STATUS.md`, `outputs/FOTMOB_BUNDESLIGA_DISCOVERY.md` und
`outputs/FOTMOB_BUNDESLIGA_SAMPLE.md` dokumentiert. Der dauerhafte Worker
verlangt weiterhin `FOTMOB_NETWORK_MODE=worker` sowie
`FOTMOB_PROVIDER_DECISION=PRODUCTION_READY` und
`FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT`.

### Historische Konsolidierung und HZ-Enrichment (V0.5.3)

V0.5.3 importiert die alte `sniper_football.db` ausschließlich read-only,
legt vor der Prüfung eine Kopie an und führt die Legacy-Zeilen über denselben
normalisierten Matchdetail-Parser. `LEGACY_IMPORT` und `FRESH_FETCH` werden
über FotMob-Match-ID und Source-Priorität dedupliziert; frische Detaildaten
ersetzen Legacy-Zeilen. Alte 60-Minuten-Werte bleiben als `m60_*` separat und
werden niemals als FirstHalf verwendet. Die Halbzeit-Features kommen nur aus
`content.stats.Periods.FirstHalf` und tragen `stats_period=FIRST_HALF`,
`source_context=LIVE_HT` sowie `captured_live=true`.

Die vollständige Prüfung kann isoliert und reproduzierbar erneut ausgeführt
werden:

~~~powershell
python scripts/run_v053_validation.py --root work/v053-validation --tipico-db data/tipico.db --output-dir outputs
~~~

Die Reports sind `outputs/LEGACY_FOTMOB_DATA_REPORT.md`,
`outputs/LEGACY_FOTMOB_SAMPLE.md`, `outputs/LEGACY_IMPORT_REPORT.md`,
`outputs/FOTMOB_TIPICO_MATCHING_REPORT.md`,
`outputs/FOTMOB_HT_ENRICHMENT_REPORT.md` und `outputs/V053_STATUS.md`.
Der Status bleibt `PARTIAL`, solange die bereitgestellte Tipico-Historie
weniger als die geforderten 20 echten deutschen Bundesliga-Events enthält;
fehlende Events werden nicht künstlich erzeugt.

### FotMob Canonical Archive und Tagesauswahl (V0.5.4)

V0.5.4 stellt im Bereich **Data / Debug → FotMob** ausschließlich die
Datumsauswahl **Von/Bis** bereit. Die Auswahl ist inklusiv und wird als
FotMob-Tagesdatum mit Zeitzone `Europe/Berlin` ausgewertet. Der Standardpfad
ruft pro Tag den vollständigen öffentlichen Tagesfeed ab: alle darin gelisteten
Länder, Ligen und Spiele werden unabhängig von ihrer Anstoßzeit indexiert.
Der optionale `includeNextDayLateNight`-Abschnitt wird ebenfalls übernommen.
Der alte `--league`-CLI-Pfad bleibt für gezielte Legacy-Läufe verfügbar.

Für jedes indexierte Spiel wird anschließend das Matchdetail gelesen. Fehlt
darin eine verwertbare `FirstHalf`-Statistik, erhält der Datensatz den Status
`SKIPPED_NO_HALFTIME` und wird nicht ins Detailarchiv geschrieben. Nur Spiele
mit Halbzeitdaten werden in der UI als Detailtabelle angezeigt. Datum, Land,
Liga, Saison, Anstoß, Teams, Status und FotMob-Match-ID liegen im kleinen
SQLite-Tagesindex; Halbzeitmetriken, Endstand, Schüsse, Events und weitere
Detaildaten liegen kanonisch in Parquet.

Die Saisonbezeichnung im Tagesfeed wird mangels Provider-Season-ID aus dem
Beobachtungsdatum als konventionelles Juli–Juni-Label (`2025/26`, `2026/27`, …)
abgeleitet und als Filter-/Archivschlüssel verwendet.

Pro Lauf werden auch Tage ohne Spiel als `fotmob_daily_load_runs` festgehalten;
die Laufzeile enthält außerdem Feed-Gruppen, Roh-Einträge, deduplizierte
Match-IDs, Next-Day-Einträge, entfernte Duplikate und die Zahl der bewusst
wegen fehlender FirstHalf-Daten übersprungenen Spiele. Ein erneuter Lauf ist idempotent für
frische Matchdetails, die deterministischen Match-Parquet-Dateien werden bei
einer echten Aktualisierung ersetzt. Die wichtigsten Dateien liegen unter:

~~~text
/var/lib/wetten/archive/fotmob/match_core/
/var/lib/wetten/archive/fotmob/period_stats/
/var/lib/wetten/archive/fotmob/shots/
/var/lib/wetten/archive/fotmob/events/
/var/lib/wetten/archive/fotmob/ht_snapshots/
/var/lib/wetten/archive/tipico/strategy/
~~~

Manuell per CLI ist derselbe Bereich so startbar:

~~~powershell
$env:FOTMOB_ENABLED="true"
$env:FOTMOB_HISTORY_ENABLED="true"
$env:FOTMOB_NETWORK_MODE="manual"
python scripts/fotmob_history.py dates --from-date 2025-08-22 --to-date 2025-08-31 --root .
# optional nur Datum/Land/Liga/Match indexieren:
python scripts/fotmob_history.py dates --from-date 2025-08-22 --to-date 2025-08-31 --index-only --root .
# V0.5.5.1-Report für den Fünf-Tage-Canary (mit CLI-Detailzusammenfassung):
python scripts/report_v0551_canary.py --from-date 2026-08-26 --to-date 2026-08-30 --root . --execution-summary outputs/V0551_DETAIL_RUN_SUMMARY.json
~~~

Die Container-Vorlage aktiviert genau diese manuellen Date-Jobs. Bei einem bereits
installierten Container übernimmt `deploy/activate_fotmob.sh` die
Schalter in `/etc/default/tipico-observer` und startet das Dashboard neu:

~~~bash
git pull
sudo bash deploy/activate_fotmob.sh
~~~

Der separate `wetten-fotmob.service` bleibt deaktiviert, damit kein dauerhafter FotMob-
Poller gestartet wird. Der technische Laufstatus des All-Leagues-Ausbaus wird
in `outputs/V055_STATUS.md`, der Fünf-Tage-Nachweis in
`outputs/V0551_FIVE_DAY_CANARY_REPORT.md` dokumentiert.

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
| STORE_RAW_RESPONSES | true | Legacy-Kompatibilität für Raw-Storage; kein Poll-Archiv |
| STORE_ODDS_HISTORY | true | Legacy-Kompatibilität für direkte Repository-Aufrufe |
| PERSIST_UI_REFRESH | false | Current-Analyse bei UI-Refresh optional persistieren |
| RAW_EVERY_POLL | false | niemals vollständige Payload je Poll archivieren |
| RAW_PAPER_ENTRY | true | Raw-Payload beim Paper-Entry dauerhaft archivieren |
| RAW_AT_HALFTIME | false | optionaler vollständiger HZ-Raw zusätzlich zum Snapshot |
| RAW_COMPRESSION | zstd | Raw-Kompression, Fallback gzip |
| PARQUET_COMPRESSION | zstd | Parquet-Kompression |
| DEBUG_RAW_RETENTION_DAYS | 7 | Aufbewahrung von Parser-/Mapping-Debug-Raw |
| WETTEN_ARCHIVE_PATH | data/archive | Parquet-Root, unter Proxmox `/var/lib/wetten/archive` |
| TIPICO_LANGUAGE | de | API-Sprache |
| TIPICO_LICENSE_REGION | DE | Lizenzregion |
| REQUEST_TIMEOUT_SECONDS | 10 | HTTP-Timeout |
| STALE_OVERVIEW_SECONDS | 30 | STALE-Grenze Übersicht |
| STALE_DETAIL_SECONDS | 15 | STALE-Grenze Eventdetails |
| COLLECTOR_FEED_REFRESH_SECONDS | 10 | Livefeed-Intervall |
| COLLECTOR_PREMATCH_REFRESH_SECONDS | 60 | Upcoming-Feed-Intervall |
| COLLECTOR_DETAIL_WORKERS | 3 | maximale parallele Eventdetails, max. 5 |
| COLLECTOR_CORE_REFRESH_SECONDS | 30 | Legacy-Kompatibilität |
| SNAPSHOT_HT_STABLE_DELAY_SECONDS | 45 | Verzögerung für HT_STABLE |
| SNAPSHOT_OUTBOX_EXPORT_INTERVAL_SECONDS | 300 | Parquet-Exportintervall |
| SNAPSHOT_OUTBOX_BATCH_SIZE | 100 | maximale Exportbatchgröße |
| SNAPSHOT_PRE_ENABLED ... SNAPSHOT_FINAL_ENABLED | true | einzelne historische Slots ein-/ausschalten |
| COLLECTOR_RETRY_DELAYS_SECONDS | 1,3,10 | Retry-Verzögerungen |
| MAX_LIVE_ODDS_AGE_SECONDS | 10 | Freshness-Grenze für Live-Quoten |
| DEFAULT_TOTAL_STAKE_EUR | 30 | Default-Einsatz für Szenarien |
| FOTMOB_ENABLED | true | FotMob-Funktion; Netzwerk wird nur durch manuelle Läufe genutzt |
| FOTMOB_API_BASE_URL | https://www.fotmob.com/api | API-Basis für den öffentlichen FotMob-Datenzugriff |
| FOTMOB_MATCH_DETAILS_PATH | /data/matchDetails?matchId={match_id} | öffentliches Matchdetail-JSON |
| FOTMOB_TIMEOUT_SECONDS | 10 | FotMob-Timeout |
| FOTMOB_MAX_RETRIES | 3 | FotMob-Retry-Anzahl, Delays 1/3/10 s |
| FOTMOB_MIN_REQUEST_INTERVAL_SECONDS | 1 | Mindestabstand zwischen FotMob-Requests |
| FOTMOB_MATCHING_TOLERANCE_MINUTES | 15 | Kickoff-Matchingfenster |
| FOTMOB_POLL_SECONDS | 30 | optionaler Worker-Poll |
| FOTMOB_PROVIDER_DECISION | LIMITED_USE | V0.5.1-Providerentscheidung; Worker benötigt PRODUCTION_READY |
| FOTMOB_AUTOMATED_USAGE | UNCLEAR | Nutzungsfreigabe; Worker benötigt ACCEPTABLE_FOR_PROJECT |
| FOTMOB_LEAGUE_PATH | /leagues/{league_id} | öffentliche League-Seite für Discovery |
| FOTMOB_SEASON_PATH | /leagues/{league_id}?season={season_label} | öffentliche Fixture-/Result-Seite; Label wird als sichtbares `YYYY/YYYY` übergeben |
| FOTMOB_DAILY_MATCHES_PATH | /data/matches?... | vollständiger FotMob-Tagesfeed inklusive `includeNextDayLateNight=true` |
| FOTMOB_ALL_LEAGUES_PATH | /data/allLeagues?... | lokalisierter Länder-/Liga-Katalog |
| FOTMOB_DAILY_TIMEZONE | Europe/Berlin | Zeitzone für die Auswahl eines FotMob-Tages |
| FOTMOB_DAILY_CCODE3 | DEU | Länderparameter des Tagesfeed-Requests; der Feed liefert mehrere Länder |
| FOTMOB_DAILY_LOCALE | de | Sprache für Katalogbezeichnungen |
| FOTMOB_HISTORY_ENABLED | true | Historical-Netzwerkzugriff, zusätzlich zur Providerfreigabe |
| FOTMOB_NETWORK_MODE | manual | `off`, `manual` für explizite CLI-/UI-Läufe, `worker` nur mit Worker-Gates |
| FOTMOB_HISTORY_WORKERS | 10 | Historical-Detailworker, maximal 10; globaler Request-Limiter bleibt aktiv |
| FOTMOB_HISTORY_REQUESTS_PER_SECOND | 0.5 | globaler Historical-Request-Limiter |
| FOTMOB_HISTORY_TIMEOUT_SECONDS | 10 | Historical-HTTP-Timeout |
| FOTMOB_HISTORY_MAX_RETRIES | 3 | HTTP-Retries für transiente Fehler |
| FOTMOB_HISTORY_STALE_MINUTES | 30 | Rücksetzung verwaister IN_PROGRESS-Claims |
| FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS | 3 | maximale Detailversuche je Match |
| FOTMOB_HISTORY_BATCH_SIZE | 100 | Parquet-Batchgröße |
| STORE_FOTMOB_HISTORICAL_RAW | false | optionales zstd-Raw nur für Historical-Details |
| FOTMOB_ARCHIVE_ROOT | leer | kanonischer FotMob-Parquet-Root, Proxmox: `/var/lib/wetten/archive/fotmob` |
| FOTMOB_HISTORY_LEAGUE_ID | 54 | Legacy-Fallback für die alten expliziten Liga-/Season-CLI-Befehle; die Datumsauswahl lädt alle Ligen |
| FOTMOB_HT_ENRICHMENT_ENABLED | true | separates Live-HZ-Enrichment; kein permanenter Historien-Poller |

Die verifizierten Tipico-Endpunkte stehen in outputs/DISCOVERY.md. Die FotMob-
Discovery, Abschlussvalidierung und Providerentscheidung stehen in
outputs/FOTMOB_DISCOVERY.md, outputs/FOTMOB_FINAL_VALIDATION.md,
outputs/FOTMOB_MATCHING_REPORT.md und outputs/FOTMOB_PROVIDER_DECISION.md.

### Storage-Migration und Cleanup

Bestehende SQLite-Snapshots können ohne Löschung exportiert und geprüft werden:

~~~powershell
python scripts/migrate_v042_storage.py --root .
python scripts/cleanup_storage.py --root .
~~~

Die Migration erzeugt `outputs/STORAGE_MIGRATION_REPORT.md` bzw. den angegebenen
Report. Der erste Lauf löscht keine SQLite-Historie. Der Cleanup entfernt nur
exportierte Outbox-Rows, temporäre Parquet-Dateien und abgelaufene Debug-Raw;
Paper-Trades, Settlements, Ledger, Match Results und Paper-Entry-Raw bleiben erhalten.

## Daten und Logs

- SQLite: data/tipico.db
- Parquet: data/archive/tipico/snapshots/year=YYYY/month=MM/date=YYYY-MM-DD/
- Tipico-Strategie-Parquet: data/archive/tipico/strategy/year=YYYY/month=MM/date=YYYY-MM-DD/
- FotMob-Parquet: data/archive/fotmob/snapshots/year=YYYY/month=MM/date=YYYY-MM-DD/
- FotMob-kanonisch: data/archive/fotmob/{match_core,period_stats,shots,events,ht_snapshots}/
- FotMob-Historical-Parquet: data/archive/fotmob/historical/league_id=.../season=...
- Raw-JSON: data/raw/YYYY-MM-DD/ (Debug und Paper-Entry, je nach Kompression)
- FotMob-Historical-Raw: data/raw/fotmob/historical/league_id=.../season=.../ (optional)
- Halbzeit-Raw: .../events/<event_id>/halftime/ nur bei `RAW_AT_HALFTIME=true`
- Logdatei: logs/tipico.log

Raw-Payloads werden kanonisch gehasht. Identische Antworten erzeugen keine zweite Datei. Normalisierte Markt- und Outcome-IDs bleiben Strings und werden nie als Float behandelt.

Die Datenbank enthält die Tabellen `events`, `event_states`, `current_event_state`,
`markets`, `outcomes`, `odds_history`, `competitions`, `snapshots`,
`snapshot_outbox`, `match_results`, `market_presence`, `canonical_outcomes`,
`current_canonical_outcomes`, `strategy_evaluations`,
`current_strategy_evaluations`, `paper_portfolios`,
`paper_portfolio_competitions`, `paper_trades`,
`paper_bankroll_transactions`, `paper_signal_log`, `paper_runtime_settings`
und `paper_worker_runs` sowie die optionalen V0.5-Tabellen `matches`,
`match_provider_links`, `teams`, `team_provider_aliases`,
`competition_provider_aliases`, `fotmob_current_state`, `fotmob_snapshots`,
`fotmob_snapshot_outbox`, `fotmob_seasons`, `fotmob_match_index`,
`fotmob_history_samples`, `fotmob_historical_archive_index` und
`fotmob_daily_index`, `fotmob_daily_load_runs`, `match_data_quality`.
`current_event_state`, `current_canonical_outcomes` und
`current_strategy_evaluations` sind ersetzbare Betriebsdaten. `snapshots` ist
die kurze SQLite-Staging-/Indexschicht; die historische Zeile wird als flache
Parquet-Zeile mit `schema_version` archiviert. `market_presence`, `odds_history`
und `canonical_outcomes` bleiben für Migration und Altbestände lesbar, werden
aber durch den V0.4.2-Collector nicht bei jedem Refresh erweitert.

## Tests

~~~powershell
pytest -q
~~~

Die fachlichen V0.3-Tests decken Normalisierung, dynamische Score-Linien,
Äquivalenz-Scope, Best Odds, Stale-/Paused-Quoten, Probability Engine,
Inkonsistenz-Markierung, Strategy Engine, Cent-Rundung und Rescue-Arithmetik
ab. Die V0.4-Tests decken Portfolio-Regeln, Einsatzmodi, Bankroll-Grenzen,
Signalfilter, alle Settlement-Klassen, HT1:1/FT2:2, Idempotenz und Snapshot-
Unveränderlichkeit ab. Die V0.4.2-Tests prüfen Current-State-Upserts,
Snapshot-Idempotenz, fachliche Trigger, FINAL-Ergebniszeilen, Outbox-/Parquet-
Export und die Trennung von UI-Refresh und Historie.
Die V0.5.2-Tests prüfen echte Season-ID-Übernahme ohne Hardcoding,
deduplizierte Matchindexierung, deterministisches Sampling, nullable
HT-/FT-Normalisierung, Zielklassen, Queue-Claims/Retry/Stale-Recovery,
Policy-Gating und resumierbare zstd-Parquet-Batches. Die V0.5.4-Tests prüfen
die kanonischen Core-/Period-/Shot-/Event-Datasets, die Datumsauswahl, Land-
und Liga-Metadaten, idempotente Wiederholung und das getrennte Tipico-
Strategie-Parquet.

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
