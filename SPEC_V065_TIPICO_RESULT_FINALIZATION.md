# V0.6.5 – Tipico Result Finalization & Historical Recovery

Stand: 07.09.2026. Umsetzungsspec auf Basis des lokalen V0.6.4-Codes.
Dieses Dokument beschreibt die nächste Implementierung; es führt keine Migration aus.

## 1. Ziel

Historische Spiele mit `ended`, `no_longer_live` oder widersprüchlichen Statusfeldern sollen einen konsistenten Abschlussstatus erhalten. Vorhandene belegte Endstände sollen in Backtests und Paper Trading tatsächlich nutzbar werden. Fehlende Endstände sollen aus gespeicherter Tipico-Evidenz oder über den vorhandenen FotMob-Backfill ergänzt werden.

Erfolg bedeutet mehr Spiele mit nachweislich gültigem Ergebnis und korrekt abgeleiteten Zielvariablen. Ein bloß höherer Zähler für `finished` ist kein Erfolgskriterium.

## 2. Befunde im aktuellen Code

- `services/collector.py`: `_is_finished()` erkennt `ended` bereits als terminal. `_persist_match_result()` schreibt aber nur bei passender Abschlussbeobachtung und vollständigem HT-/FT-Stand. Ein erkanntes Spielende garantiert daher keinen Eintrag in `match_results`.
- `storage/database.py`: `_mark_event_no_longer_live_locked()` erzeugt einen lokalen Zustand bei Verschwinden aus dem Livefeed. Dabei werden die zuletzt bekannten Tore übernommen. Dieser Zustand bestätigt keinen Endstand.
- `services/result_backfill.py`: Die Kandidatenauswahl berücksichtigt derzeit fehlende FT-Werte oder fehlende Ergebniszeilen. Vollständige Scores mit falschem Status, fehlendem HT, unklarem Scope oder inkonsistenten Ableitungen werden dadurch nicht vollständig erfasst.
- `tipico_research/source.py`: Der Backtest akzeptiert `ENDED` bereits. Er benötigt außerdem gültige Ergebnisse, passende Halbzeitstände und geeignete Einstiegsdaten. Die Ergebnisquelle wird bisher pauschal als `TIPICO_MATCH_RESULT` ausgegeben.
- `paper/results.py`: Auch hier werden Ergebniszeilen pauschal als Tipico-Quelle bezeichnet. Die vorhandene Ergebnisprüfung und Abrechnung müssen die neue Herkunft und Qualitätsbewertung berücksichtigen.

Diese Befunde stammen aus dem Code. Die Verteilung der Fälle in der aktuellen Container-Datenbank muss ein Audit ermitteln.

## 3. Verbindliche Grundregel

`ended` ist bei einer bestätigten Provider-Abschlussbeobachtung ein normalisierbares Synonym für `finished`.

`no_longer_live` bedeutet nur: Das Spiel wird nicht mehr im Livefeed geführt. Auch nach mehreren Wochen ist der dort hinterlegte Stand möglicherweise ein Zwischenstand. Alter löst eine Nachprüfung aus, ersetzt aber keinen Ergebnisbeleg.

Beispiele:

| Vorhandene Daten | Gewünschtes Ergebnis |
|---|---|
| Tipico meldet `ended`, FT 2:1, vollständige Abschlussbeobachtung | Kanonischer Spielstatus `finished`; Ergebnisqualität separat prüfen |
| `events` sagt `no_longer_live`, gespeicherter Tipico-Abschlussbeleg enthält FT 2:1 | Abschlussbeleg auswerten und aktuellen kanonischen Status reparieren |
| `no_longer_live`, letzter Stand 1:0 in Minute 73, Spiel vor zwei Wochen | `RESULT_PENDING`, zuletzt beobachtet 1:0; keine automatische FT-Übernahme |
| FotMob bestätigt passendes Spiel als regulär beendet, FT 2:1 | `finished`, FT 2:1 mit FotMob-Herkunft; HT separat validieren |
| Regulärer Endstand bekannt, HT fehlt | `finished`, Endstand anzeigen; keine H2-Zielvariable und kein H2-Settlement |
| Abbruch, Verlegung oder Absage | Eigenständiger Status; keine Umwandlung durch Zeitablauf |
| Zwei belastbare Quellen widersprechen sich | Konflikt sichtbar halten und automatische Verwendung sperren |

## 4. Drei getrennte Entscheidungen

Pro Event werden folgende Dimensionen unabhängig bestimmt:

1. **Spielstatus:** `SCHEDULED`, `LIVE`, `HALFTIME`, `FINISHED`, `CANCELLED`, `POSTPONED`, `ABANDONED`, `UNKNOWN`.
2. **Ergebnisstatus:** `VERIFIED`, `PARTIAL`, `PENDING`, `CONFLICT`, `UNAVAILABLE`.
3. **Verwendbarkeit:** FT-Auswertung, H2-Auswertung und Paper-Settlement erhalten getrennte Freigaben und Ablehnungsgründe.

Ein Spiel kann sicher beendet sein, obwohl sein Ergebnis noch nicht bekannt ist. Ein verifiziertes Ergebnis kann für H2 ungeeignet sein, wenn der Halbzeitstand fehlt oder dem eingefrorenen Einstieg widerspricht.

Die Datenbank verwendet für den kanonischen Abschluss weiterhin `finished` in Kleinschreibung. Rohstatus und ursprüngliche Beobachtungen bleiben als Evidenz erhalten.

## 5. Lokales Audit vor Änderungen

Ein schreibgeschützter Audit läuft auf einer konsistenten SQLite-Sicht einschließlich vorhandener WAL-Daten. `immutable=1` darf nur bei einer tatsächlich unveränderlichen, konsistenten Kopie verwendet werden; nicht für die laufende Container-DB.

Auswertung je `event_id`, nicht je Snapshot:

- Rohstatus-Verteilung in `events`, `current_event_state` und letzter `event_states`-Beobachtung;
- vorhandene `match_results`: Status, HT, FT, Quelle und Ergebnis-Scope;
- Abschlussbelege aus `event_states`, `FINAL`-Snapshots und vorhandenen Archiv-/Raw-Daten;
- Widersprüche zwischen Statusfeldern, Quellen und Scores;
- offene Paper-Trades und verfügbare HT-Einstiegsbeobachtungen;
- Fälle nach Land, Liga, Datum und Alter: unter 24 Stunden, 1–7 Tage, über 7 Tage;
- genaue Anzahl der allein durch Statusnormalisierung reparierbaren Spiele;
- Anzahl der aus lokaler Evidenz rekonstruierbaren Ergebnisse;
- Anzahl notwendiger FotMob-Prüfungen und nicht auflösbarer Fälle.

Ein Snapshot-Name `FINAL` allein reicht nicht. Seine Payload muss eine fachlich geeignete Abschlussbeobachtung enthalten. Ein mit Fallback-Daten erzeugter Snapshot darf keinen Zwischenstand in einen Endstand verwandeln.

## 6. Reihenfolge der Ergebnisermittlung

### 6.1 Bereits vorhandene Ergebniszeile prüfen

Zuerst `match_results` validieren. Ein belegtes `ended`/`final`/`completed` wird kanonisch `finished`. Ein vollständiger Score mit `no_longer_live` oder unbekannter Herkunft wird nicht allein aufgrund seiner Existenz bestätigt.

Prüfen: Identität, terminaler Spielstatus, Score-Gültigkeit, reguläre Spielzeit, HT-Konsistenz und abgeleitete Torzahlen. Ergebnis und Status müssen aus derselben zuordenbaren Beobachtung stammen oder ihre Zusammenführung muss ausdrücklich belegt sein.

### 6.2 Tipico-Historie rekonstruieren

Vorhandene Abschlussbelege zuerst lokal suchen. Tatsächlich bestätigte spätere Provider-Korrekturen dürfen frühere Ergebnisse revidieren, müssen aber eine neue nachvollziehbare Revision erzeugen.

Fehlende Halbzeitstände dürfen aus eindeutig zugeordneten Tipico-Halbzeitbeobachtungen ergänzt werden. Bei widersprüchlichen HT-Beobachtungen wird kein beliebiger Wert gewählt. Der beim Strategieeinstieg eingefrorene Halbzeitstand bleibt unverändert.

### 6.3 FotMob-Backfill verwenden

Den bestehenden Worker erweitern und keine zweite unabhängige Ergebnis-Pipeline aufbauen. Bestätigte Links bevorzugen, sonst Matching nach Teams, Heim/Gast, Anstoßzeit, Wettbewerb, Land sowie Jugend-/Reserve-/Frauenmerkmalen. Provider-IDs gehören unterschiedlichen Namensräumen an.

Detailantwort erneut auf Identität prüfen. Unsichere oder mehrdeutige Treffer bleiben offen. Tagesindex-Lücken und fehlgeschlagene Requests werden getrennt von einem bestätigten fehlenden Kandidaten behandelt. Veraltete oder unvollständige Index-Tage müssen gezielt aktualisierbar sein; allein eine existierende Zeile bestätigt keine vollständige Tagesabdeckung.

Ein Endstand ohne HT darf als verifiziertes FT-Ergebnis gespeichert werden, wenn Abschluss und Scope eindeutig sind. Für H2 bleibt das Spiel gesperrt, bis ein vertrauenswürdiger HT-Stand vorliegt.

### 6.4 Keine Auflösung möglich

`PENDING` mit Retry-Grund; nach konfigurierbaren erfolglosen Versuchen `UNAVAILABLE`. Dieser Status bedeutet Datenlücke, nicht Spielabsage oder Wettverlust. Erneute Prüfung bleibt bei neuen Links, Indexdaten oder explizitem Recheck möglich.

## 7. Ergebnisvalidierung und Zielvariablen

Scores müssen nichtnegative ganze Zahlen sein; `NULL` bleibt unbekannt. Boolesche Werte, Dezimalzahlen und unplausible Parserwerte werden abgelehnt.

Für ein gültiges H2-Ergebnis:

```text
FT_home >= HT_home
FT_away >= HT_away
H2_home = FT_home - HT_home
H2_away = FT_away - HT_away
H2_total = H2_home + H2_away
target = 0 | 1 | 2_PLUS
```

Die Differenz muss je Team stimmen; eine nichtnegative Gesamtsumme allein reicht nicht. Abgeleitete Werte werden aus den validierten Basiswerten neu berechnet, nicht unabhängig mit `COALESCE` aus möglicherweise widersprüchlichen Altwerten zusammengesetzt.

Reguläre Spielzeit inklusive Nachspielzeit, Verlängerung und Elfmeterschießen müssen unterschieden werden. Bei Verlängerung ist H2 nur nutzbar, wenn der 90-Minuten-Stand separat belegt ist. Eine leere Angabe zum Elfmeterschießen beweist für sich allein nicht, dass es keine Verlängerung gab. Unbekannter Scope erhält keine automatische strenge Ergebnisfreigabe.

`finished` allein schaltet weder Backtest noch Paper-Settlement frei.

## 8. Speicherung und Migration

Bestehende Tabellen und Provenienzfelder aus V0.6.4 weiterverwenden. Additive Migrationen müssen auf Alt-Datenbanken und bei wiederholtem Start funktionieren.

Benötigte logische Felder, soweit nicht bereits vorhanden:

- kanonischer Spielstatus und ursprünglicher Providerstatus;
- Ergebnisstatus und maschinenlesbare Ablehnungsgründe;
- `result_source`, `result_evidence_id`, `result_confidence`, `result_scope_status`;
- `result_revision`, `result_resolved_at`, `result_last_checked_at`;
- Quelle und Referenz der HT-Werte, getrennt von der FT-Quelle;
- `ended_at`, sofern tatsächlich bekannt, und separat `end_observed_at`;
- FT-/H2-Freigaben als zentrale Ableitung oder konsistente View.

Der Zeitpunkt der Nachpflege ist kein historischer Abpfiff. Er darf nicht als solcher für Spieltagsstatistiken oder zeitliche Backtest-Splits verwendet werden. Bestehendes `finished_at` muss hinsichtlich seiner bisherigen Beobachtungssemantik dokumentiert und mit Alt-Consumern kompatibel gehalten werden.

Evidenz revisionssicher speichern: Referenz auf vorhandenen Snapshot/State oder kompakter Providerbeleg mit ID, Zeiten, Status, Score, Scope, Hash und Regelversion. Nur ein Hash ohne rekonstruierbaren Beleg genügt nicht. Identische Wiederholungen erzeugen keine doppelten Ergebnisrevisionen.

Historische `event_states` und Snapshots werden nicht rückwirkend umetikettiert. Aktuelle Anzeige nutzt eine kanonische View oder explizit gekennzeichnete abgeleitete Felder. Rohbeobachtung und aufgelöstes Ergebnis müssen in der UI unterscheidbar bleiben.

Konflikte werden vor dem Schreiben geprüft. Bei nachträglich eintreffenden Tipico-Daten darf ein abweichender FotMob-Stand nicht still überschrieben werden. Die bestehende pauschale Überschreibelogik in `upsert_match_result()` muss dafür erweitert werden. Gleichlautende Bestätigungen dürfen Provenienz ergänzen; belegte Korrekturen erzeugen eine Revision.

Schreibvorgänge je Event sind atomar. Netzwerkzugriffe laufen außerhalb von Schreibtransaktionen. Parallel laufender Collector und Backfill müssen denselben aktuellen Ergebnisstand prüfen; dafür SQLite-Transaktionsgrenzen beziehungsweise Versionsprüfung verwenden. Bereits erfolgte Paper-Abrechnungen werden nicht still verändert.

## 9. Kandidatenauswahl und täglicher Betrieb

Der bestehende `wetten-result-backfill.timer` bleibt der tägliche Einstieg. Der Worker führt lokale Reparatur und anschließend die benötigten Provider-Prüfungen aus.

Kandidaten umfassen:

- vergangene Spiele ohne Ergebniszeile;
- Ergebniszeilen mit fehlendem HT/FT, ungültigen Ableitungen oder ungeklärtem Status;
- `ended`-Spiele mit belegtem Abschluss, aber fehlender kanonischer Normalisierung;
- `no_longer_live` mit inzwischen vorhandener Abschlussevidenz;
- bestehende `APPLIED`-Queue-Einträge, wenn ihre aktuelle Ergebnisqualität unzureichend ist;
- neue Evidenz zu früheren `UNAVAILABLE`-/Konfliktfällen.

Alter steuert Fälligkeit und Priorisierung. Standard: erste Provider-Nachprüfung frühestens drei Stunden nach Anstoß; bei fehlender Anstoßzeit nur lokale Prüfung beziehungsweise begründete manuelle Nachpflege. Abbruch/Verlegung gesondert behandeln.

Zehn Netzwerk-Worker und konfigurierbare Batchgröße bleiben erhalten. Neu eingegangene Kandidaten und bereits fällige Retries müssen fair bedient werden, damit alte erfolglose Fälle nicht den gesamten 500er-Batch besetzen. Lokale Statusreparaturen benötigen kein Netzwerkbudget.

Zusätzlich einen expliziten einmaligen Bestandslauf anbieten: alle zum Laufstart erfassten Kandidaten in fortsetzbaren Batches prüfen; nicht auf morgen verschobene Retries sofort erneut ausführen. Bei Abbruch fortsetzbar, bei Wiederholung idempotent. Auf dem Container darf nur ein solcher Ergebnislauf pro DB gleichzeitig aktiv sein.

Geplante CLI-Funktionen, genaue Namen bei Implementierung festlegen:

```text
audit                 Bestand und Reparaturpotenzial ohne Änderungen
reconcile --dry-run   konkrete Änderungen mit Belegen vorschlagen
reconcile --apply     fälligen Batch übernehmen
reconcile --all-due   gesamten fälligen Bestand begrenzt und fortsetzbar abarbeiten
recheck --event-id    einen bestimmten Fall einschließlich Sperrgrund erneut prüfen
```

Provider-Ausfälle oder gesperrte Konfiguration dürfen nicht als erfolgreicher vollständiger Lauf oder als `NO_CANDIDATE` ausgegeben werden.

## 10. Backtest und Paper Trading

- `tipico_research/source.py` und `paper/results.py` müssen die tatsächliche Ergebnisquelle und Evidenzrevision verwenden.
- FotMob darf die Ergebnislabels ergänzen. Tipico-Quoten, P1 und alle Strategie-Eingangsdaten bleiben die zum Entscheidungszeitpunkt verfügbaren Tipico-Daten.
- Ergebnisqualität zentral prüfen; Backtest und Paper Trading dürfen keine voneinander abweichenden Definitionen eines gültigen H2-Ergebnisses verwenden.
- Ergebnisabdeckung, HT-Abdeckung und vollständige Backtest-Verwendbarkeit separat ausweisen.
- Ein fehlendes Label erhält keine Klasse `0`, `1` oder `2_PLUS`. Auch bei ungültigem Ergebnis darf keine scheinbar gültige `outcome_class` exportiert werden.
- Studienlauf speichert Ergebnisquelle, Revision, Auflösungszeit, Dataset-Fingerprint und Label-Stichtag. Neue Ergebnisse erzeugen einen neuen Studienlauf; alte Reports bleiben reproduzierbar.
- Für zeitgetreue Paper-Simulation zählt ein Ergebnis erst ab seinem damaligen Bekanntwerden. Später ergänzte Labels dürfen retrospektiv Treffer bewerten, aber keine frühere Einsatzentscheidung beeinflussen.
- Offene Paper-Trades werden durch den normalen Settlement-Prozess verarbeitet. Bereits verbuchte Trades erhalten bei Konflikten einen Prüfbedarf; keine automatische Doppelbuchung oder rückwirkende Ledger-Änderung.

## 11. Oberfläche und Reports

Im Datenbereich eine kompakte Ergebnisübersicht:

- Spiele mit bestätigtem Abschluss;
- Spiele mit verifiziertem regulärem FT;
- Spiele mit gültigem HT+FT für H2;
- davon tatsächlich backtestfähige Spiele mit geeignetem Einstieg;
- offene Ergebnisse, Konflikte und endgültig nicht verfügbare Ergebnisse;
- zuletzt ergänzt, zuletzt geprüft und nächster geplanter Lauf.

Filter nach Datum, Land, Liga, Rohstatus, kanonischem Status, Ergebnisquelle und Auflösungsstatus. Je Spiel HT, FT, letzter beobachteter Stand, Quelle, Beleg und Ablehnungsgrund zeigen. Beispieltext: „Nicht mehr live · Endergebnis noch unbestätigt · letzter Stand 1:0“.

Reports: `RESULT_FINALIZATION_STATUS.md`, `RESULT_FINALIZATION_AUDIT.csv`, `RESULT_FINALIZATION_CHANGES.csv`, `RESULT_FINALIZATION_UNRESOLVED.csv`. Jede Zahl muss ihre Bezugsmenge nennen; FT-bestätigt und H2-backtestfähig dürfen nicht gleichgesetzt werden.

## 12. Verbindliche Tests und Abnahme

1. Bestätigtes `ended` normalisiert zu `finished`; ursprüngliche Beobachtung bleibt erhalten.
2. Wochenaltes `no_longer_live` mit Zwischenstand wird ohne Abschlussbeleg nicht übernommen.
3. Lokale bestätigte Abschlussbeobachtung rekonstruiert eine fehlende Ergebniszeile ohne Netzwerk.
4. `FINAL`-Snapshot mit reinem Fallback-/Zwischenstand wird abgelehnt.
5. Vollständiges Ergebnis mit falschem Status und bestehendem `APPLIED` bleibt reparierbar.
6. Verifiziertes FT ohne HT ist anzeigbar, aber erhält kein H2-Label und keine H2-Abrechnung.
7. FT kleiner als HT bei nur einem Team wird trotz positiver Gesamtdifferenz abgelehnt.
8. HT-Konflikt mit eingefrorenem Einstieg sperrt genau dessen H2-Auswertung.
9. Verlängerung, Elfmeterschießen, unbekannter Scope, Absage und Abbruch korrekt behandeln.
10. Mehrdeutiges Matching, falsches Land, vertauschte Teams und Jugend-/Reservevarianten nicht übernehmen.
11. Bestandslauf fortsetzbar; tägliche Retries fair; Netzwerkfehler bleiben unterscheidbar.
12. Wiederholter Lauf erzeugt keine Dubletten; gleichzeitiger Collector-Write verliert keine Evidenz.
13. Späte Provider-Korrektur erzeugt Revision/Prüfbedarf und keine zweite Ledger-Buchung.
14. Reale Ergebnisquelle wird im Backtest und Paper Trading korrekt angezeigt/exportiert.
15. Vorher-/Nachher-Audit auf konsistenter lokaler DB-Kopie, danach gesamter fälliger Bestandslauf. Nicht nur einen 50er-Test als vollständige Bestandsreparatur melden.

Abschlussbericht nennt: geprüfte Spiele, reine Statusreparaturen, neue Ergebnisse aus lokaler Tipico-Evidenz, neue FotMob-Ergebnisse, neue H2-Labels, neue vollständig backtestfähige Spiele sowie verbleibende Gründe. Keine Mindestzahl erfinden; der Gewinn richtet sich nach der tatsächlich vorhandenen Evidenz.

Container-Deployment dokumentieren und lokal testen. Ein Container-PASS darf erst nach dortigem beobachtetem Lauf gemeldet werden. Datenbanken und private Ergebnisexports bleiben außerhalb von Git.

## 13. Umfang

Enthalten: Audit, Statusnormalisierung, lokale Ergebnisrekonstruktion, Erweiterung des bestehenden Backfills, additive Migration, aktuelle Anzeige, Backtest-/Paper-Integration, Tests und nachvollziehbarer Bestandslauf.

Nicht Teil dieses Milestones: neue Wettstrategien, ML, Quote-/P1-Rekonstruktion aus späteren Daten, angenommene Endstände durch Zeitablauf oder automatische Änderung bereits abgeschlossener Paper-Buchungen.
