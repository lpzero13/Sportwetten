# V0.5.4.1 – FotMob Activation, Live Link & Data-Collection Fix

Stand: 31.08.2026, Europe/Berlin
Basis: `V054_STATUS.md` / Commit `3f1ffa1`

## Gesamtstatus

```text
FOTMOB_V0541_STATUS = PASS
DATA_COLLECTION_STATUS = FIXED
MANUAL_FOTMOB_HISTORY = ENABLED
AUTOMATED_FOTMOB_WORKER = OFF_BY_DESIGN
FULL_BUNDESLIGA_BACKFILL = NOT_RUN
```

## Fehlerbehebung Data / Debug

Der Data-Bereich stürzte bei einer leeren oder frisch initialisierten Datenbank
mit `KeyError: 'date'` ab. `collection_metrics_for_date()` berechnete den
UTC-Tag zwar intern, gab ihn aber nicht im Ergebnis zurück; die UI verwendete
anschließend `coverage["date"]`. Die Datenbankfunktion liefert den Schlüssel
jetzt immer. Der Data-Bereich zeigt dadurch auch ohne Collector-Daten eine
vollständige Storage-Übersicht mit Nullwerten.

## Änderungen seit V0.5.4

### FotMob-Aktivierung

- Die private Standardkonfiguration nutzt jetzt
  `FOTMOB_ENABLED=true`, `FOTMOB_HISTORY_ENABLED=true` und
  `FOTMOB_NETWORK_MODE=manual`.
- Der manuelle Von-/Bis-Lauf bleibt der einzige historische Netzwerk-Trigger.
- `STORE_FOTMOB_HISTORICAL_RAW=false` bleibt aktiv; die kanonischen Metriken,
  Periodenstatistiken, Schüsse und Events werden weiterhin vollständig in
  Parquet gespeichert.
- `deploy/activate_fotmob.sh` aktualisiert bestehende Container-Konfigurationen,
  legt vorher ein Zeitstempel-Backup an und startet nur das Dashboard neu.
- `wetten-fotmob.service` bleibt deaktiviert. Es wurde kein permanenter
  FotMob-Worker freigeschaltet.

### Live-Oberfläche

- Jede Live-Zeile enthält jetzt eine FotMob-Schaltfläche.
- Die Schaltfläche ist aktiv, wenn für das Event bereits ein FotMob-
  Current-State gespeichert ist; ohne Daten bleibt sie sichtbar, aber
  deaktiviert und erzeugt keinen Netzwerkabruf.
- Ein Klick öffnet die Eventdetails direkt im Tab `FotMob HT`.
- Gespeicherte FotMob-Daten bleiben auch bei deaktiviertem Netzwerkzugriff
  lesbar.
- Der blaue Hinweistext im Halftime Scanner wurde entfernt.

### Datenbereich und Tageslogik

- Der Von-/Bis-Bereich bleibt inklusiv und wird nach FotMob-UTC-Datum
  ausgewertet.
- Für die konfigurierte FotMob-Liga `54` werden alle Spiele jedes ausgewählten
  Tages indexiert und bei `fetch_details=true` einzeln vollständig verarbeitet.
- SQLite enthält den Tagesindex mit Datum, Land, Liga, Saison, Teams, Match-ID
  und Detailstatus; große Statistikdaten bleiben gemäß V0.5.4 im kanonischen
  Parquet-Archiv.

## Reale Validierung

Für den im UI verwendeten Zeitraum `2026-08-29` bis `2026-08-31`:

```text
Spiele im Tagesindex: 8
Aufteilung:           29.08. = 6, 30.08. = 2, 31.08. = 0
Detailabrufe:         8 angefordert, 8 erfolgreich
HTTP:                 10 x 200, 0 Fehler, 0 Retries, 0 x 429
Period-Stats:         888 Zeilen
Schüsse:              258 Zeilen
Events:               165 Zeilen
Liga/Land:            Bundesliga / Deutschland
Season:               40040 / 2026/2027
```

Der Test bestätigt damit, dass jeder Fixture des ausgewählten Zeitraums
geladen und mit seinen Detailmetriken archiviert wird.

## Tests

```text
67 passed, 1 skipped
```

Zusätzlich geprüft:

- Regressionstest für `coverage["date"]` bei leerer Datenbank.
- Streamlit-Test mit frisch leerer Datenbank: Data Collection rendert ohne
  Exception bis einschließlich `Storage Overview`.
- Regressionstest mit zwei Fixtures am selben Tag: beide werden indexiert,
  abgerufen und archiviert.
- Streamlit-UI-Test: FotMob-Button ist bei vorhandenen Daten aktiv, öffnet
  den Intent `fotmob` und bleibt ohne Daten deaktiviert.
- Python-Kompilierung und Bash-Syntax von beiden Proxmox-Skripten.

## Deployment bestehender Proxmox-Container

```bash
cd /opt/tipico-observer
git pull
sudo bash deploy/activate_fotmob.sh
```

Die vollständige historische Bundesliga-Verarbeitung über alle bekannten
Fixtures ist weiterhin nicht automatisch gestartet. Der aktuelle Tagesimport
ist auf FotMob-Liga 54 / Bundesliga begrenzt; andere Ligen bleiben außerhalb
dieses Meilensteins.
