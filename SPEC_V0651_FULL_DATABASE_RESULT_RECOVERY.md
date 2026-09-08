# V0.6.5.1 – Full Database Result Recovery & Verified Coverage

Stand: 07.09.2026

## 1. Auftrag und gewünschtes Ergebnis

Den vollständigen historischen Tipico-Bestand auf gültige Ergebnisse prüfen, belegbare Ergebnisse rekonstruieren beziehungsweise über FotMob ergänzen und die gewonnenen Labels tatsächlich für Backtests und Paper Trading nutzbar machen.

Dieser Auftrag umfasst ausdrücklich die Implementierung **und die reale Ausführung gegen den vollständigen lokalen Bestand**. Eine fertige CLI, bestandene Unit-Tests, ein Dry-Run oder ein kleiner Canary allein erfüllen den Auftrag nicht.

Der Nutzer benötigt keine künstliche Vollständigkeit von 100 %. Das angestrebte Ergebnis sind mindestens **90 % verifizierte reguläre Endstände im definierten historischen Bestand** und mindestens **90 % nutzbare H2-Ergebnislabels für Spiele mit einem bereits vorhandenen geeigneten Tipico-Halbzeit-Einstieg**. Beide Kennzahlen müssen getrennt ausgewiesen werden. Die Vollständigkeit der eigentlichen Prüfung muss 100 % der festgelegten Prüfliste erreichen.

Die 90 % sind ein zu überprüfendes Projektziel, keine vorab garantierte Providerabdeckung. Bei Nichterreichen müssen tatsächliche Ursachen, fehlende Spielzahlen und konkrete nächste Maßnahmen dokumentiert werden. Fehlende Ergebnisse dürfen nicht erfunden und Qualitätsanforderungen nicht abgesenkt werden, um das Ziel rechnerisch zu erfüllen.

## 2. Ausgangslage und bekannte Grenzen von V0.6.5

Lokales Projekt:

```text
C:\Users\chris\Documents\Codex\2026-08-29\es-x20
```

Zu prüfende lokale Quelldatenbank:

```text
C:\Users\chris\Documents\Codex\2026-08-29\es-x20\Tipico DB\tipico.db
```

Belege im bestehenden Projekt:

- `SPEC_V065_TIPICO_RESULT_FINALIZATION.md`
- `V065_RESULT_FINALIZATION_STATUS.md`
- `work/result-finalization-full-20260907/reports/latest.json`
- `services/result_finalization.py`
- `services/result_backfill.py`
- `scripts/result_finalization.py`
- `storage/database.py`
- `paper/results.py`
- `tipico_research/source.py`

Der vorhandene Auditbericht nennt 2.318 Fußball-Events. Der bisherige Bestandsversuch meldet `CACHED_ONLY`, einen Batch und 500 lokal ausgewählte Kandidaten. Davon wurden 158 lokale Ergebnisreparaturen gemeldet. Das ist kein vollständiger Provider-Prüflauf. `applied_total=500` bezeichnet dabei auch Verwaltungs-/Statusoperationen und darf nicht als 500 gewonnene Endstände verstanden werden.

Die bestehende Testsuite wurde zuletzt mit 224 erfolgreichen Tests und einem übersprungenen Test gemeldet. Diese Zahl beweist weder die vollständige Bestandsverarbeitung noch 90 % Ergebnisabdeckung.

Diese historischen Zahlen sind eine Ausgangsbasis. Vor der Umsetzung tatsächlichen Code, DB-Schema, Dateipfad, Bestandsumfang und vorhandene Änderungen erneut prüfen; keine Zahlen ungeprüft als aktuelle Messung übernehmen.

## 3. Verbindlicher Umfang

Enthalten sind:

1. Bestandsaudit und feste Prüfliste über alle gespeicherten Fußball-Events.
2. Prüfung auch bereits vorhandener Ergebniszeilen.
3. Vollständige lokale Rekonstruktion unabhängig von Providerverfügbarkeit.
4. Echte FotMob-Abfragen für verbleibende historische Fälle aller Länder und Ligen.
5. Sichere Ergebnisvalidierung, Provenienz, Konflikte und Revisionen.
6. Fortsetzbarer Lauf mit sichtbarem Fortschritt und vollständigen Reports.
7. Reale Vorher-/Nachher-Prüfung zunächst auf einer konsistenten Kopie.
8. Nach erfolgreicher technischer Validierung Übernahme in die ausdrücklich genannte lokale Datenbank mit Sicherung und Nachkontrolle.
9. Containerfähiger Betrieb und Integration in den vorhandenen täglichen Timer.
10. Verifikation der tatsächlich verwendeten Backtest- und Paper-Settlement-Pfade.

Nicht enthalten: neue Wettstrategien, ML, FotMob-Statistik-Vollimport, Rekonstruktion früherer Quoten aus späteren Daten, echte Wettabgabe oder automatische Neuverbuchung bereits abgeschlossener Paper-Trades.

Die aktuelle Erstellung dieses Dokuments implementiert oder startet diese Arbeiten noch nicht. Bei späterer Beauftragung der Umsetzung gehören die beschriebenen lokalen Datenbankprüfungen und die gesicherte Nachpflege zum Auftrag.

## 4. Messbare Bezugsmenge und 90-%-Ziel

### 4.1 Fester Stichtag und Spiel-Grain

Beim Laufstart `as_of_utc`, Regelversion, Konfigurations-Fingerprint, Codeversion und Datenbankidentität speichern. Die Prüfliste enthält jedes Fußball-Event genau einmal je `event_id`. Snapshots, Quoten und Provideranfragen sind keine zusätzlichen Spiele.

Jedes vorhandene Event erhält eine dokumentierte Einordnung, auch wenn es noch nicht fällig ist. Später hinzukommende Spiele dürfen den Nenner dieses Laufs nicht verändern. Sie werden vom täglichen Betrieb oder einem neuen Lauf übernommen.

### 4.2 Historischer Bestand

Für diesen einmaligen Bestandslauf gilt standardmäßig eine Reifezeit von 24 Stunden nach Anstoß. Für die laufende tägliche Nachpflege bleibt eine konfigurierbare erste Prüfung ab drei Stunden nach Anstoß möglich.

- Alle Events mit `kickoff_at <= as_of_utc - 24h` gehören zunächst zum historischen Bestand, unabhängig vom gespeicherten Rohstatus.
- Wochenalte `running`, `break` oder `pre_match` sind keine nachgewiesen aktuellen Live-Spiele. Sie müssen geprüft werden und dürfen den Nenner nicht umgehen.
- Ohne Anstoßzeit: bei belastbarer historischer Abschlussbeobachtung oder einer mindestens 24 Stunden alten letzten Beobachtung als historischen Fall aufnehmen; fehlende Zeit als separaten Prüfgrund führen. Ohne zeitliche Einordnung bleibt das Event in einer ausgewiesenen Gruppe `AGE_UNKNOWN`.
- Ein aktueller verifizierter Providerbeleg für eine Verlegung oder ein tatsächlich noch laufendes Spiel führt zu einer begründeten Neueinordnung. Diese wird mit Vorher-/Nachher-Zuordnung protokolliert.
- Aktuelle und zukünftige Spiele werden als `NOT_DUE` ausgewiesen und bei diesem Lauf nicht als fehlgeschlagenes Ergebnis gewertet.

### 4.3 Brutto- und bereinigte historische Menge

Bezeichner:

```text
N_all        = alle Fußball-Events in der eingefrorenen Prüfliste
N_historical = alle historisch fälligen Events vor fachlichen Ausschlüssen
N_excluded   = belegte Sonderfälle ohne regulär auswertbares Ergebnis
N_eligible   = N_historical - N_excluded
N_ft         = Events aus N_eligible mit verifiziertem regulärem FT
N_h2         = Events aus N_eligible mit gültigem HT und regulärem FT
N_entry      = Events aus N_eligible mit mindestens einem geeigneten
               historischen Tipico-Strategieeinstieg, unabhängig vom Ergebnis
N_entry_h2   = Events aus N_entry mit einem zum Einstieg passenden H2-Label
N_backtest   = Events mit mindestens einem nach den tatsächlichen
               Research-Regeln vollständig verwendbaren Beobachtungssatz
```

Zulässige Ausschlüsse sind beispielsweise bestätigte Absage, Abbruch ohne gültigen regulären Abschluss, nachgewiesenes Duplikat oder ein eindeutig anderes Spielformat ohne passenden Abrechnungsscope. Sonderfälle mit einem nachgewiesenen auswertbaren 90-Minuten-Ergebnis bleiben grundsätzlich nutzbar; eine Verlängerung allein ist kein zwingender Ausschluss.

Keine Ausschlussgründe: unbekanntes Ergebnis, fehlendes HT, kleinere Liga, fehlende FotMob-Zuordnung, unbekanntes Land, Providerfehler, Konflikt oder unbekannter Scope. Diese Fälle bleiben in der Bezugsmenge und senken gegebenenfalls die Abdeckung.

Die einmal erzeugte Bruttomenge bleibt erhalten. Fachliche Ausschlüsse dürfen nur mit Beleg und Änderungshistorie ergänzt werden. Bruttoquote, bereinigte Quote und Ausschlüsse immer gemeinsam berichten.

### 4.4 Zielgrößen

| Kennzahl | Formel | Ziel / Bedeutung |
|---|---|---|
| Inventarisierung | eingeordnete Events / N_all | 100 % |
| Lokale Prüfung | lokal geprüfte historische Events / N_historical | 100 % |
| Vollständige Fallbearbeitung | fachlich fertig geprüfte historische Events / N_historical | 100 %; Provider-Blockaden zählen nicht als fertig |
| FT-Abdeckung brutto | N_ft / N_historical | Unbereinigte Sicht immer ausweisen |
| FT-Abdeckung bereinigt | N_ft / N_eligible | Mindestens 90 % angestrebt |
| H2-Abdeckung | N_h2 / N_eligible | Separat ausweisen; erklärt fehlende Halbzeitlabels |
| Ergebnisabdeckung der Einstiege | N_entry_h2 / N_entry | Mindestens 90 % angestrebt |
| Vollständig backtestfähig | N_backtest / N_eligible | Reale Nutzbarkeit; fehlende Quoten separat erklären |

Bei Nenner 0 steht `N/A`, niemals 100 %. Zu jeder Quote Zähler, Nenner und Definition zeigen. Keine Rundung auf 90 %, wenn der exakte Quotient darunter liegt. Fehlende Anzahl bis zum Ziel: `max(0, ceil(0.90 * Nenner) - Zähler)`.

Der Mindestbestand für `N_entry` wird aus historischen Eingangsmerkmalen bestimmt, bevor das Resultat geprüft wird. Ein fehlendes Ergebnis darf ein ansonsten brauchbares Entry-Spiel nicht aus diesem Nenner entfernen.

Die Regeln für `N_entry` und `N_backtest` müssen die echten Research-Funktionen verwenden. Ein vorhandener HT-Snapshot allein beweist weder gültige Quoten noch Marktsemantik, Quote-Aktualität oder einen nutzbaren Einstieg.

Auch beim Erreichen von 90 % weiterarbeiten, bis die gesamte Prüfliste bearbeitet ist. Das Ziel ist kein vorzeitiges Abbruchkriterium.

## 5. Zuerst die bekannten technischen Lücken schließen

Die folgenden Stellen anhand des tatsächlichen Codes prüfen und durch Regressionstests absichern:

1. **Batch-Abbruch:** `--all-due` darf lokale Restkandidaten nicht zurücklassen, weil FotMob im selben Batch `CACHED_ONLY` liefert.
2. **Mehrfachauswahl:** Unveränderte FT-only- oder offene Fälle dürfen nicht immer wieder denselben Batch besetzen. Lokale Prüfungen dürfen Provider-Versuchszähler und Retry-Zeitpunkte nicht auf null zurücksetzen.
3. **Vorhandene Ergebnisse:** Der Vollbestand darf sich nicht auf die bisherige Missing-Result-Kandidatenauswahl beschränken. Auch `VERIFIED`, `APPLIED` und vorhandene vollständige Scores müssen lokal revalidiert werden.
4. **Reports:** Den Gesamtbericht aus allen persistierten Laufzeilen aufbauen. Der letzte Batch und `samples[:50]` sind keine vollständige Datengrundlage. Auch reine Audits müssen sämtliche offenen Events exportieren.
5. **Tagesindex:** Eine existierende FotMob-Indexzeile beweist keinen vollständigen, aktuellen Tag. `--refresh-index` muss auch vorhandene veraltete/unvollständige Tage auffrischen können; frische Antworten müssen ältere Cacheeinträge tatsächlich ersetzen.
6. **Scopes:** Ein leeres Elfmeterschießen-Feld allein beweist keine reguläre Spielzeit. Migrationspfade mit `COALESCE(extra_time, 0)` und ein optionaler Unknown-Scope-Modus dürfen keine strenge Freigabe erzeugen.
7. **Abgeleitete Labels:** `outcome_class`, `h2_goals` und gespeicherte Tor-Klassen müssen bei ungültigem Ergebnis sämtlich NULL bleiben. Kein Export darf eine Klasse aus einem zwar berechneten, aber verworfenen Ergebnis übernehmen.
8. **Paper-Abrechnung:** Eine Sperre in `classify()` muss bis zum wirklichen Ledger-Schreibpfad wirken. Kein nachfolgender Aufruf darf trotz `UNRESOLVED` anhand der rohen Scores abrechnen. Alte Ergebnis-Caches müssen auf Revision und Qualität geprüft werden.
9. **Korrekturen:** Ein späteres Tipico-Ergebnis darf einen abweichenden FotMob-Stand nicht allein aufgrund der Providerpriorität ersetzen. Konflikt, belegte Korrektur und gleichlautende Bestätigung unterscheiden.
10. **Zeitsemantik:** `finished_at` ist im Altbestand teilweise ein Beobachtungs-/Nachpflegezeitpunkt. Migration darf ihn nicht ungeprüft als tatsächliches `ended_at` ausgeben.
11. **Sperre:** Ein abgestürzter Prozess darf keine permanente `RUNNING`-Sperre hinterlassen. Resume und Wiederanlauf nach Container-Reboot verifizieren.
12. **UI:** Ein Limit der geladenen Explorer-Zeilen darf Filter und Gesamtkennzahlen nicht auf die neuesten 5.000 Spiele beschränken. Filter und Pagination im vollständigen DB-Bestand anwenden.

## 6. Ablauf der vollständigen Prüfung

### Phase A – Preflight und konsistente Sicherung

- Tatsächlichen absoluten DB-Pfad, Größe, Schema, Journal-Modus und vorhandene WAL-Dateien feststellen.
- Pfad explizit protokollieren. Nicht versehentlich die Dashboard-DB `data/tipico.db` verwenden, wenn `Tipico DB/tipico.db` gemeint ist.
- Vorhandene Tabellen, Archive, Raw-Belege, Providerlinks und Ergebnisrevisionen inventarisieren.
- Konsistente Sicherung mit SQLite-Backup-API beziehungsweise geeignetem DB-Snapshot erstellen. Eine aktive DB nicht lediglich als einzelne `.db` kopieren.
- `PRAGMA quick_check` und schemaangepasste Integritäts-/Eindeutigkeitsprüfungen ausführen; Sicherungsdatei zusätzlich verifizieren.
- Backup-Pfad und Prüfsummen der geschlossenen Sicherung sowie Laufmanifest dokumentieren. Hash nur der laufenden Hauptdatei genügt bei WAL nicht als logischer Dataset-Fingerprint.
- FotMob-Konfiguration und tatsächliche Netzwerkverfügbarkeit mit kleiner repräsentativer Tages-/Detailprüfung feststellen. Erstprüfung zählt als technische Vorbereitung, nicht als Bestandsabnahme.

Bei Providerblockade lokale Arbeit vollständig fortführen und den Providerteil fortsetzbar offen lassen.

### Phase B – Vollständiger lokaler Pass

Alle historischen Events einschließlich vorhandener Resultate prüfen. Reihenfolge der Belege:

1. bestehende Ergebniszeile mit Herkunft, Revision, Score und Scope;
2. terminale Tipico-Eventbeobachtungen und State-Historie;
3. geeignete finale Snapshots und ihre zugehörigen Payloads;
4. vorhandene Raw-/Archivbelege, soweit erreichbar und eindeutig zuordenbar;
5. gesicherte Halbzeitbeobachtungen für fehlendes HT.

Mutable Events und aktuelle States können als Eingangsbelege dienen; bei Übernahme deren entscheidende Werte kompakt unveränderlich sichern. Eine Referenz auf eine später überschreibbare Zeile allein reicht nicht.

`PARTIAL`-Snapshotqualität kann fehlende Wettmärkte bedeuten. Sie darf nicht pauschal jedes darin enthaltene bestätigte FT entwerten. Stattdessen unterscheiden: terminale Provider-Evidenz mit unvollständigen Märkten versus Fallback/Zwischenstand ohne Endbestätigung. Bei fehlendem Nachweis bleibt das Ergebnis offen.

Dieser Pass muss auch ohne jede FotMob-Verbindung die gesamte lokale Prüfliste abarbeiten.

### Phase C – FotMob-Tage vervollständigen

Benötigte Tage aus den offenen Events ableiten. UTC-Anstoßzeit und verwendete Tagesfeed-Zeitzone berücksichtigen; passende Nachbartage bei Tagesgrenzen prüfen. Beliebige Spielzeiten, alle Länder und Ligen einbeziehen.

Pro Provider/Datum/Zeitzone/Feed-Konfiguration einen Indexlauf dokumentieren:

- Anfragestatus, HTTP-Code, Fetchzeit, Payload-Hash und Parser-Version;
- Anzahl gelieferter und erfolgreich geparster Spiele;
- Vollständigkeit relativ zur empfangenen Tagesantwort;
- bei paginierten Antworten Stand aller Seiten;
- `COMPLETE`, `PARTIAL`, `FAILED`, `STALE` oder `UNKNOWN` mit Grund.

Ein erfolgreich geladener leerer Tag unterscheidet sich von Requestfehler, Parserfehler und unvollständigem Cache. Änderungen in der Tagesantwort müssen den Cache aktualisieren; verschwundene Zeilen dürfen keine verifizierten historischen Resultate löschen.

Frische vollständige Tage innerhalb eines Laufs wiederverwenden. Detailabfragen nach Provider-Match-ID deduplizieren. Es genügt, Tage und Resultatdetails für die Tipico-Prüfliste zu laden; ein Statistik-Vollimport aller FotMob-Spiele ist nicht erforderlich.

### Phase D – Identität und Ergebnis bestätigen

Bestätigte Links zuerst verwenden, aber Detailantwort erneut auf Identität prüfen. Sonst Matching anhand von Heim/Gast, Mannschaftsvarianten, Anstoßzeit, Land, Wettbewerb und Wettbewerbsart.

- Frauen-, Jugend-, Reserve-, Amateur- und Nationalmannschaften auseinanderhalten.
- Länder-/Namensalias nur explizit, nachvollziehbar und mit Zusatzmerkmalen anwenden.
- Keine automatische Übernahme nur wegen zweier ähnlicher Teamnamen.
- Keine stille Vertauschung von Heim und Gast.
- Bei mehreren plausiblen Kandidaten `AMBIGUOUS` mit Kandidatenliste führen.
- Fehlender Kickoff darf ohne anderweitig belastbaren Identitätsbeleg nicht zur automatischen Fuzzy-Zuordnung führen.
- Nicht fußballspezifische oder abweichende Spielformate gesondert behandeln.

Nach einem erfolgreichen Lookup FT, HT und Scope getrennt validieren. Ein Provider kann das Ergebnis belegen, ohne alle Statistikfelder zu liefern.

### Phase E – Gezielte zweite Runde

Nach dem ersten kompletten Pass offene Gründe gruppieren. Erneut prüfen, wenn ein Fehler behebbar ist: fehlende Tagesabdeckung, Timeout/5xx, noch laufendes Provider-Spiel, inzwischen vorhandener Link oder korrigierter Alias.

Technische Fehler erhalten begrenzte Retries mit Backoff und Jitter. `Retry-After` berücksichtigen. Permanenter No-Data-Fall und Rate-Limit sind unterschiedliche Zustände. Kein endloses Nachladen derselben unveränderten Antworten.

Ein manueller Provider-Link kann schwierige Fälle ergänzen; auch dann automatische Ergebnis-/Scope-Prüfung durchführen. Neue externe Ergebnisanbieter erst als ausdrücklich begründete Erweiterung vorschlagen, wenn Tipico und FotMob ausgeschöpft sind.

## 7. Fachliche Validierung und gemeinsame Nutzung

Die strengen Regeln von V0.6.5 gelten weiter und sind zentral für Migration, Collector, lokalen Recovery-Pass, Provider-Backfill, Research-Export und Paper-Settlement wiederzuverwenden.

- Scores sind nichtnegative ganze Zahlen. Boolesche Werte und Dezimalzahlen nicht durch `int()` scheinbar legitimieren. Informationsverlust im Parser anhand der verfügbaren Originalfelder prüfen.
- NULL bleibt unbekannt. Ein fehlender HT-Stand ist nicht 0:0.
- Für beide Teams einzeln `FT >= HT` prüfen.
- H2-Tore und Klasse ausschließlich aus validierten HT-/FT-Basiswerten neu berechnen.
- FT ohne HT kann für FT freigegeben werden; H2 und seine Klasse bleiben gesperrt.
- Bei Verlängerung/Elfmeterschießen nur einen separat belegten regulären Stand für entsprechende 90-Minuten-/H2-Märkte nutzen.
- Unknown Scope darf eine explorative Bewertung bekommen, zählt aber nicht in die strengen 90-%-Zähler.
- Ein Konflikt mit einem eingefrorenen HT-Einstieg sperrt die betroffene Beobachtung beziehungsweise den Trade. Ein anderer korrekter Einstieg desselben Spiels darf separat nutzbar bleiben.
- Ergebnis-Provenienz kann zwischen FT und HT unterschiedlich sein. Beispielsweise FT von FotMob plus bestätigtes HT von Tipico: gemeinsame Identität, Scope, HT-Konsistenz und beide Belege nachweisen.
- Gleiche Bestätigung erzeugt keine neue Ergebnisrevision. Tatsächliche Label-/Scope-/HT-Korrekturen sind versioniert und nachvollziehbar.
- `result_resolved_at` bedeutet Bekanntwerden in unserem System; `ended_at` nur belegtes historisches Ende. Backtest-Zeitsplits verwenden Anstoß-/Entscheidungszeiten.
- Bereits gebuchte Paper-Trades erhalten bei späteren Konflikten einen Prüfhinweis. Kein automatisches Zurückbuchen oder Doppel-Settlement.

Research-Dataset und Berichte speichern verwendete Quellen, Ergebnisrevisionen, Label-Stichtag und Dataset-Fingerprint. Alte Studien bleiben reproduzierbar. Nachträgliche Labels dürfen die Trefferbewertung ergänzen, aber keine historischen Eingangsquoten oder Entscheidungen ändern.

## 8. Persistente Prüfliste, Resume und Laufstatus

Bestehende Queue-/Evidenztabellen weiterverwenden und bei Bedarf um Lauf-/Itemtabellen ergänzen. Keine zweite unabhängige Matching- und Ergebnisimplementierung aufbauen.

Pro Lauf mindestens speichern:

```text
run_id, source_database_id, source_path, as_of_utc,
code_version, rule_version, config_fingerprint,
started_at, heartbeat_at, completed_at,
run_status, coverage_target_status, processing_complete,
frozen_event_count, historical_count, excluded_count,
backup_path, report_directory
```

Pro Event und Lauf mindestens:

```text
run_id, event_id, cohort, eligibility_reason,
local_status, provider_status, resolution_status,
stage, attempt_count, next_attempt_at, last_error,
provider_match_id, evidence_id, result_revision,
before_result_use_ft, after_result_use_ft,
before_entry_h2_usable, after_entry_h2_usable,
checked_at, completed_at
```

Eindeutigkeit `(run_id, event_id)`. Die komplette Liste wird vor der Verarbeitung eingefroren. Pro Pass jedes Item höchstens einmal bearbeiten; Retries sind explizite spätere Versuche. Bei Resume keine unberührten Events durch globale `LIMIT`-Abfragen überspringen.

Statusdimensionen trennen:

- Lauf: `RUNNING`, `PAUSED`, `BLOCKED_PROVIDER`, `COMPLETED`, `FAILED`.
- Verarbeitung: vollständig / unvollständig, inklusive verbleibender unversuchter und technisch blockierter Fälle.
- Coverage-Ziel: `MET`, `NOT_MET`, `NOT_EVALUATED` je Zielgröße.

Ein sauber belegter fehlender Providerkandidat kann fachlich fertig geprüft und trotzdem ungelöst sein. Ein HTTP-Fehler ohne erfolgreichen Lookup ist technisch blockiert und nicht vollständig geprüft. Ein bloßer Versuchszähler darf keine vollständige Fallbearbeitung vortäuschen.

Ein abgeschlossener Lauf mit weniger als 90 % bleibt als `COMPLETED` mit `coverage_target_status=NOT_MET` sichtbar. Dies darf nicht als erreichtes Projektziel beschrieben werden.

## 9. Parallelität, Locking und Ressourcennutzung

- Standardmäßig zehn gleichzeitige Detail-Worker mit bestehendem adaptivem FotMob-Rate-Control.
- Lokale SQLite-Schreibvorgänge kurz, atomar und möglichst serialisiert; Netzwerk außerhalb von Schreibtransaktionen.
- Persistierte Ergebniskorrekturen innerhalb derselben Transaktion gegen aktuellen DB-Stand prüfen. Ein Python-Lock schützt nicht vor einem zweiten Collector-Prozess.
- Lock vor Migration und schreibender Laufvorbereitung erwerben. OS-Dateisperre oder belastbares Lease-/Heartbeat-Modell verwenden.
- Verwaiste Sperren nach Prozessabbruch/Reboot sicher erkennen; Prozess-ID allein genügt wegen Wiederverwendung nicht.
- Graceful Shutdown bei SIGTERM; angefangene Items nach Neustart sicher erneut prüfbar.
- Arbeitsspeicher auf Batch-/Cachegrößen begrenzen; nicht alle detaillierten Providerantworten und Batches im RAM sammeln.
- Für tägliche Läufe neue und alte fällige Fälle fair einplanen, beispielsweise mit reservierten Anteilen und Überlauf in die andere Gruppe.
- Kein künstliches Gesamtlimit von 500 Events. 500 ist eine Batchgröße.

Reports werden aus der persistierten Laufhistorie erzeugt und lassen sich unabhängig vom Worker neu aufbauen. Unveränderliche laufbezogene Reports plus atomar aktualisiertes `latest` verwenden.

## 10. Verbindliche reale lokale Ausführung

Die Umsetzung ist erst abnahmefähig, wenn folgende Schritte tatsächlich ausgeführt und protokolliert wurden:

1. Originaldatenbank schreibgeschützt vollständig auditieren und Ergebnis-Baseline erstellen.
2. Verifizierte konsistente Sicherung und gesonderte Arbeitskopie erzeugen.
3. Migration und kleines Canary auf der Arbeitskopie testen; dies ist nur Vorbereitung.
4. Vollständigen lokalen Pass und anschließend reale FotMob-Prüfung aller dafür vorgesehenen Fälle auf der Arbeitskopie ausführen. Keine dauerhafte `cached`-Ersatzabnahme.
5. Falls nötig begrenzte zweite Runde für behebbare Fehler durchführen. Resume praktisch testen.
6. Vorher-/Nachher-Werte auf derselben Prüfliste berechnen. Stichproben der übernommenen und abgelehnten Ergebnisse gegen ihre tatsächlichen Belege prüfen.
7. Technische Freigabe: keine beschädigten Daten, keine unzulässigen Labels, keine stillen Ergebnisüberschreibungen, keine Ledger-Doppelbuchung, vollständige Berichte.
8. Anschließend die konkret benannte lokale Datenbank gesichert nachpflegen. Validierte Belege/Entscheidungen können importiert werden; erneute Identitäts-/Versionsprüfung im Ziel erforderlich. Kein unkontrolliertes Ersetzen einer inzwischen weitergeschriebenen DB durch die Testkopie.
9. Existierende neue Collector-Ergebnisse bei der Übernahme berücksichtigen. Bei Drift konfliktbehaftete Items erneut prüfen. Ursprüngliche Laufkohorte erhalten; neu hinzugekommene Events separat behandeln.
10. Abschließenden Audit und tatsächlichen Research-Dataset-Neuaufbau für die Ziel-DB ausführen. Bestätigen, dass die gewonnenen Labels dort nutzbar sind.

Der Abschluss muss eindeutig sagen, ob nur die Arbeitskopie oder auch die benannte Ziel-DB aktualisiert wurde. Alle Pfade und Zeitpunkte nennen.

Unter 90 % dürfen fachlich valide neue Ergebnisse trotzdem übernommen werden. Der verfehlte Zielwert muss sichtbar bleiben. Bei realer technischer Blockade ist der Lauf mit Checkpoint und konkretem Grund zu übergeben; kein vollständiger PASS behaupten.

Der Lauf muss über den gesamten lokalen Bestand gehen. Ein Abbruch nach 50, 432 oder 500 Events darf nur als unvollständiger Zwischenstand gemeldet werden.

## 11. CLI und Containerbetrieb

Die vorhandene CLI erweitern. Folgende Bedienung ist eine Soll-Schnittstelle, keine Behauptung bereits existierender Befehle:

```text
result_finalization.py audit --db PATH --out-dir DIR
result_finalization.py reconcile --db PATH --full-inventory --dry-run
result_finalization.py reconcile --db PATH --full-inventory --apply --workers 10 --refresh-index
result_finalization.py resume --db PATH --run-id RUN_ID
result_finalization.py status --db PATH --run-id RUN_ID
result_finalization.py report --db PATH --run-id RUN_ID --out-dir DIR
result_finalization.py recheck --db PATH --event-id EVENT_ID --apply
```

Bestehendes `--all-due` kompatibel unterstützen. Den Unterschied zwischen vollständiger Revalidierung (`--full-inventory`) und täglichen fälligen Queue-Items klar dokumentieren. Recheck muss genau das angegebene Event bearbeiten und bestehende Retry-Sperren explizit für diesen Fall übergehen können.

Windows-Aufrufe mit Leerzeichen im DB-Pfad und Linux-Aufrufe ohne Windows-Pfade testen. Secrets nicht protokollieren. Vorhandene Zugangskonfiguration verwenden; kein zusätzlicher Provider-Account ist Bestandteil dieser Spec.

Der bestehende `wetten-result-backfill.timer` bleibt der tägliche Einstieg. Ein initialer Vollbestand muss separat ausführbar sein und mit dem täglichen Dienst dieselbe Sperre verwenden. UI-/Collector-Start dürfen nicht automatisch wieder den kompletten Altbestand prüfen.

Bestehendes Service-Timeout von 30 Minuten mit tatsächlicher Laufzeit abgleichen: Resume-sichere Laufzeitgrenze oder geeignete Timeout-Konfiguration. Ein Timeout darf weder Fortschritt verlieren noch einen neuen Lauf dauerhaft sperren.

Exit-Codes für technische Fehler/Blockade und abgeschlossenen Lauf dokumentieren. Verfehlte Datenabdeckung getrennt im maschinenlesbaren Zielstatus ausweisen; dadurch kein automatisches endloses systemd-Restart auslösen.

Container-Update, Timerstatus, manueller Start, Logs, Resume und Reportpfade dokumentieren. Da der Container von hier nicht erreichbar ist, Linux-Kompatibilität lokal prüfen und Container-Abnahme ausdrücklich als ausstehend kennzeichnen. Datenbanken, Backups und private Einzelspielreports bleiben außerhalb von Git. Veröffentlichung nach geltender Nutzerbeauftragung; dieses Dokument verlangt keinen automatischen Push.

## 12. Oberfläche

Im vorhandenen Datenbereich eine kompakte Übersicht für den gewählten Lauf:

- geprüft / Gesamtbestand und abgeschlossene lokale Prüfungen;
- Providerprüfungen offen / technisch blockiert;
- bestätigte FT-Abdeckung mit Zähler/Nenner;
- H2-Abdeckung und Ergebnisabdeckung brauchbarer Einstiege;
- vollständig backtestfähige Spiele;
- Abstand zu 90 % in Prozentpunkten und Spielen;
- letzter Fortschritt, Workeranzahl, Durchsatz und geschätzte Restzeit mit Unsicherheit.

Die Anzeige liest persistierten Fortschritt und startet selbst keine Providerabfragen. Filter nach Datum, Land, Liga, Rohstatus, kanonischem Status, Quelle und Bearbeitungsgrund auf den gesamten Bestand anwenden.

Pro Spiel: letzter beobachteter Stand, bestätigtes HT/FT, Scope, verwendeter Beleg, Ergebnisrevision und konkrete Erklärung der Nichtverwendbarkeit. Bei gefilterten Anzeigen stets gefilterte Bezugsmenge und globale Laufmenge unterscheiden.

Häufige Gründe verständlich anzeigen, beispielsweise „Endstand bestätigt, Halbzeit fehlt“, „FotMob-Tag konnte nicht geladen werden“, „Zwei mögliche Spiele gefunden“ oder „Historische Einstiegsquoten fehlen“.

## 13. Berichte und Nachweise

Die vier etablierten Reportnamen erhalten:

- `RESULT_FINALIZATION_STATUS.md`: Gesamtbefund, Zielerreichung, Vorher/Nachher, Laufzeit, Grenzen.
- `RESULT_FINALIZATION_AUDIT.csv`: genau eine Zeile je Event der eingefrorenen Prüfliste.
- `RESULT_FINALIZATION_CHANGES.csv`: sämtliche tatsächlichen Änderungen aller Batches mit Quelle/Revision; Vorschläge separat kennzeichnen.
- `RESULT_FINALIZATION_UNRESOLVED.csv`: alle ungelösten beziehungsweise nicht verwendbaren historischen Fälle, auch wenn sie in keinem Provider-Sample vorkamen.

Zusätzlich:

- `RESULT_RECOVERY_MANIFEST.json`: Pfade, DB-/Code-/Regelidentität, Stichtage und Konfiguration ohne Secrets.
- `RESULT_RECOVERY_COVERAGE.csv`: global sowie nach Datum, Land, Liga und Altersklasse; absolute Nenner, Ausschlüsse, FT/H2/Entry-Label-/Backtest-Abdeckung.
- `RESULT_RECOVERY_PROVIDER_DAYS.csv`: Vollständigkeit und Fehler der benötigten Tagesindizes.
- `RESULT_RECOVERY_VALIDATION.md`: Integritätsprüfungen, Testfälle, Stichproben, Resume-/Idempotenznachweis.
- `RESULT_RECOVERY_STATUS.json`: maschinenlesbarer Gesamtfortschritt und Zielstatus.

Vorher-/Nachher-Audit unveränderlich je Lauf aufbewahren. Sowohl „nach alter gespeicherter Freigabe“ als auch „nach neuer strenger Validierung“ zeigen, wenn die neue Regel bereits gespeicherte Labels zurücknimmt.

Im Abschlussbericht mindestens nennen:

1. Alle Events, historische Events, nicht fällige Events und Fälle mit unbekanntem Alter.
2. Belegte Ausschlüsse samt Gründen.
3. Vollständig geprüfte, unversuchte und technisch blockierte Fälle.
4. Verifizierte FT vor/nach, neue lokale und neue FotMob-FT, verlorene Freigaben und Nettozuwachs.
5. H2-Labels vor/nach sowie Ergebnisabdeckung der geeigneten Einstiege.
6. Vollständig backtestfähige Spiele vor/nach nach realer Dataset-Prüfung.
7. Quellbestätigungen und reine Statusänderungen getrennt von neuen Ergebnissen.
8. Konflikte, fehlende HT, Scope-Lücken, Matchingprobleme, Provider-No-Data und technische Fehler.
9. Gesamtlaufzeit, reine Netzwerkzeit, Requests, erfolgreiche Details, Retries und effektiv bearbeitete eindeutige Spiele pro Minute.
10. Zielerreichung beider 90-%-Kennzahlen und exakt fehlende Spiele.
11. Aktualisierte Ziel-DB, Backup- und Reportpfade; Containerstatus getrennt.

Alle Zahlen müssen aus den vollständigen persistierten Run-Items und Resultaten herleitbar sein. Summenchecks: keine doppelten Events, Nenner-Konsistenz, Vorher + Gewinne - Rücknahmen = Nachher.

## 14. Vorgehen bei weniger als 90 %

Unterhalb eines Zielwerts ist eine belegte Lückenanalyse verbindlich:

- Welche Ursachen erklären die fehlende Abdeckung in absoluten Spielzahlen?
- Wie viele Fälle sind mit besserem Index-/Namensmatching voraussichtlich noch technisch auflösbar?
- Wie viele Spiele haben bei FotMob FT, aber keine HT, und wie viele davon lassen sich mit lokalem Tipico-HT ergänzen?
- Welche Ligen haben dauerhaft geringe Providerabdeckung?
- Wie viele Spiele würden selbst mit Ergebnis wegen fehlender Einstiegsquoten unbrauchbar bleiben?
- Welche Ursache entstand beim Collector und muss für neu eintreffende Spiele behoben werden?

Die nächste Runde priorisiert Fälle mit vorhandenen brauchbaren Einstiegsdaten und behebbaren Fehlern. Trotzdem den übrigen historischen Bestand vollständig prüfen und darstellen.

Keine Liga rückwirkend aus dem Erfolgsnenner streichen. Eine zusätzliche Liga-Auswertung hilft später, die Sammelqualität gezielt zu verbessern. Sie ist keine Begründung, unbekannte Spiele nachträglich verschwinden zu lassen.

Als Verbesserung für neue Spiele bestehende Collector-Abschlussversuche und Ergebnis-Retries prüfen. Ein behobener Erfassungsfehler ist wertvoller als immer wieder dieselben alten Datenlücken zu reparieren. Umfang auf Abschluss-/Ergebniserfassung beschränken.

## 15. Verbindliche Tests und fachliche Abnahme

### Automatisierte Regression

1. Mindestens 1.201 gemischte Events bei Batchgröße 500: jedes Item bearbeitet, kein Rest nach dem ersten Batch.
2. Providerblockade im ersten Batch: lokaler Pass trotzdem vollständig, Providerstatus offen/blockiert, kein vollständiger PASS.
3. Prozessabbruch nach partiell geschriebenem Batch und erfolgreicher Resume; keine verlorenen Items oder Ergebnisduplikate.
4. Bereits vorhandene `VERIFIED`-/`APPLIED`-Ergebnisse mit falscher Qualität oder Ableitung werden revalidiert.
5. Wochenalte `running`, `break`, `pre_match` und `no_longer_live` werden inventarisiert; keine automatische Ergebnisannahme.
6. Vorhandener, aber unvollständiger FotMob-Tag wird aufgefrischt; frische Daten gewinnen gegen alten Cache.
7. Erfolgreich leerer Tag, fehlender Cache, Parserfehler, HTTP-Fehler und echter No-Candidate bleiben unterscheidbar.
8. FT-only, fehlendes HT, HT-Konflikt, `FT_home < HT_home` bei positiver Gesamtdifferenz, ungültige Scoretypen korrekt behandeln.
9. Regulärer 90-Minuten-Stand versus AET-/Penalty-Stand und Unknown Scope; keine Freigabe aus leeren Flags.
10. Ähnliche Namen, falsches Land, Jugend/Reserve/Frauen und vertauschte Teams erzeugen keine falschen Links.
11. Unterschiedliche Provider-FT erzeugen sichtbaren Konflikt; bestätigte Korrektur erzeugt vollständige Revision.
12. Paper-End-to-End: gesperrtes H2 bleibt OPEN bis zum Ledger, erlaubtes H2 wird genau einmal gebucht; veralteter Ergebnis-Cache umgeht Sperre nicht.
13. Research-Export: ungültiges Ergebnis hat weder H2-Torzahl noch Outcome-Klasse; echte Quelle und Revision bleiben erhalten.
14. Nenner bleibt unabhängig vom Matching-Erfolg; NULL/0-Werte und leere Kohorten korrekt darstellen.
15. Reports enthalten alle Events und Provider-Outcomes über sämtliche Batches, nicht nur die letzten 50 Samples.
16. Erneuter Lauf ohne neue Evidenz: keine neuen Ergebnisrevisionen, keine doppelten Änderungen oder Ledgerbuchungen.
17. Zwei Workerprozesse beziehungsweise Collector plus Nachpflege: keine verlorenen Ergebnisänderungen; Sperre nach Crash wieder benutzbar.
18. UI-Pagination/Filter berücksichtigen Events jenseits eines Anzeige-Limits.

### Reale Abnahme

- Vollständiger Audit und echter Bestandslauf auf der lokalen Kopie mit erfolgreichen FotMob-Netzwerkabrufen, sofern offene Fälle solche benötigen.
- Geschichtete Stichprobe von mindestens 30 übernommenen Ergebnissen oder sämtlichen Übernahmen, falls weniger: verschiedene Länder/Ligen, lokale und Providerquellen, FT-only und HT-Kombinationen. Zusätzlich mindestens zehn abgelehnte/offene Fälle prüfen, sofern vorhanden.
- Stichprobe prüft nicht nur Datenbankkonsistenz, sondern auch Spielidentität, HT/FT und Scope am gespeicherten Providerbeleg.
- Vollständiger Nachher-Audit und Research-Dataset-Prüfung auf der nachgepflegten Ziel-DB.
- Wiederholung ohne neue Daten belegt Idempotenz.
- Vollständige projektweite Regression nach den fachlichen Änderungen, plus gezielte CLI-/Migrations-/Resume-Tests.

## 16. Definition of Done

Der technische Teil ist abgeschlossen, wenn alle erforderlichen Implementierungen und Prüfungen bestehen, der vollständige lokale Bestand verarbeitet ist, validierte Änderungen in der benannten lokalen Ziel-DB vorhanden sind und sämtliche Reports nachvollziehbar erzeugt wurden.

Das Coverage-Ziel ist erreicht, wenn beide relevanten Zielquoten mindestens 90 % betragen. Bei einer leeren Entry-Kohorte ist das H2-Entry-Ziel nicht messbar und darf nicht als erfüllt gelten.

Wenn Quellen trotz vollständiger Bearbeitung weniger liefern, lautet die Übergabe ausdrücklich „Technisch abgeschlossen, Coverage-Ziel nicht erreicht“ mit gemessenen Quoten und Lückenanalyse. Bei unvollständiger Bearbeitung oder blockiertem Provider lautet sie „Bestandsprüfung noch unvollständig“ mit Checkpoint und verbleibender Arbeit.

Eine bloße Erhöhung des `finished`-Zählers, ein einzelner erfolgreicher Batch oder ausschließlich bestandene Unit-Tests erfüllen diese Spec nicht.
