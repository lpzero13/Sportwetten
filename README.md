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
- V0.5.8: atomare Feed-Reconciliation, Coverage-Cache, Queue-Diagnostik und
  capability-basierte Smart-Live-Universe-Priorisierung
- V0.5.9.1: integrierter FotMob-Produktionspfad mit sichtbarer Runtime-Matrix,
  Feature-Health, Resolver-/HT-Metriken, Deploy-Identität und Logrotate-Härtung
- V0.6.0: getrennte historische HT-ML-Research-Factory mit Leakage-Audit,
  kanonischem Parquet-Cache, Walk-forward-Validation, Registry, Resume und
  reproduzierbaren Reports

Nicht enthalten sind Live-ML-Wahrscheinlichkeiten oder eine automatische
Modellaktivierung, FotMob-Quoten,
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

## V0.6.0 Historical HT ML Research Factory

Die Research-Engine ist ein separater, ausdrücklich gestarteter Prozess. Sie
liest die vorhandenen FotMob-Parquet-Dateien read-only und erzeugt ihren
eigenen Cache, ihre Registry, Modelle und Reports. Installation, UI-Start,
Collector-Neustart und Container-Neustart starten kein Training.

~~~powershell
python -m research.ml_v060 audit
python -m research.ml_v060 build-dataset --workers 10
python -m research.ml_v060 run --mode local
python -m research.ml_v060 resume
python -m research.ml_v060 report
python -m research.ml_v060 export-models --top-k 5
~~~

`local` trainiert höchstens die zehn handverlesenen Startkonfigurationen.
`--max-experiments N` bedeutet immer bis zu `N` neue Experimente in diesem
Aufruf; bereits registrierte Konfigurationen werden dedupliziert. Für größere
explizite Läufe stehen beispielsweise `--mode standard --max-experiments 100`
oder `--mode deep --max-experiments 1000` zur Verfügung. Eigene YAML-/JSON-
Konfigurationen können mit `run --mode custom --config experiments.yaml`
gestartet werden. Der Locked-Testzeitraum bleibt für die spätere Shortlist
reserviert und wird im lokalen Lauf nicht zur Modellauswahl verwendet.

Die zentralen Ergebnisse liegen lokal unter
`research/cache/`, `research/ml_registry.sqlite`, `research/models/v060/` und
`research/output/v060/`; diese erzeugten Daten sind absichtlich nicht Teil des
Git-Repositories. Das Target sind ausschließlich reguläre Tore nach der
Halbzeit (`H2_GOALS_0`, `H2_GOALS_1`/`LOSS_MIDDLE`, `H2_GOALS_2_PLUS`). V0.6.0
beansprucht weder ROI noch eine Aktivierung für CT110 oder Paper Trading.

### V0.6.1 Research-Hardening

Die optionalen Research-Abhängigkeiten werden separat und reproduzierbar
installiert:

~~~powershell
python -m pip install -r requirements-research.txt
python -m research.ml_v060 preflight --container
python -m research.ml_v060 plan --mode deep --max-experiments 100
python -m research.ml_v060 run --mode deep --max-experiments 100
~~~

`preflight` führt für CatBoost einen echten Fit-, Predict-, Serialize- und
Reload-Smoke-Test aus. `plan` trainiert nicht. Standard/Deep starten bei
fehlender CatBoost-Fähigkeit oder verändertem Dataset-Hash nicht; ein
angefordertes optionales Modell wird niemals still durch ein anderes ersetzt.
Pro Experiment bleiben Registry-Metriken, Environment-/Dataset-Hashes und
OOF-Predictions erhalten. Große Modell-Binaries werden standardmäßig auf die
Top-2 je tatsächlich instanziierter Modellfamilie sowie explizit gepinnte
Experimente begrenzt (`V061_ARTIFACT_TOP_K_PER_FAMILY`,
`V061_PINNED_EXPERIMENTS`). Der Collector veröffentlicht zusätzlich Cache-,
Resolver-, WAL-, Transaktions- und Slow-Operation-Metriken; schwere Status-
Aggregationen laufen mit TTL und zeigen ihr `metric_age_seconds`.
Die Nachtlaufdateien `V061_STATUS.md` und `V061_OVERNIGHT_REPORT.md` werden im
Projektroot erzeugt. CT110-Canary und Collector-Beobachtung müssen dort
separat ausgeführt werden.

### V0.6.1.1 Production Hardening und ML-Run-Gates

V0.6.1.1 ergänzt die bestehende Research-Engine um einen diagnostizierbaren
exklusiven ML-Lock, Feed-Reconciliation, getrennte Heartbeat-/Full-Statuspfade,
den negativen FotMob-Resolver-Cache und eine install-time Deployment-Identität.
Installation, Neustart und `plan` trainieren weiterhin nicht automatisch.

Die Statuslatenz kann ohne Netzwerkzugriff reproduzierbar geprüft werden:

~~~powershell
python -m research.ml_v060 lock-status
python -m research.ml_v060 deployment-status
python -m research.ml_v060 status-benchmark --iterations 100
~~~

Der Proxmox-Installer erzeugt `DEPLOYMENT_MANIFEST.json` aus genau dem
installierten Quellbaum. Auf einem Produktionscontainer sind anschließend
`source_commit` und `source_tree_hash` mit `deployment-status --integrity`
prüfbar. Ein verwaister Lock wird nie blind gelöscht:

~~~bash
python -m research.ml_v060 clear-stale-lock
~~~

Der erste echte CT110-Lauf bleibt eine bewusste manuelle Sequenz. Ein
verifizierter Plan wird beim Training nicht neu erzeugt:

~~~bash
python -m research.ml_v060 deployment-status --integrity
python -m research.ml_v060 lock-status
python -m research.ml_v060 preflight --container
python -m research.ml_v060 canary --model catboost
python -m research.ml_v060 plan --mode deep --max-experiments 100
python -m research.ml_v060 verify-plan <RUN_ID>
python -m research.ml_v060 run --plan <RUN_ID>
python -m research.ml_v060 report --full-tests PASS
~~~

`canary` muss auf dem realen CT110-Dataset `requested=CATBOOST` und
`effective=CATBOOST` sowie Fit, Predict, Serialize, Reload und Predict nach
Reload nachweisen. Ein einzelnes fehlgeschlagenes Experiment wird als
`FAILED` registriert und kann weiterlaufen; strukturelle Fehler, kritischer
Datenträgerplatz oder ein ungesunder Collector stoppen den Lauf. Es gibt für
den ersten 100er-Lauf keinen Scheduler. `V0611_STATUS.md` und
`V0611_RUNTIME_REPORT.md` markieren nicht lokal beobachtbare CT110-/Live-
Canaries ausdrücklich als `PENDING` und behaupten keinen lokalen PASS.

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
Detailansicht mit der normalisierten Auswertung. **FotMob Live** ist im
Live-Überblick für ein ausgewähltes Event als Schaltfläche vorhanden. Wenn
noch kein bestätigter Link existiert, kann dort eine numerische FotMob-Match-ID
gezielt geprüft werden; diese einmalige Zuordnung bleibt für die laufende
Sitzung im RAM. Das Panel ruft ausschließlich das ausgewählte Match ab,
cached normalisierte Werte kurz im RAM und schreibt weder SQLite noch Parquet.
Die Detailansicht enthält die Tabs Analyse, FotMob Live, FotMob HT, Alle Tipico
Märkte, Odds History und Raw / Debug. Bei fehlenden Detailstatistiken zeigt
FotMob Live einen klaren No-Data-Hinweis und beendet die weitere Aktualisierung.
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

### Production Collector Hardening (V0.5.8)

Der erste gültige Livefeed nach einem Collector-Neustart wird gegen den
persistierten `current_event_state` reconciliiert. Fehlende vorher aktive
Events erhalten `NO_LONGER_LIVE`; ein finales Ergebnis wird dabei nicht
erfunden. Feed-Plausibility-Gate, eine gemeinsame SQLite-Transaktion für den
gesamten Feedbatch und idempotentes Schließen der aktuellen Quoten schützen
vor partiellen Zuständen. `event_states`, Odds-History, Snapshots, Outbox,
Raw-Daten und Parquet-Archive bleiben erhalten.

Der Status schreibt die Tages-Coverage standardmäßig höchstens alle 30 Sekunden
neu und zeigt das Cache-Alter transparent an. Zusätzlich werden `queue_due`,
`queue_future`, `oldest_due_age_seconds` und die Verteilung nach Snapshot-Typ
ausgegeben. WAL bleibt aktiv; der Collector wird nicht durch ein blindes
Erhöhen der Workerzahl oder durch langsamere globale Polls optimiert.

Der Smart-Live-Universe lässt alle Tipico-Spiele im globalen Feed sichtbar und
klassifiziert nur die teuren Detailpfade. Bekannte Jugend-/Reserve-/Friendly-
Wettbewerbe erhalten P4, unbekannte Liga-/Saison-Capabilities gehen über P2 in
kontrollierte Discovery, und nur nachgewiesene FotMob-/Tipico-Abdeckung erhält
P1. Frauenfußball wird nicht pauschal ausgeschlossen. Die Tabellen
`fotmob_coverage_catalog` und `tipico_market_capability` sind additive
Capability-Caches; historische Daten und das ausgewählte V0.5.7-FotMob-Live-
Panel bleiben unverändert.

### FotMob-Enrichment (V0.5.3 / V0.5.9.1)

FotMob ist in der privaten Standardkonfiguration aktiviert. Der integrierte
Tipico-Collector kann bei einer bestätigten Liga-/Team-/Kickoff-Verknüpfung
Pre-Match nur den kleinen Match-Index nutzen und beim ersten Tipico-HZ-Signal
genau einen öffentlichen `/api/data/matchDetails?matchId={id}`-Abruf auslösen. Der FotMob-Tab zeigt
die FirstHalf-Werte daneben. FotMob-Werte werden weder für das Tipico-
Market-Ranking noch für Paper-Trades oder Settlement verwendet.

Der Produktionspfad verlangt weiterhin die drei expliziten Gates
`FOTMOB_NETWORK_MODE=worker`, `FOTMOB_PROVIDER_DECISION=PRODUCTION_READY` und
`FOTMOB_AUTOMATED_USAGE=ACCEPTABLE_FOR_PROJECT`. Der Collector-Status zeigt
eine maschinenlesbare `feature_runtime_matrix` und sichtbare Warnungen, wenn
eine konfigurierte Funktion effektiv blockiert ist. Für ein einzelnes
ausdrücklich gewünschtes Match gibt es weiterhin `scripts/discover_fotmob.py`:

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

Für Proxmox ist `wetten-fotmob.service` vorhanden, bleibt aber bewusst
deaktiviert: Der integrierte `wetten-collector.service` ist der einzige
FotMob-Worker und verhindert doppelte Requests. Bei `LIMITED_USE`/`UNCLEAR`
bleiben automatische FotMob-Requests sichtbar blockiert; die manuelle
Datumsbereich-Auswahl und die Anzeige bereits gespeicherter Daten bleiben
verfügbar.

### Production Activation and Validation (V0.5.9.1)

`deploy/activate_fotmob.sh` setzt die Produktions-Gates in der vorhandenen
Konfiguration, sichert die alte Datei, deaktiviert den separaten FotMob-Service
und startet UI sowie Collector neu. `deploy/install_proxmox.sh` installiert
zusätzlich `/etc/logrotate.d/wetten` mit dem korrekten Dienstbenutzer. Der
Collector-Status enthält Version, Git-Commit/Branch, Dirty-State,
Konfigurations-Fingerprint, effektive FotMob-Schalter, Resolver-/Detail-/HT-
Zähler, Outbox-Alter und die Feature-Health.

Die lokale Test- und Providerprüfung kann den tatsächlichen CT110-Live-Canary
nicht ersetzen. Ohne Zugriff auf den privaten Proxmox-Container bleibt der
Release-Status deshalb `PARTIAL`, bis dort `wetten-collector.service`,
`systemctl is-active`, `logrotate -d` und ein echtes Live-Spiel geprüft wurden.
Der lokale Prüfrahmen ist über `scripts/validate_v0591.py` ausführbar; der
verbindliche Zwischenstand steht in `V0591_STATUS.md`:

~~~bash
python3 scripts/validate_v0591.py --run-tests --local-provider --live-canary
~~~

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

Die Jobs sind bewusst getrennt. Explizite CLI-/UI-Läufe können weiterhin
`FOTMOB_NETWORK_MODE=manual` verwenden. Der installierte Produktionspfad läuft
mit `worker` im integrierten Collector; der separate FotMob-Service wird nicht
aktiviert:

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

### Performance-Freigabe und Capability Audit (V0.5.6)

V0.5.6 entfernt den historischen globalen `0.5 req/s`-Default. Manuelle
Historical-Läufe starten standardmäßig mit `ADAPTIVE`, 5 req/s und zehn
Detail-Workern. Bei stabilen Fenstern wird in konfigurierten +5-req/s-Schritten
bis zum Maximum erhöht; bei 429/403, Fehler-, Timeout- oder Latenzproblemen
greift ein begrenzter Backoff mit Cooldown. Der Client verwendet eine langlebige
Session mit Keep-Alive, Kompression und einem konfigurierbaren Connection-Pool.
Provider-Schutz wird nicht umgangen.

Die drei abgeschlossenen Tage des kontrollierten Performance-Tests werden mit
demselben Client geladen. Jede Stufe schreibt ihre Messung in
`fotmob_performance_profile`; die UI zeigt Current/Effective RPS, Worker,
Requests, Erfolgsrate, 429, Retries, Median/P95 und den bekannten stabilen
Maximalwert. `SKIPPED_NO_HALFTIME` bleibt eine Datenqualitätsentscheidung und
ist kein Performancefehler. Der vollständige Audit steht in
`outputs/PROJECT_CAPABILITY_AUDIT.md`, der Messreport in
`outputs/V056_FOTMOB_PERFORMANCE_REPORT.md` und der Abschlussstatus in
`outputs/V056_STATUS.md`.

Beispiel für einen bewusst gestarteten Drei-Tage-Test:

~~~powershell
$env:FOTMOB_ENABLED="true"
$env:FOTMOB_HISTORY_ENABLED="true"
$env:FOTMOB_NETWORK_MODE="manual"
python scripts/fotmob_history.py performance --from-date 2026-08-26 --to-date 2026-08-28 --root .
~~~

### Max-Throughput- und Bottleneck-Probe (V0.5.6.1)

Der separate manuelle Max-Throughput-Test misst den realen Pfad oberhalb der
bisherigen 30-RPS-Grenze. Er verwendet exakt drei abgeschlossene Tage, 100
Detailanfragen je Stufe, eine 250er-Bestätigung der höchsten stabilen Stufe
und testet 30, 35, ... 60 sowie bei Stabilität 70, 80, 90 und 100 Ziel-RPS.
Dabei werden Rate-Slots, tatsächliche HTTP-Starts, Detail-/Parserzeit, CPU,
RSS, Worker und Connection-Pool protokolliert. Es gibt keinen Proxy-,
Fingerprint- oder Challenge-Bypass. Die Ergebnisse stehen in
`outputs/V0561_MAX_THROUGHPUT_REPORT.md`, `outputs/V0561_STATUS.md` und der
Tabelle `fotmob_performance_profile`.

Beispiel:

~~~powershell
$env:FOTMOB_NETWORK_MODE="manual"
python scripts/fotmob_history.py max-throughput --from-date 2026-08-26 --to-date 2026-08-28 --root .
~~~

Die aktuelle Messung bestätigte 100 Ziel-RPS ohne Provider- oder Parserfehler.
Der praktische Durchsatz liegt auf Windows wegen quantisierter Worker-/Timer-
Scheduling-Intervalle bei rund 63 effektiven Detailstarts/s; deshalb bleiben
10 Worker und ein Pool von 40 die Empfehlung. Der normal verwendete
`FOTMOB_MAX_RPS`-Default ist auf den bestätigten Wert 100 angehoben.

Der separate `wetten-fotmob.service` bleibt deaktiviert, damit kein dauerhafter FotMob-
Poller gestartet wird. Der technische Laufstatus des All-Leagues-Ausbaus wird
in `outputs/V055_STATUS.md`, der Fünf-Tage-Nachweis in
`outputs/V0551_FIVE_DAY_CANARY_REPORT.md` dokumentiert.

### Release Correctness und Provider-Linking (V0.5.9)

V0.5.9 schützt jeden Tipico-Livepoll vor strukturell verdächtigen Leer- oder
Teilfeeds, zentralisiert die Terminal-/Quotenabsicherung und lässt legitime
zukünftige Reschedules sowie glaubwürdige Live-Recovery weiterhin zu. Die
zusätzliche Tabelle `provider_event_links` hält Tipico- und FotMob-Identität,
Wettbewerb, Länder-/Teamnamen, Kickoff, Confidence, Methode und Status fest.
`EXACT`, `HIGH_CONFIDENCE` und `MANUAL` sind die einzigen automatisch
verwendbaren Linkstatus; `AMBIGUOUS`, `UNMATCHED` und `INVALIDATED` lösen keinen
automatischen Detailabruf aus.

Die Zuordnung nutzt den gecachten FotMob-Tagesindex inklusive angrenzender
UTC-Tage, grenzt zuerst auf Wettbewerb/Land ein und prüft anschließend
Heimteam, Auswärtsteam und Kickoff. Das Livepanel bleibt beim bestätigten
Link und speichert seine ungefähr zehnsekündigen Detailantworten weiterhin nur
im RAM. Halbzeit-Enrichment schreibt nur verwertbare
`Periods.FirstHalf`-Daten; fehlt die Struktur oder sind keine Werte nutzbar,
bleibt der maschinenlesbare Zustand `NO_HALFTIME` und es werden keine
Nullwerte oder leeren HT-Snapshots erzeugt. Der Implementierungs- und
Validierungsstand steht in `V059_STATUS.md`.

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
| STALE_PREMATCH_GRACE_HOURS | 6 | Grace Period, bevor vergangene Pre-Match-Zeilen stale werden |
| COLLECTION_METRICS_CACHE_TTL_SECONDS | 30 | TTL für Tages-Coverage im Collector-Status |
| COLLECTOR_SQL_TRACE_ENABLED | false | optionale SQL-/Commit-Messung für Benchmarks |
| MAX_LIVE_ODDS_AGE_SECONDS | 10 | Freshness-Grenze für Live-Quoten |
| DEFAULT_TOTAL_STAKE_EUR | 30 | Default-Einsatz für Szenarien |
| FOTMOB_ENABLED | true | FotMob-Funktion für integrierten read-only Produktionspfad |
| FOTMOB_API_BASE_URL | https://www.fotmob.com/api | API-Basis für den öffentlichen FotMob-Datenzugriff |
| FOTMOB_MATCH_DETAILS_PATH | /data/matchDetails?matchId={match_id} | öffentliches Matchdetail-JSON |
| FOTMOB_TIMEOUT_SECONDS | 10 | FotMob-Timeout |
| FOTMOB_MAX_RETRIES | 3 | FotMob-Retry-Anzahl, Delays 1/3/10 s |
| FOTMOB_MIN_REQUEST_INTERVAL_SECONDS | 1 | Legacy-Intervall nur für `FIXED`; historische Standardläufe nutzen adaptive RPS |
| FOTMOB_MATCHING_TOLERANCE_MINUTES | 15 | Kickoff-Matchingfenster |
| FOTMOB_POLL_SECONDS | 30 | optionaler Worker-Poll |
| FOTMOB_PROVIDER_DECISION | PRODUCTION_READY | explizite Providerentscheidung für den integrierten Worker |
| FOTMOB_AUTOMATED_USAGE | ACCEPTABLE_FOR_PROJECT | explizite Projektfreigabe für automatisierte read-only Nutzung |
| FOTMOB_LEAGUE_PATH | /leagues/{league_id} | öffentliche League-Seite für Discovery |
| FOTMOB_SEASON_PATH | /leagues/{league_id}?season={season_label} | öffentliche Fixture-/Result-Seite; Label wird als sichtbares `YYYY/YYYY` übergeben |
| FOTMOB_DAILY_MATCHES_PATH | /data/matches?... | vollständiger FotMob-Tagesfeed inklusive `includeNextDayLateNight=true` |
| FOTMOB_ALL_LEAGUES_PATH | /data/allLeagues?... | lokalisierter Länder-/Liga-Katalog |
| FOTMOB_DAILY_TIMEZONE | Europe/Berlin | Zeitzone für die Auswahl eines FotMob-Tages |
| FOTMOB_DAILY_CCODE3 | DEU | Länderparameter des Tagesfeed-Requests; der Feed liefert mehrere Länder |
| FOTMOB_DAILY_LOCALE | de | Sprache für Katalogbezeichnungen |
| FOTMOB_HISTORY_ENABLED | true | Historical-Netzwerkzugriff, zusätzlich zur Providerfreigabe |
| FOTMOB_NETWORK_MODE | worker | `off`, `manual` für explizite Läufe, `worker` für den integrierten Collector |
| FOTMOB_RATE_MODE | adaptive | `adaptive`, `fixed` oder `conservative` |
| FOTMOB_INITIAL_RPS | 5 | Startwert für adaptive Historical-Läufe |
| FOTMOB_RPS_STEP | 5 | RPS-Schritt beim gesunden Ramp-up |
| FOTMOB_MIN_RPS / FOTMOB_MAX_RPS | 0.5 / 100 | Adaptive Unter-/Obergrenze; 100 in V0.5.6.1 real bestätigt |
| FOTMOB_INITIAL_WORKERS / FOTMOB_MAX_WORKERS | 10 / 40 | konfigurierbare Historical-Workergrenzen |
| FOTMOB_RATE_WINDOW_REQUESTS | 20 | Auswertungsfenster der Adaptive Rate Control |
| FOTMOB_RATE_COOLDOWN_SECONDS | 5 | Cooldown nach Backoff |
| FOTMOB_CONNECTION_POOL_SIZE | 40 | Keep-Alive-Connection-Pool des HTTP-Clients |
| FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL | 25 | Detailanfragen je Probe-Stufe |
| FOTMOB_PERFORMANCE_WORKER_LEVELS | 10,20,30,40 | Worker-Benchmarkstufen |
| FOTMOB_PERFORMANCE_STABLE_CONFIRMATIONS | 2 | Anzahl stabiler Läufe für den bekannten Maximalwert |
| FOTMOB_HISTORY_WORKERS | 10 | Legacy-Alias; wird auf `FOTMOB_MAX_WORKERS` begrenzt |
| FOTMOB_HISTORY_REQUESTS_PER_SECOND | 5 | Legacy-Alias; neue historische Ratensteuerung verwendet `FOTMOB_RATE_MODE` |
| FOTMOB_HISTORY_TIMEOUT_SECONDS | 10 | Historical-HTTP-Timeout |
| FOTMOB_HISTORY_MAX_RETRIES | 3 | HTTP-Retries für transiente Fehler |
| FOTMOB_HISTORY_STALE_MINUTES | 30 | Rücksetzung verwaister IN_PROGRESS-Claims |
| FOTMOB_HISTORY_MAX_RETRY_ATTEMPTS | 3 | maximale Detailversuche je Match |
| FOTMOB_HISTORY_BATCH_SIZE | 100 | Parquet-Batchgröße |
| STORE_FOTMOB_HISTORICAL_RAW | false | optionales zstd-Raw nur für Historical-Details |
| FOTMOB_ARCHIVE_ROOT | leer | kanonischer FotMob-Parquet-Root, Proxmox: `/var/lib/wetten/archive/fotmob` |
| FOTMOB_HISTORY_LEAGUE_ID | 54 | Legacy-Fallback für die alten expliziten Liga-/Season-CLI-Befehle; die Datumsauswahl lädt alle Ligen |
| FOTMOB_HT_ENRICHMENT_ENABLED | true | separates Live-HZ-Enrichment; kein permanenter Historien-Poller |
| SMART_UNIVERSE_ENABLED | true | Capability-basierte Detailpfad-Auswahl bei vollständigem Tipico-Radar |
| SMART_UNIVERSE_CACHE_TTL_SECONDS | 300 | TTL des Smart-Universe-Catalogs |
| SMART_UNIVERSE_DISCOVERY_PROBE_SECONDS | 900 | Mindestabstand kontrollierter P2-Probes je Wettbewerb |
| FOTMOB_COVERAGE_MIN_SAMPLE_SIZE | 5 | Mindeststichprobe für FULL/NO_DATA |
| FOTMOB_COVERAGE_FULL_RATIO | 0.90 | Detailabdeckung für FULL |
| FOTMOB_COVERAGE_NO_DATA_RATIO | 0.10 | Detailabdeckung bis zu NO_DATA |
| TIPICO_MARKET_CAPABILITY_MIN_SAMPLE_SIZE | 5 | Mindeststichprobe für Markt-Capability |
| TIPICO_MARKET_CAPABILITY_MIN_RATIO | 0.50 | Mindestabdeckung der ZERO-/2+-Märkte |

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
`fotmob_daily_index`, `fotmob_daily_load_runs`, `fotmob_performance_profile`,
`fotmob_coverage_catalog`, `tipico_market_capability`, `provider_event_links`
und `match_data_quality`.
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
Strategie-Parquet. Die V0.5.6-Tests prüfen den thread-sicheren Rate-Controller,
Backoff/Ramp-up, persistierte Performance-Profile sowie den kontrollierten
Drei-Tage-Probeablauf.
Die V0.5.8-Tests prüfen Startup-Reconciliation nach frischem Service-Neustart,
Plausibility-Gate, vollständigen Batch-Rollback, terminale Quoten, Stale-
Pre-Match/Rescheduling, Coverage-Cache, Queue-Diagnostik und Smart-Universe-
Prioritäten.

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
