# V0.6.5.1 – Full Database Result Recovery Status

Stand: 07.09.2026

## Übergabe

Der technische Full-Inventory-Lauf wurde implementiert und gegen die konkret
benannte lokale Tipico-Datenbank ausgeführt. Die technische Prüfung ist
abgeschlossen; das fachliche 90-%-Coverage-Ziel wurde mit den vorhandenen
Daten und der aktuellen Tipico↔FotMob-Abdeckung nicht erreicht.

## Umsetzung

- Event-Grain-Inventar mit genau einer Prüflistenzeile je Fußball-`event_id`;
  `N_all`, Reife-/Ausschlusskohorten und `as_of_utc` werden persistent
  gespeichert.
- Vollständiger lokaler Ergebnispass einschließlich bereits gespeicherter
  Ergebniszeilen; FT- und H2-Freigabe werden getrennt neu bewertet.
- Fehlende Kickoff-Zeit wird anhand alter dauerhafter Beobachtungen separat
  als historisch eingestuft, ansonsten als `AGE_UNKNOWN` ausgewiesen.
- FotMob-Tagesindex kann explizit aufgefrischt werden; ein frischer leerer
  bzw. vollständiger Tag entfernt veraltete Cachezeilen für diesen Tag,
  ohne bereits verifizierte Tipico-Ergebnisse zu löschen.
- Providerdetails werden dedupliziert mit zehn Workern verarbeitet. Provider-
  Tage, HTTP-/Parserstatus, Payload-Hashes, Evidenz, Matchingstatus, Konflikte
  und Resume-Heartbeat werden gespeichert.
- Resultat-Enrichment, Konflikte und spätere Tipico-/Provider-Korrekturen
  erhöhen `result_revision` monoton und schreiben eine Lineage-Zeile.
  Gleichlautende Wiederholungen bleiben idempotent.
- Ein Auditfehler im ersten Real-Report wurde nachträglich korrigiert: Die 421
  bereits vorhandenen, fachlich durch FotMob angereicherten Ergebniszeilen
  tragen jetzt Revision `1`; die zugehörigen Item- und Lineage-Zeilen stimmen
  überein. Die Korrektur wurde gegen den Preflight-Backup abgegrenzt und mit
  `quick_check` erneut verifiziert.
- SQLite-Backup erfolgt über die native Backup-API. UI-Explorer filtert im
  vollständigen Bestand und paginiert erst nach dem Filter.
- Containerbetrieb: täglicher `wetten-result-backfill.timer` bleibt der
  laufende Nachpflegepfad. Für den initialen Vollbestand wurde zusätzlich
  `wetten-result-recovery.service` als manuell startbare, nicht automatisch
  aktivierte Unit ergänzt. Der Lauf nutzt dieselbe DB-Sperre und ist resume-
  fähig.

## Reale lokale Ausführung

Quelle:

```text
C:\Users\chris\Documents\Codex\2026-08-29\es-x20\Tipico DB\tipico.db
```

Die Quelldatei wurde nicht durch eine Arbeitskopie ersetzt. Vor dem Schreiben
wurde eine konsistente Sicherung angelegt:

```text
C:\Users\chris\Documents\Codex\2026-08-29\es-x20\work\v0651-target-recovery\c7805481-81bd-4f24-96d9-617f1578fc1e\backup\tipico-preflight.db
```

Der Ziel-Run ist:

```text
c7805481-81bd-4f24-96d9-617f1578fc1e
```

`as_of_utc`: `2026-09-07T15:02:11.127329+00:00`

Technischer Laufstatus: `COMPLETED`

SQLite `quick_check`: `ok`
Arbeitskopie und Ziel-DB wurden vor beziehungsweise nach dem Schreiben
separat geprüft.

## Gemessene Zielabdeckung

| Kennzahl | Ergebnis |
|---|---:|
| N_all | 2.318 |
| N_historical | 2.126 |
| N_not_due | 192 |
| N_age_unknown | 0 |
| N_excluded | 1 |
| N_eligible | 2.125 |
| Lokale Prüfung | 2.126 / 2.126 = 100 % |
| Vollständige Prüflistenbearbeitung | Ja |
| FT vor → nach | 421 → 865 |
| FT-Abdeckung brutto | 865 / 2.126 = 40,6867 % |
| FT-Abdeckung | 865 / 2.125 = 40,7059 % |
| H2 vor → nach | 421 → 864 |
| H2-Abdeckung | 864 / 2.125 = 40,6589 % |
| Geeignete H2-Entries | 1.436 |
| H2-Entry-Labels | 627 / 1.436 = 43,6629 % |
| Backtest-fähige Events nach Research-Regeln | 627 |

Damit fehlen bis 90 % exakt 1.048 FT-Ergebnisse und 666 H2-Entry-Labels.
Die Zielstatus lauten beide `NOT_MET`. Die 1.260 nicht FT-freigegebenen
eligible Events bleiben in der Bezugsmenge; fehlende Providerkandidaten und
Matchingkonflikte wurden nicht aus dem Nenner entfernt.

## Provider- und Laufmessung

- Zehn FotMob-Tagesindizes wurden real über das Netzwerk geladen; alle zehn
  meldeten `COMPLETE` mit HTTP 200.
- 339 Netzwerkrequests, davon 329 Detailabrufe; HTTP-Erfolgsquote 100 %,
  keine 403/429/5xx/Timeouts und keine Retries.
- 286 reguläre FotMob-Ergebnisse wurden übernommen. 579 lokale Ergebniszeilen
  wurden im Lauf geprüft beziehungsweise nach strenger Regel neu geschrieben.
- Für alle 286 providerseitigen Übernahmen existiert jetzt zusätzlich eine
  laufbezogene `RESULT_INSERTED_BY_PROVIDER`-Lineage; zusammen mit den 579
  lokalen Änderungen enthält der Lauf damit 865 Änderungszeilen.
- Ziel-DB-Queue nach dem Lauf: 578 bestehende/verifizierte lokale Fälle,
  286 gültige FotMob-Anwendungen, 1.073 `NO_CANDIDATE`, 145 `UNMATCHED` und
  43 `IDENTITY_CONFLICT`.
- Provider offen nach dem Lauf: **0**; technisch blockiert: **0**. Die offenen
  historischen Fälle sind fachlich bearbeitet, aber überwiegend wegen
  `NO_CANDIDATE`, `UNMATCHED` oder Identitätskonflikten ungelöst.
- Gesamtlaufzeit inklusive Preflight/Backup: 42,074 Sekunden; Detailphase
  effektiv rund 17,18 Requests/Sekunde.
- Eine geschichtete Stichprobe von 30 übernommenen Ergebnissen (Tipico,
  lokale Evidenz und FotMob, mehrere Länder/Ligen) sowie zehn offenen/
  abgelehnten Fällen wurde gegen Score, Scope, Freigabe, Evidenz-ID,
  Identitäts-/HTTP-Status und Revision geprüft: `PASS`, 0 Fehler.

## Nutzbare Folgedaten

Für die nachgepflegte Ziel-DB wurde der Tipico-Research-Datensatz neu erzeugt:

```text
C:\Users\chris\Documents\Codex\2026-08-29\es-x20\work\v0651-target-research-dataset\tipico-dataset-20260907T150327Z
```

Ergebnis: 1.640 Beobachtungen, davon 695 mit aufgelöstem validem Ergebnis.
Die Recovery-Abdeckung `N_backtest=627` bleibt strenger und verlangt zusätzlich
die tatsächlichen Entry-/Evidence-Regeln.

## Reports

Alle Reports des Ziel-DB-Laufs liegen unter:

```text
C:\Users\chris\Documents\Codex\2026-08-29\es-x20\work\v0651-target-recovery\c7805481-81bd-4f24-96d9-617f1578fc1e
```

Enthalten sind Audit, vollständige Change-/Unresolved-Liste, Coverage nach
Datum/Land/Liga/Alterskohorte, Provider-Tage, Manifest, maschinenlesbarer
Status und Validierungsbericht. Die zusätzliche Belegstichprobe steht in
`RECOVERY_SAMPLE_CHECK.json`.

## Tests und Grenzen

- Fokussierte Finalisierung/Backfill/Recovery-Regression: **18 passed**.
- Enthalten sind unter anderem 1.201 Eventzeilen über mehrere logische
  Batches, Providerblockade/Resume, Cache-Auffrischung, Filter jenseits der
  ersten Seite, fehlende Kickoff-Zeit sowie Revisions-/Konflikt-Idempotenz.
- Vollständige projektweite Testsuite mit der Projektumgebung: **230 passed,
  1 skipped**. `git diff --check`: **PASS**; geänderte Python-Module:
  `py_compile` **PASS**.
- Der Proxmox-Container ist von dieser Umgebung nicht erreichbar. Deshalb ist
  dort kein PASS behauptet. Nach dem Quellupdate ist auf dem Container die
  manuelle Unit `systemctl start wetten-result-recovery.service` beziehungsweise
  die dokumentierte CLI-Sequenz auszuführen und anhand der Reports zu prüfen.
- Dieser Status dokumentiert keinen GitHub-Push; die Veröffentlichung ist ein
  separater, ausdrücklich zu beauftragender Schritt.
