# V0.5.9 Status

```text
V059_STATUS = PARTIAL

POST_STARTUP_EMPTY_FEED_PROTECTION = PASS
CENTRAL_TERMINAL_PROTECTION = PASS
V03_PERSISTENCE_FIX = PASS

PROVIDER_LINK_TABLE = PASS
DAILY_FOTMOB_FIXTURE_INDEX = PASS
COMPETITION_MATCHING = PASS
TEAM_NORMALIZATION = PASS
TEAM_ALIAS_MATCHING = PASS
KICKOFF_MATCHING = PASS
AMBIGUOUS_PROTECTION = PASS

PREMATCH_AUTO_LINK = PASS
LIVE_AUTO_LINK = PASS
SELECTED_MATCH_AUTO_LINK = PASS
HALFTIME_AUTO_LINK = PASS

FOTMOB_LIVE_AUTOMATIC = PASS
FOTMOB_LIVE_NO_PERSISTENCE = PASS
FOTMOB_HT_ENRICHMENT = PASS

ML_HT_READINESS = PASS

FULL_TEST_SUITE = PASS
```

## Validierungsbasis

Die lokale Prüfung wurde nach der letzten Änderung vollständig wiederholt:

- `python -m py_compile ...`: PASS
- `python -m pytest -q`: **113 passed, 1 skipped** in 47,03 Sekunden
- V0.5.9-Regressionen: **9 passed**
- `git diff --check`: PASS; die angezeigten LF/CRLF-Hinweise sind reine
  Zeilenende-Warnungen ohne Diff-Fehler
- Unabhängiger Read-only-Review der V0.5.9-Punkte: PASS

## Reale FotMob-Providerprobe

Am **02.09.2026** wurde der öffentliche FotMob-Tagesfeed für den 02.09.2026
read-only abgerufen und durch denselben Index-Parser verarbeitet:

```text
daily feed = PASS
fixtures extracted = 187
feed groups = 59
countries = 38
leagues = 54
next-day-late-night entries = 22
duplicates removed = 0
invalid entries = 0
payload bytes = 103911
response time = 272 ms
```

Ein realer Detail-Probeabruf für die aus dem Tagesfeed gelieferte Match-ID
`1000018859` war ebenfalls erfolgreich. Das Match enthielt verwertbare
FirstHalf-Daten:

```text
detail probe = PASS
league = Canadian Championship
country = CAN
FirstHalf = available
payload bytes = 946319
response time = 697 ms
```

Diese Providerproben schreiben nicht in die Projekt-Datenbank und sind kein
CT110-Produktivlauf.

## Reale Tipico-/Matching-Zählung

Die geforderten Produktionszählungen sind derzeit nicht verfügbar, weil CT110
aus dieser Umgebung nicht erreichbar ist und daher kein echter Tipico-Livefeed
beobachtet wurde. Es werden keine Werte erfunden:

```text
observed Tipico events = NOT_AVAILABLE
auto-linked events = NOT_AVAILABLE
EXACT count = NOT_AVAILABLE
HIGH_CONFIDENCE count = NOT_AVAILABLE
AMBIGUOUS count = NOT_AVAILABLE
UNMATCHED count = NOT_AVAILABLE
FotMob detailed-data count = NOT_AVAILABLE
```

## Umgesetzte Punkte

- Jeder Livefeed wird auch nach der Startup-Reconciliation auf Struktur,
  Inhalt und Event-Anzahl geprüft. Verdächtige Leer-/Teilfeeds lösen keine
  globale `NO_LONGER_LIVE`-Reconciliation aus.
- Upcoming-, Detail-, Livefeed- und Reconciliation-Pfade verwenden den
  zentralen Terminalschutz. `FINISHED`/`NO_LONGER_LIVE` öffnen weder alte
  Quoten noch alte Eventzustände wieder; zukünftige Reschedules und glaubhafte
  Live-Recovery bleiben möglich.
- `provider_event_links` wurde additiv eingeführt. Die Tabelle speichert die
  vollständige Tipico↔FotMob-Identität sowie Confidence, Methode, Status,
  Kickoffs und Revalidierungszeitpunkt. Alte V0.5.3-Links werden migriert.
- Der Resolver arbeitet mit einem gecachten all-league Tagesindex, prüft
  angrenzende UTC-Tage und schränkt Kandidaten zuerst nach Wettbewerb/Land
  ein. `matchDetails` wird nicht zur reinen Identifikation aufgerufen.
- Teamnamen werden zentral und Unicode-sicher normalisiert. Heim-/Auswärts-
  Reihenfolge, Länder-/Wettbewerbsscope und Kickoff-Toleranz sind weiterhin
  erforderlich; Tipico- und FotMob-IDs werden nicht direkt verglichen.
- Manuelle `INVALIDATED`-Entscheidungen können nicht durch einen späteren
  Tagesindex automatisch wiederbelebt werden. `AMBIGUOUS`, `UNMATCHED` und
  `INVALIDATED` lösen keinen Live- oder HT-Detailabruf aus.
- Das Livepanel nutzt bestätigte Links automatisch, bleibt ungefähr im
  10-Sekunden-Rhythmus und speichert Live-Statistiken ausschließlich im RAM.
- Halbzeit-Enrichment schreibt nur verwertbare `Periods.FirstHalf`-Daten.
  Bei fehlenden oder leeren FirstHalf-Daten bleibt der Zustand maschinenlesbar
  `NO_HALFTIME`; es gibt keinen leeren HT-Snapshot und keine künstlichen
  Nullwerte. `enhanced_ml_allowed` bleibt dann `false`.

## Noch offen

```text
CT110_DEPLOYMENT = NOT_VERIFIED
```

Nach dem Übertragen des Repository-Stands auf CT110 müssen dort der Collector,
die UI, ein echter Tipico-Livefeed und mindestens ein automatisches
Tipico↔FotMob-Linking zur Halbzeit verifiziert werden. Erst danach können die
Produktionszählungen oben belastbar ausgefüllt werden.
