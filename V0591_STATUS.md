# V0.5.9.1 Status

Stand: 03.09.2026 · lokale Validierung unter Windows, CT110 von hier nicht erreichbar.

```text
V0591_STATUS = PARTIAL

NO_HIDDEN_FEATURE_GATES = PASS
FOTMOB_RUNTIME_EFFECTIVE = PASS
FOTMOB_DAILY_INDEX_PRODUCTION = PENDING
AUTO_LINK_PRODUCTION = PENDING
FOTMOB_LIVE_PRODUCTION = PENDING
FOTMOB_HT_PRODUCTION = PENDING
ML_HT_READINESS_PRODUCTION = PENDING
V03_PRODUCTION_PERSISTENCE = PASS
DEPLOYMENT_IDENTITY = NOT_VERIFIED
FEATURE_HEALTH_STATUS = PASS
LOGROTATE = NOT_VERIFIED
FULL_TEST_SUITE = PASS
CT110_DEPLOYMENT = NOT_VERIFIED
LIVE_E2E_CANARY = PARTIAL
```

## Implementiert und lokal verifiziert

- Die Default-Gates aktivieren den integrierten Collector-Pfad: `worker`,
  `PRODUCTION_READY`, `ACCEPTABLE_FOR_PROJECT`.
- `feature_runtime_matrix`, `feature_health`, sichtbare Runtime-Warnungen,
  effektive Konfiguration, Version/Git-Identität und Config-Fingerprint werden
  im Collectorstatus ausgegeben. Konfiguriert aber blockiert führt zu
  `*_DEGRADED` und einer Startup-Warnung.
- Der integrierte `wetten-collector.service` bleibt der einzige FotMob-Worker;
  `wetten-fotmob.service` wird durch Installation und Aktivierung deaktiviert.
- Daily-Index-, Resolver-, Detail- und HT-Zähler sind im Status unter
  `fotmob` verfügbar. HT-Zustände unterscheiden `AVAILABLE`, `NO_HALFTIME`,
  `NO_LINK` und `NOT_OBSERVED`; leere FirstHalf-Daten erzeugen keinen Fake-
  Snapshot.
- Die V0.3-Persistenzprüfung läuft über den Collector-Aufruf
  `_update_current_state()` und prüft zusätzlich SQL-Spalten, Platzhalter und
  Werteanzahl vor dem produktiven Insert.
- Halbzeit-Verschwinden und Wiederkehr werden gezählt; es wurde keine
  unbelegte Grace-Zeit eingeführt.
- Logrotate deckt den aktuellen Projekt-Log sowie den alten Pfad
  `/var/log/wetten/tipico.log` ab. Das Installationsskript korrigiert bei einem
  Upgrade dessen Verzeichnis- und Dateirechte. Es wurden keine Logs gelöscht.
- Ein Tipico-Feed ohne aktive Fußballspiele, der beispielsweise nur Tennis
  enthält, wird als sauberer Zustand akzeptiert. Ein fehlender Fußball-Bucket
  bei zuvor aktiven Fußballspielen bleibt aus Sicherheitsgründen blockiert.

## Lokale Nachweise

```text
FULL_TEST_SUITE: 123 passed, 1 skipped in 47.42s
compileall: PASS
bash -n deploy/activate_fotmob.sh: PASS
bash -n deploy/install_proxmox.sh: PASS
```

Real provider canary, nicht CT110:

```text
Datum: 2026-09-03
FotMob Daily Index: PASS
Requests: 1
Fehler: 0
Fixtures: 85
```

Real integrated local live canary, nicht CT110:

```text
Tipico Livefeed Requests: 1
Feed-Fehler: 0
Plausibilitätsfehler: 0
Aktive Fußballspiele: 0
Ergebnis: PENDING (zum Prüfzeitpunkt kein Live-Fußballspiel)
```

Diese Werte sind lokale Belege und keine Produktionszählungen.

## CT110 noch offen

CT110 wurde in dieser Umgebung nicht deployed und kann nicht erreicht werden.
Deshalb werden keine produktiven Event-, Link-, Detail-, HT- oder
`enhanced_ml_allowed`-Zählungen erfunden. Vor einem echten `PASS` müssen auf
CT110 mindestens Checkout/Commit, Runtime-Konfiguration, laufender Collector,
`/etc/logrotate.d/wetten`, ein echtes aktuelles Spiel und der vollständige
PREMATCH → LIVE → Auto-Link → FotMob Live → HALFTIME → FirstHalf → Export-
Nachweis geprüft werden.

Die lokale Identität zum Statuszeitpunkt war:

```text
app_version = 0.5.9.1
git_branch = main
git_commit = 984070cd5f0668a9162d0dacb6820817ecf4d634
working_tree_dirty = true
```

Der Commit ist der letzte vorhandene Repository-Stand; die V0.5.9.1-
Änderungen liegen lokal noch als Arbeitsbaumänderungen vor. Daher ist die
Deployment-Identität bewusst `NOT_VERIFIED` und der Gesamtstatus `PARTIAL`.
