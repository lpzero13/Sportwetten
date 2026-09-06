# V0.6.3 – Tipico Backtest & Pattern Lab

Ausführliche Implementierungsspezifikation für das bestehende Projekt Sportwetten.

Stand: 06.09.2026. Grundlage: V0.6.2 und eine ausschließlich lesende Untersuchung der vom Nutzer kopierten Container-Datenbank. Dieses Dokument beschreibt die nächste Umsetzung; es ist weder ein fertiger Backtester noch ein Nachweis einer profitablen Strategie.

## 1. Auftrag und gewünschtes Ergebnis

Baue einen reproduzierbaren, ausschließlich auf Tipico-Daten basierenden Forschungs- und Backtestbereich. Er soll zeigen, wie sich beobachtete Quoten, daraus abgeleitete Markt-Wahrscheinlichkeiten und der Halbzeitstand zu späteren Ergebnissen und zur simulierten Rendite verhalten.

Der Nutzer möchte insbesondere folgende Fragen beantworten:

- Wenn Tipicos aus Quoten abgeleitetes P1 zwischen X und Y liegt: Wie häufig fällt tatsächlich exakt ein Tor in Halbzeit zwei?
- Wie häufig gewinnt dann die Kombination auf 0 oder 2+ Tore, und was verdient oder verliert sie zu den tatsächlich gespeicherten Quoten?
- Welche Bereiche haben hohe beziehungsweise niedrige Trefferquoten? Welche davon haben trotzdem eine schlechte Rendite?
- Welche Rolle spielen die beiden Kaufquoten, der P1-Break-even und der Halbzeitstand?
- Ist die Kombination besser als die jeweiligen Einzelwetten auf 0 oder 2+ Tore?
- Lassen sich daraus wenige nachvollziehbare Regeln formulieren, die anschließend auf neuen Spielen im Paper Trading geprüft werden?
- Kann Hermes diese Versuche auf dem Container regelmäßig ausführen, ohne ständig Regeln und Testdaten zu vermischen?

Das System darf als korrektes Ergebnis ausdrücklich melden: „Kein belastbarer Vorteil gefunden“ oder „Daten reichen noch nicht aus“. Es darf keinen Gewinner erzwingen.

### 1.1 Fachliche Einordnung

Eine hohe Trefferquote ersetzt keinen positiven Erwartungswert. Wenn die tatsächlichen Wahrscheinlichkeiten für sämtliche angebotenen Wetten zu ungünstigen Auszahlungen führen, erzeugen Filter oder Einsatzaufteilungen allein keinen dauerhaften Gewinn. Ein funktionierender Filter würde gerade bedeuten, dass innerhalb einer Teilgruppe die tatsächlichen Ergebniswahrscheinlichkeiten günstiger sind, als es die Kaufquoten erfordern.

Das Vorhaben braucht dafür kein ML. Es untersucht zunächst empirische Häufigkeiten und einfache, vorab definierte Regeln. Ob Tipico solche Unterschiede zulässt, wird weder positiv noch negativ vorausgesetzt.

„P1 laut Tipico“ ist in der aktuellen Anwendung eine aus mehreren Quoten berechnete Größe. Sie ist keine direkt veröffentlichte, verifizierte wahre Wahrscheinlichkeit von Tipico.

## 2. Verbindlicher Umfang

### Enthalten

1. Audit und eingefrorener Datenstand.
2. Kanonischer Datensatz mit historischen Einstiegsbeobachtungen und getrennten Ergebnisbelegen.
3. Deskriptive P1-, Quoten- und Halbzeitstand-Auswertungen.
4. Deterministischer Snapshot-Backtest für Halbzeiteinstiege.
5. Kleine, feste Bibliothek von Regelvarianten und Vergleichsstrategien.
6. Zeitlich saubere Bewertung und Unsicherheitsangaben.
7. Export versionierter Kandidaten für Paper Trading.
8. Gemeinsame Regelauswertung für Backtest und Paper Trading.
9. Dokumentierter, wiederholbarer Ablauf für Hermes auf dem Container.
10. Kompakte Einbindung in die vorhandene Oberfläche nach erfolgreicher Daten- und Rechenprüfung.

### Nicht Bestandteil dieser Version

- FotMob-Abfragen, FotMob-Metriken, ML, trainierte Kalibrierungsmodelle oder andere Datenanbieter.
- Echtgeldwetten, automatische Wettabgabe oder Zugang zu Wettkonten.
- Neue Sportarten, weitere Buchmacher, Cash-out, Hedging während des Spiels oder Progressionssysteme.
- Flächendeckende Suche über tausende Parameterkombinationen.
- Optimierte Kelly-Einsätze oder Bankroll-Optimierung zur Verbesserung eines schwachen Signals.
- Strategien mit Einstieg nach einem Tor oder in Minute 60/70/80/85/90. Die vorhandenen Daten dafür bleiben für eine spätere Version verfügbar.
- Rückwirkende Umdeutung alter Paper-Trades oder Änderung bestehender Portfolioregeln.

Die Umsetzung soll zuerst einen überprüfbaren Audit und CLI-Backtest liefern. Ein größerer UI-Umbau ist dafür nicht erforderlich.

## 3. Tatsächlich geprüfte Ausgangsdaten

### 3.1 Identität und Grenzen der Prüfung

Lokale Quelle:

`C:\Users\chris\Documents\Codex\2026-08-29\es-x20\Tipico DB\tipico.db`

| Merkmal | Festgestellter Wert |
|---|---|
| Dateigröße | 1.028.866.048 Bytes, ca. 1,03 GB / 0,96 GiB |
| SHA-256 | `22f754659f7c21df505d5e2b5a96675211bd642660acb311a0814c6737e06f3a` |
| SQLite `PRAGMA quick_check` | `ok` |
| Event-Beobachtungszeitraum, UTC | 30.08.2026 10:31:01 bis 06.09.2026 10:02:21 |
| Zeitraum | Acht berührte Kalendertage, ungefähr eine Woche Beobachtung |
| `events` | 2.318 Fußball-Events |
| Wettbewerbs-IDs / Länderbezeichnungen in `events` | 266 / 110 |
| `snapshots` | 14.650 |
| `event_states` | 116.338, erst ab 02.09.2026 08:12:59 UTC |
| `odds_history` / `canonical_outcomes` | Jeweils 0 Zeilen |
| `strategy_evaluations` | 2.839 Zeilen für 726 Events |
| `match_results` | 421 Events mit vollständigen HT-/FT-Scores |
| `paper_portfolios`, `paper_trades`, `paper_observations`, `paper_decisions` | Jeweils 0 Zeilen |
| Parquet-Verweise in Snapshots | 14.635 |
| Snapshots mit nichtleerem `relevant_markets_json` | 12.789 |
| `snapshot_outbox` | 15 noch nicht exportierte Einträge |

Im bereitgestellten Ordner lag nur `tipico.db`; keine zugehörigen Parquet-Dateien, Raw-Payloads oder WAL-/SHM-Dateien. Die Archivpfade verweisen unter anderem auf `/var/lib/wetten/archive/tipico/...` im Container. Das bedeutet nicht, dass die Archive dort fehlen. Sie waren lediglich nicht Teil dieser lokalen Prüfung.

`quick_check=ok` bestätigt die geprüfte SQLite-Struktur, aber nicht die Vollständigkeit einer Kopie aus einer laufenden WAL-Datenbank. Die genannten leeren Paper-Tabellen beschreiben nur diese Kopie. Daraus folgt keine Aussage über später angelegte Portfolios oder den aktuellen Containerbetrieb.

### 3.2 Halbzeit-Abdeckung

| Prüfung | `HALFTIME` | `HT_STABLE` |
|---|---:|---:|
| Snapshots / unterschiedliche Spiele | 1.640 | 1.640 |
| Beide Kaufquoten > 1 vorhanden | 1.557 | 1.580 |
| Gespeichertes P1 vorhanden | 1.411 | 1.439 |
| Join auf `match_results` vorhanden | 419 | 419 |
| Kaufquoten + P1 + Ergebnis vorhanden | 370 | 377 |
| Zusätzlich `break`/`HZ`, aktueller Score = HT-Score und HT-Score = Ergebnis-HT | 308 | 375 |
| Explizite `extra_time=0` und `penalties=0` im Snapshot | 20 | 20 |
| Kaufquoten + P1 + Ergebnis + diese expliziten Scope-Flags | 2 | 2 |

Die 375 `HT_STABLE`-Spiele sind **vorläufige historische Kandidaten**, keine 375 vollständig verifizierten oder nachweislich ausführbaren Trades. Marktsemantik, Quellenzuordnung, Ergebnisprovenienz, tatsächlich bekanntes Einstiegsfenster und fehlende historische Nachweise sind noch gesondert zu prüfen.

Die Halbzeit-Snapshots verteilen sich auf 253 Wettbewerbs-IDs und 107 Länderbezeichnungen. Das sind für einzelne Ligen sehr kleine Stichproben. Länderbezeichnungen sind zunächst Provider-Metadaten, keine automatisch validierten souveränen Staaten.

### 3.3 Konkrete Qualitätsbefunde und Folgen

| Befund | Evidenz | Bedeutung und Anforderung |
|---|---|---|
| Ergebnisabdeckung begrenzt | 419 von 1.640 HT-Spielen, ca. 25,5 %, haben einen Join auf `match_results` | Nur die abgeschlossenen Spiele auszuwerten kann die Stichprobe verzerren. Fehlende Ergebnisse nach Datum, Wettbewerb und Quotenbereich ausweisen. |
| Snapshot-Typ ist kein sicherer Phasennachweis | 274 `HALFTIME`-Zeilen zeigen `running` und Minutenwerte; 4 `HT_STABLE`-Zeilen zeigen 46/47 Minuten | Widersprüche prüfen. Eine möglicherweise nachlaufende Uhr ist nicht automatisch ein falscher Snapshot, darf aber auch nicht stillschweigend als bestätigte Halbzeit gelten. |
| `FINAL` ist kein hinreichender Ergebnisbeleg | Von 503 FINAL-Snapshots zeigen 56 `break`, 26 `running`, 421 `finished` | Die 82 nicht-terminal beschrifteten Zeilen nicht allein aufgrund von `snapshot_type` oder vorhandener Zielklasse abrechnen. |
| Historische Scope-Flags fehlen | Je 1.620 von 1.640 HT-/HT_STABLE-Snapshots haben NULL bei Extra Time/Penalties | NULL nicht als FALSE behandeln. Rekonstruktionsbelege und Annahmen getrennt führen. |
| Einzelquotenstatus fehlt im schlanken JSON | In allen geprüften HT-/HT_STABLE-`relevant_markets_json` fehlen explizite Felder für `available`, `status`, `observed_at`, `settlement_scope` | Ein historisch ausgewählter Preis ist nicht automatisch ein vollständiger Ausführungsnachweis. |
| P1 lässt sich nicht immer aus den vier flachen Spalten reproduzieren | 10 Abweichungen > 1e-6 unter 2.850 HT-/HT_STABLE-Zeilen mit P1 und vier Resttorquoten; maximale absolute Differenz ca. 0,7745 | Quellen-/Periodenprüfung erforderlich; nicht blind die vier Spalten normalisieren oder negatives P1 auf 0 setzen. Alle zehn Fälle liegen in den HALFTIME-Zeilen mit `running`. |
| Keine vollständige Tick-Historie in SQLite | `odds_history` und `canonical_outcomes` leer | Snapshot-Replay ist möglich; kontinuierliche Preisbewegungen sind mit dieser Kopie nicht nachgewiesen. |

Die 421 Ergebniszeilen haben keine negativen teamweisen HZ2-Tordifferenzen und keine Abweichung zwischen gespeicherten Torzahlen und HT-/FT-Arithmetik. Es gab keine doppelten Kombinationen aus `event_id` und `snapshot_type` und keine HT-Konflikte im untersuchten HT-Join. Das sind positive Teilprüfungen, keine vollständige Provenienzprüfung.

Der aktuelle Serializer schreibt ausgewählte beziehungsweise als offen erkannte relevante Quoten. Diese Codeeigenschaft kann bei passender, belegter historischer Version als indirekter Nachweis dienen. Sie ersetzt nicht die Prüfung, welche Version die jeweilige alte Zeile tatsächlich erzeugt hat. Ein identischer `normalizer_version`-Text allein identifiziert nicht den gesamten Serializer oder Collector.

**Erste Priorität ist daher die Qualität und Abdeckung der Ergebnis- und Einstiegsdaten.** Eine größere Parametersuche löst diese Einschränkungen nicht.

## 4. Datenzugriff, Audit und Reproduzierbarkeit

### 4.1 Quelle schützen und sauber einfrieren

- Für eine lokale Kopie SQLite mit URI `mode=ro` und `PRAGMA query_only=ON` öffnen. Keinen bestehenden `Database`-Konstruktor verwenden, falls dieser Migrationen, Schreibzugriffe oder Runtime-Initialisierung ausführt.
- Keine Änderungen an Quelltabellen, kein VACUUM, keine Reparatur und kein Checkpoint auf der Nutzerkopie.
- Im Container eine konsistente SQLite-Backup-Kopie über die Backup-API oder ein vorhandenes geprüftes Backupverfahren erzeugen. Eine aktive WAL-Datenbank nicht einfach als einzelne Datei kopieren.
- Neben der Datenbank auch das Manifest der tatsächlich verwendeten Archive und deren Hashes einfrieren. Unexportierte Outbox-Einträge berücksichtigen und über stabile Identität deduplizieren.
- Snapshot-ID nur innerhalb ihres Datenbankbestands als eindeutig betrachten. Identität mindestens aus Source-Dataset-ID und Snapshot-ID bilden; Payload-Hash für Integritätsprüfung verwenden.
- Pfade sind konfigurierbar. Windows-Pfade oder `/var/lib/wetten` nicht fest im Backtest verankern. Archivpfade kontrolliert relativ zu einem konfigurierten Archivwurzelverzeichnis auflösen.
- Das Manifest enthält Quellhash, Schema-/Adapterversion, Software-Commit, UTC-Cutoff, Zeilenzahlen, verwendete Dateien, fehlende Dateien, Zeitzone und alle Auswahlregeln.
- Die fehlenden Ergebnisbelege und der unvollständige letzte Tag bleiben sichtbar. `max(last_seen_at)` ist kein Beleg für lückenlose Erfassung.

Der Ordner `Tipico DB/` ist derzeit ein unversionierter, nicht durch die bestehende `.gitignore` ausgeschlossener Ordner. Vor einem späteren Implementierungs-Commit ist dieser konkrete Datenordner sowie das neue Forschungs-Ausgabeverzeichnis auszuschließen. Keine Datenbank oder Archive auf GitHub hochladen; bestehende historische Reportdateien nicht pauschal löschen.

### 4.2 Audit-Ausgaben

Jeder Audit liefert:

- Volumen, unterschiedliche Events, Zeitabdeckung und Schema.
- Abdeckung je Tag, Wettbewerb und Quelle; Zustand fehlender Ergebnisse einschließlich Alter.
- Funnel von erfassten Fußballspielen über verifizierte Halbzeitbeobachtungen bis zu auswertbaren Trades.
- Primären Ausschlussgrund pro Zeile für additive Funnel-Zahlen und zusätzlich alle zutreffenden Qualitätsflags.
- Nullraten, Score-Konflikte, fehlende IDs, doppelte oder widersprüchliche Markteinträge, P1-Abweichungen und zeitliche Widersprüche.
- Verteilung der Ergebnisverfügbarkeit über vorher festgelegte P1-/Quotenbereiche. Dafür keine Ergebniswerte zum Definieren der Bereiche verwenden.
- Separate Anzahl tatsächlich unabhängiger Spiele. Mehrere Snapshots, Varianten oder Portfolioentscheidungen erhöhen diese Zahl nicht.
- Stichproben-Belege mit Source-Dataset-ID, Event-ID, Snapshot-ID und benannten Prüfregeln.

## 5. Kanonischer Datensatz und Evidenzstufen

### 5.1 Getrennte Datenobjekte

Mindestens vier getrennte Objekte beziehungsweise Tabellen/Dateien vorsehen:

1. `observations`: Was war zu einem konkreten Zeitpunkt bekannt?
2. `results`: Welcher Endstand ist durch welche Quelle und zu welchem Zeitpunkt belegt?
3. `decisions`: Welche versionierte Regel hätte auf einer Beobachtung wie entschieden?
4. `trades`: Eingefrorene Einsätze, Preise, simulierte Ausführung und späteres Settlement.

Historische Ergebnisfelder dürfen technisch nicht als verfügbare Eingangsfeatures in der Regelauswertung erscheinen. Die Engine erhält ausschließlich ein Entry-Objekt; Labels werden danach für die Bewertung zugespielt.

### 5.2 Pflichtfelder der Beobachtung

| Gruppe | Felder beziehungsweise Inhalt |
|---|---|
| Herkunft | Dataset-ID, Beobachtungs-ID, Snapshot-ID/-Typ, Payload-Hash, Quellpfad, Parser-/Normalizer-/Serializer-Version soweit bekannt |
| Identität | Tipico-Event-ID, Wettbewerbs-ID, Land, Ligabezeichnung, Heim-/Auswärtsteam |
| Zeit | `observed_at_utc`, `available_at_utc` soweit belegt, Kickoff, erste bestätigte HT-Beobachtung, Abstand dazu, Zeitpunkt der simulierten Entscheidung |
| Zustand beim Einstieg | Tatsächlicher Provider-Status, Periode, Display-Zeit, aktueller Score, HT-Score, Extra-Time-/Penalty-Flags und Herkunft dieser Angaben |
| Kaufquoten | q0/q2, Markt-/Outcome-IDs, Marktart, Linie, Periode, Settlement-Scope, Status, Verfügbarkeit, Beobachtungszeit und Quellenbeleg je Auswahl |
| P1-Referenzen | Beide U/O-Paare mit IDs, Linien, Periode, Quoten und Zeit; gespeicherte und neu berechnete Werte getrennt |
| Abgeleitete Größen | p0, p1, p2+, P1-Break-even, Win-ROI, P1-Puffer, Kehrwertsumme, Overround je Referenzpaar |
| Qualitätsfelder | Phase-, Scope-, Ergebnis- und Markt-Nachweis, fehlende Felder, Rekonstruktionsmethode, verfügbare Analysen, Evidenzstufe |

Die aktuelle `events`- oder `current_*`-Zeile darf nicht als historischer Zustand an alte Beobachtungen angehängt werden. Stabile Identitätsangaben dürfen mit gekennzeichneter Herkunft ergänzt werden; veränderliche Scores, Karten, Phasen und Quoten nicht.

`event_states` nur mittels rückwärtsgerichtetem Zeit-Join mit dokumentierter maximaler Distanz verwenden. Eine erst später beobachtete Halbzeit darf eine frühere Ersthalbzeitquote nicht rückwirkend zum gültigen Halbzeiteinstieg machen. Bei bereits in HZ vorgefundenen Spielen ist die tatsächliche Dauer der Halbzeit unbekannt.

Besonders auf abgeleitete Archivfelder achten: Der aktuelle Helfer `_snapshot_period` kann aus dem Snapshot-Typ HALFTIME/HT_STABLE die Zeichenfolge `FIRST_HALF` erzeugen. Ein solches abgeleitetes Feld ist keine unabhängig beobachtete Provider-Periode. Herkunft und Bedeutung von Phasenfeldern vor ihrer Verwendung prüfen; weder diese Ableitung noch der Snapshot-Name darf die tatsächliche Spielphase ersetzen.

### 5.3 Drei Evidenzstufen mit separaten Ergebnissen

**A – `VERIFIED_REPLAY`**

Historische Beobachtung enthält direkte oder durch Originalbelege wiederhergestellte, zeitlich passende Nachweise für Phase, Quotenidentität, offenen Zustand, Marktsemantik und regulären Settlement-Scope. Ein gültiger Ergebnisbeleg liegt vor. Dieser Datensatz unterstützt den strengeren Replay-Vergleich; er beweist trotzdem keine reale Annahme beim Buchmacher.

**B – `LEGACY_EXPLORATORY`**

Quoten und Zielereignis sind ausreichend eindeutig für eine beschreibende historische Auswertung, mindestens ein Ausführungs- oder Scope-Nachweis fehlt jedoch. Die konkret benötigten Annahmen müssen pro Zeile benannt sein. Diese Zeilen dürfen Hypothesen liefern und einen als angenommen gekennzeichneten Snapshot-ROI zeigen, aber nicht als ausführbare oder bestätigte Strategie vermarktet werden.

Ist nicht einmal klar, ob sich eine Quote auf HZ2 oder auf die verbleibende erste Halbzeit bezieht, ist Stufe B nicht zulässig.

**C – `UNUSABLE_OR_PENDING`**

Ungeklärte Marktsemantik, fehlende Quoten, widersprüchliche Scores, unbekanntes Ergebnis oder nicht auflösbare Zeitkonflikte. Keine erfundene Treffer-/Verlustzuordnung. Ein fehlendes Ergebnis bleibt `PENDING` beziehungsweise `UNRESOLVED` und kein VOID.

Stufen nicht zu einer gemeinsamen Rendite mischen. Ein später verfügbarer Originalbeleg kann die Stufe in einer neuen Dataset-Version verbessern; die alte Version bleibt reproduzierbar. Historische Datenqualitätsausnahmen dürfen keine Live-Validierung des Paper-Workers abschalten.

### 5.4 Ergebnisregeln

- Einstieg betrifft ausschließlich Tore nach bestätigter Halbzeit bis Ende der regulären Spielzeit einschließlich Nachspielzeit.
- Ergebnis zunächst aus `match_results`, danach aus unabhängig validierten terminalen Tipico-Belegen. Widersprüche zwischen Quellen nicht durch eine beliebige Priorität verstecken.
- `snapshot_type=FINAL`, Minute 90 oder `NO_LONGER_LIVE` allein genügt nicht.
- Gespeicherte `second_half_goals` und Zielklassen immer gegen die Score-Arithmetik und Terminalität prüfen.
- `g2 = (FT_home - HT_home) + (FT_away - HT_away)`; beide teamweisen Differenzen müssen nichtnegativ sein.
- Verlängerung und Elfmeterschießen nur dann beherrschbar, wenn der reguläre Endstand separat bestätigt ist. Keine geschätzten 90-Minuten-Scores.
- HT-Korrekturen gegenüber dem eingefrorenen Einstieg als Konflikt ausweisen und bis zur expliziten Klärung aus Renditeauswertungen ausschließen.
- Ergebnisstatus, Quelle, Erfassungszeit und Revision speichern. `finished_at` nicht ungeprüft als tatsächlichen Abpfiff oder Zeitpunkt der erstmaligen Ergebnisverfügbarkeit interpretieren.
- Fehlende Resultate zunächst aus vorhandenen Tipico-Archiven prüfen. Optionaler gezielter Tipico-Ergebnisnachlauf muss separat, begrenzt und nachvollziehbar sein. Keine FotMob-Ergänzung in dieser Version.
- Ein altes Spiel, das Tipico nicht mehr beantwortet, bleibt ungeklärt. Kein unbegrenztes Retry und kein geratenes Ergebnis.

## 6. Fachliche Definitionen und Berechnung

### 6.1 Zielklassen

- `Y0 = 1`, wenn HZ2 genau 0 Tore hat.
- `Y1 = 1`, wenn HZ2 genau 1 Tor hat.
- `Y2 = 1`, wenn HZ2 mindestens 2 Tore hat.
- Für die Kombination: `covered_hit = Y0 + Y2`.
- `profitable_trade = 1`, wenn das tatsächlich berechnete P/L > 0 ist. Diese Größe ist bei Rundung und Kosten nicht zwingend identisch mit `covered_hit`.

Alle Wahrscheinlichkeiten intern als Zahlen von 0 bis 1. In der Oberfläche Prozent; Differenzen von Wahrscheinlichkeiten in **Prozentpunkten**. Prozent und Prozentpunkte nicht vertauschen.

### 6.2 Markt-P1

Bei zwei konsistenten, zeitgleichen U/O-Referenzpaaren für 0,5 und 1,5 verbleibende Tore:

```text
p0 = (1 / q_under_0_5) / ((1 / q_under_0_5) + (1 / q_over_0_5))
p01 = (1 / q_under_1_5) / ((1 / q_under_1_5) + (1 / q_over_1_5))
p1_market = p01 - p0
p2plus = 1 - p01
```

- Referenzpaare müssen jeweils aus demselben Markt, derselben Periode und derselben Beobachtung stammen. Nicht jeweils die attraktivste Einzelquote zusammenwürfeln.
- Konsistenzbedingung: `0 <= p0 <= p01 <= 1` und Summe der drei Klassen = 1 innerhalb einer definierten Toleranz.
- Keine stillschweigende Begrenzung negativer Werte auf 0.
- Quote 0 und Quote 2+ allein reichen nicht zur eindeutigen Rekonstruktion von P1.
- `1 / p1_market` darf optional als rechnerische faire Quote angezeigt werden; es ist keine tatsächlich angebotene oder kaufbare Exakt-1-Quote. P1=0 ergibt keine endliche Quote.
- Bei Match-Total-Fallback müssen Linien zu `HT_total + 0,5` beziehungsweise `HT_total + 1,5` passen. `NEXT_GOAL_NONE` als Kaufquote ist nur bei belegter Gleichwertigkeit zulässig.
- `p1_stored`, `p1_recomputed`, Berechnungsmethode, Herkunft und Abweichung speichern. Ein ungeklärter Konflikt blockiert P1-basierte Regeln.
- Vorhandene Power-Normalisierung nur als zusätzliche Sensitivitätskennzahl, nicht als zweite unabhängige Prognose oder neue Modellpipeline.
- Überschneidung zwischen Referenzquoten und Kaufquoten kennzeichnen. Der daraus berechnete Puffer ist keine unabhängige Bestätigung der eigenen Quote.

### 6.3 Kombination `ZERO_OR_2PLUS`

Für Dezimalquoten `q0`, `q2` und Gesamteinsatz `S`:

```text
k = 1/q0 + 1/q2
s0_exact = S * (1/q0) / k
s2_exact = S - s0_exact
payout_covered_exact = S / k
win_roi_exact = 1/k - 1
p1_break_even_exact = 1 - k
p1_buffer_market = p1_break_even_exact - p1_market
```

Vor Kosten und Cent-Rundung gilt für eine tatsächliche Verlustwahrscheinlichkeit p1 bei gleich hoher gedeckter Auszahlung:

```text
expected_roi = (1 - p1) / k - 1
```

Beispiel ausschließlich zur Rechenprüfung: q0=4,00 und q2=2,00 ergeben k=0,75, Win-ROI=33,333… % und P1-Break-even=25 %. Bei einer tatsächlichen P1 von 30 % wäre der erwartete ROI −6,666… %, obwohl die Kombination 70 % der Spiele abdeckt.

P1-Break-even und Win-ROI sind monotone Umformungen derselben Größe. Sie dürfen nicht als zwei unabhängige neue Signale ausgegeben werden. Auch eine aus P1 abgeleitete faire Quote enthält gegenüber P1 keine zusätzliche Information.

Für Simulationen bestehende Cent-Aufteilung und Auszahlungsrundung aus `intelligence/strategy.py` wiederverwenden oder nachweislich identisch extrahieren. Kein zweiter leicht abweichender Rechner.

### 6.4 Tatsächliche Backtest-Abrechnung

```text
bei Y0: P/L = payout_zero - S - zusätzliche Kosten
bei Y1: P/L = -S - zusätzliche Kosten
bei Y2: P/L = payout_two_plus - S - zusätzliche Kosten
ROI = Summe(P/L) / Summe(Einsätze)
```

Kosten dürfen nicht doppelt abgezogen werden. Das Kostenprofil definiert eindeutig, ob ein Betrag Einsatz, Gewinn oder Auszahlung belastet. Es wird kein angeblich aktueller gesetzlicher oder anbieterspezifischer Kostensatz fest eingebaut.

Bei unterschiedlichen Quoten ist die Gesamt-Rendite aus den einzelnen Trades zu berechnen. Ein Vergleich von durchschnittlichem P1-Break-even und durchschnittlicher Verlustquote ersetzt diese Rechnung nicht. Mit Rundung oder ungleichen Auszahlungen ist auch eine Erwartungswertberechnung über p0, p1 und p2 mit den jeweiligen Auszahlungen vorzuziehen.

Standard: fester Gesamteinsatz von 10 EUR pro Spiel und Variante. Zusätzlich normierte Rendite zur Vergleichbarkeit. Keine Einsatzsteigerung nach Verlusten.

## 7. Historischer Einstieg und Ausführungssimulation

### 7.1 Startmodus: `HT_STABLE_ONCE`

Die erste Version vergleicht alle initialen Varianten am selben vorab festgelegten Beobachtungszeitpunkt:

- Verwende genau den zugehörigen HT_STABLE-Snapshot, sofern er tatsächlich in bestätigter Halbzeit liegt und die jeweiligen Datenanforderungen erfüllt.
- Fehlt er, kein nachträgliches Ausweichen auf die beste HALFTIME- oder spätere Quote.
- Pro Event und Variante maximal ein Einstieg.
- Ein nicht erfüllter Filter bedeutet NO_BET für diesen Entscheidungspunkt. Nicht anschließend so lange weitere Snapshots durchsuchen, bis die Variante doch noch auslöst.
- Auswahl erfolgt vor Kenntnis des Ergebnisses. Für die anfängliche Musteranalyse keine nachträglich gewählte „beste Minute“.

Das ist eine bewusst eingeschränkte, mit der vorhandenen Datenlage prüfbare Baseline. Sie unterscheidet sich von einer laufenden „erste passende Beobachtung im Zeitfenster“-Strategie und erhält deshalb eine eigene Entry-Policy-ID.

### 7.2 Historisch und live dasselbe Ereignis

Der vorhandene Collector erzeugt HT_STABLE über einen verzögerten Job. Die tatsächliche Implementierung und der konfigurierte Verzögerungswert müssen geprüft und versioniert werden. Aus dem Namen allein keine exakten 45 oder 60 Sekunden ableiten.

Die Paper-Variante muss dieselbe geteilte HT_STABLE-Beobachtung konsumieren können. Ihr darf nicht einfach das bestehende 0–120-Sekunden-Portfolio mit einem gleich benannten Quotenfilter zugeordnet werden, wenn dieses auf einem anderen Snapshot einsteigt.

Die Uhr für Fensterregeln beginnt bei der ersten bestätigten, verfügbaren HT-Beobachtung. Bei einem bereits in HZ gefundenen Spiel ist dies ein Zeitpunkt seit **Erkennung**, nicht seit tatsächlichem Halbzeitbeginn. Herkunft und Unsicherheit sichtbar halten.

### 7.3 Weitere Entry-Policies erst nach Baseline

`HALFTIME_ONCE` und echte zeitliche „erste passende Beobachtung“-Regeln sind optionale Erweiterungen. Sie sind eigene Experimente mit eigener Stichprobe und dürfen nicht still in den Basistest einfließen.

Eine HT→HT_STABLE-Quotenänderung darf erst zum späteren Zeitpunkt verwendet werden. Beide Beobachtungen müssen bis dahin verfügbar sein und dieselbe Tor-Zielsemantik haben. Die spärlichen Standard-Snapshots belegen keine lückenlose Intrahalbzeit-Preisentwicklung.

### 7.4 Ausführungsevidenz

Getrennte Profile:

1. `OBSERVED_SNAPSHOT`: hypothetische Annahme der gespeicherten Preise. Kennzeichnung „Snapshot-Simulation“.
2. `DELAY_REPLAY`: nur bei hinreichend dichten, zeitlich belegten Folgebeobachtungen; vorab definierte Latenz und Frischegrenze. Kein Folgepreis vorhanden bedeutet `EXECUTION_UNKNOWN`, nicht automatisch unveränderter Preis.
3. `PRICE_STRESS`: hypothetische Kürzung des Netto-Quotengewinns, z. B. `q_stress = 1 + (q - 1) * (1 - h)` für h=0 %, 1 %, 2 %. Als Sensitivität kennzeichnen, nicht als beobachtete Slippage.

Nach Preisänderungen Einsätze und Auszahlung neu berechnen. Ergebnisse für Baseline und Stress zeigen; nicht nachträglich das günstigste Ausführungsprofil wählen.

„Quote frisch“ ist relativ zum damaligen simulierten Entscheidungszeitpunkt zu prüfen, niemals relativ zur heutigen Uhr. Ist der tatsächliche Empfangszeitpunkt nicht vorhanden, eine entsprechende Annahme kennzeichnen.

## 8. Musteranalyse: P1 X gegenüber beobachtetem Y

### 8.1 Primäre Tabelle

Für jeden vorab definierten Bereich ausgeben:

| Kennzahl | Bedeutung |
|---|---|
| Beobachtete / auswertbare / ungeklärte Spiele | Nenner und Abdeckung |
| Mittelwert und Spannweite des Markt-P1 | Tatsächliche Werte innerhalb des Bereichs, nicht nur dessen Mitte |
| `observed_p1 = Summe(Y1) / N_resolved` | Historisch beobachteter Anteil exakt eines HZ2-Tores |
| `expected_count_1 = Summe(p1_market)` | Vom verwendeten Markt-P1 erwartete Anzahl |
| `observed_count_1 - expected_count_1` | Absolute Abweichung |
| `observed_p1 - mean(p1_market)` | Abweichung in Prozentpunkten; negativ bedeutet weniger beobachtete Ein-Tor-Ausgänge |
| Anteil 0 / 1 / 2+ | Vollständige Ergebnisverteilung |
| Covered-Hit-Rate und Anteil profitabler Trades | Unterschied zwischen abgedecktem Ausgang und Gewinn |
| Realisierter ROI und Netto-P/L | Ergebnis mit den jeweiligen Quoten und Einsätzen |
| Einsatzvolumen, typische Auszahlungen und Break-even-Spanne | Einordnung der Trefferquote |
| Unsicherheitsintervalle, Anzahl Tage und Wettbewerbe | Belastbarkeit und Konzentration |

`observed_p1` ist eine rückblickende Häufigkeit einer Gruppe, keine bekannte Wahrscheinlichkeit für ein zukünftiges einzelnes Spiel. Für die Anfangsversion kein dynamisches Ersetzen von Live-P1 durch diese Häufigkeit.

### 8.2 Darstellungen

- P1-Bereiche: vorhergesagter Mittelwert gegenüber tatsächlichem Ein-Tor-Anteil mit Intervallen.
- Daneben Rendite derselben Bereiche. Hohe Trefferquote und negative Rendite dürfen gleichzeitig sichtbar sein.
- Quoten-q0/q2-Tabelle oder Heatmap nur mit klaren Zellgrößen, Anzahl unterschiedlicher Spiele und Kennzeichnung dünner Zellen.
- Halbzeit-Torsumme als grobe Segmentierung; einzelne HT-Scores optional deskriptiv.
- Land, Liga und Datum als Drill-down. Filteränderungen erzeugen eine neue, als explorativ gekennzeichnete Ansicht und keinen nachträglich unabhängigen Test.
- Saisondimension nur, wenn Tipico eine verlässliche Saisonkennung liefert. Aus dem Kalenderjahr keine Spielzeit wie „25/26“ erfinden. Sonst „Saison unbekannt“.

### 8.3 Kleine Stichproben und fehlende Ergebnisse

- Jede Zelle anzeigen können, aber unter 30 unterschiedlichen abgeschlossenen Spielen als `VERY_LOW_SAMPLE` kennzeichnen und aus Kandidatenrankings ausschließen.
- 30 beziehungsweise später 100 Spiele sind Produkt-Schwellen für Lesbarkeit/Prüfbarkeit, keine statistischen Profitabilitätsnachweise.
- P1- und Trefferquoten mit 95%-Wilson-Intervall als einfache deskriptive Angabe. Abhängigkeiten zwischen Spielen zusätzlich über Tagesblöcke berücksichtigen, sobald ausreichend Tage vorhanden sind.
- Bei wenigen Tagen keine präzise wirkenden blockbasierten Konfidenzaussagen erzwingen. Methode und Zahl der Blöcke anzeigen; unter 14 verschiedenen Tagen standardmäßig `TEMPORAL_EVIDENCE_INSUFFICIENT`.
- Ergebnisausfälle je Segment ausweisen. Für bereits historisch entry-fähige, aber noch ergebnislose Spiele eine getrennte Best-/Worst-Case-Grenze berechnen, soweit Auszahlung und Marktsemantik bekannt sind.
- Offene und unbekannte Spiele nicht als verloren, gewonnen oder erstattet behandeln. Keine automatische Imputation oder Gewichtung ohne gesonderte Methodik.

Wilson-Intervalle sind eine Standardmethode für binomiale Anteile; die Grenzen und Voraussetzungen sind bei [NIST beschrieben](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm). Sie korrigieren weder Datenselektion noch die Suche nach dem besten von vielen Filtern.

## 9. Initiale Regelbibliothek

Die folgenden **18 Varianten sind vorab definierte Testhypothesen**, keine bereits aus dieser Datenbank abgeleiteten Gewinner. Für die erste historische Runde keine zusätzlichen Kreuzprodukte generieren.

### 9.1 Gemeinsames Vergleichsuniversum

- Entry-Policy `HT_STABLE_ONCE`.
- Regulieres Fußballspiel und eindeutig definierte HZ2-Ziele.
- Beide Kaufquoten und für die P1-Vergleiche ein konsistentes Markt-P1.
- Für das zentrale Kombinationsuniversum k<1, also positiver gedeckter Bruttogewinn vor Kosten.
- Alle Wettbewerbe grundsätzlich zugelassen. Semantisch abweichende Spielformate oder ungeklärte Spielzeiten anhand dokumentierter Metadaten ausschließen, nicht nach späterem P/L.
- Ein fixer Gesamteinsatz von 10 EUR je Variante und Spiel.
- Evidenzstufen getrennt.

Die Referenz-Einzelwetten werden auf genau denselben entry-fähigen Spielen und zu denselben Zeitpunkten wie die Kombinationsreferenz geprüft. Eine Variante mit Filter erhält zusätzlich einen Vergleich aller drei Wettformen auf genau ihrer Teilmenge. Dadurch wird eine andere Spielauswahl nicht mit einer besseren Einsatzkombination verwechselt.

| ID | Variante | Bedingung zusätzlich zum gemeinsamen Universum |
|---|---|---|
| R00 | Kombination 0 oder 2+ | Kein zusätzlicher Filter |
| R01 | Nur 0 Tore | Ganzer Einsatz auf dieselbe q0-Auswahl |
| R02 | Nur 2+ Tore | Ganzer Einsatz auf dieselbe q2-Auswahl |
| P10 | Kombination, P1 < 20 % | `0 <= p1 < 0.20` |
| P11 | Kombination, P1 20–25 % | `0.20 <= p1 < 0.25` |
| P12 | Kombination, P1 25–30 % | `0.25 <= p1 < 0.30` |
| P13 | Kombination, P1 30–35 % | `0.30 <= p1 < 0.35` |
| P14 | Kombination, P1 35–40 % | `0.35 <= p1 < 0.40` |
| P15 | Kombination, P1 ≥ 40 % | `0.40 <= p1 <= 1` |
| B10 | Kombination, niedriger P1-Break-even | `0 < p1_break_even < 0.20` |
| B11 | Kombination, mittlerer P1-Break-even | `0.20 <= p1_break_even < 0.30` |
| B12 | Kombination, höherer P1-Break-even | `0.30 <= p1_break_even < 1` |
| G10 | Kombination, Puffer unter −10 pp | `p1_buffer < -0.10` |
| G11 | Kombination, Puffer −10 bis −5 pp | `-0.10 <= p1_buffer < -0.05` |
| G12 | Kombination, Puffer ab −5 pp | `p1_buffer >= -0.05` |
| H10 | Kombination bei HT-Torsumme 0 | `ht_home + ht_away = 0` |
| H11 | Kombination bei HT-Torsumme 1 | `ht_home + ht_away = 1` |
| H12 | Kombination bei HT-Torsumme ≥ 2 | `ht_home + ht_away >= 2` |

Negativer marktseitiger Puffer ist in dieser Forschungsbibliothek kein globales Verbot: Die Varianten untersuchen gerade seine empirische Bedeutung. Das erlaubt aber keine Umgehung von Phase-, Quoten-, Identitäts- oder Settlement-Prüfungen.

Die vorhandene Familie `MARKET_STRUCTURE` ist dafür konzeptionell näher geeignet als eine pauschale `MARKET_ONLY`-Pflicht auf positiven Puffer. Der Implementierer muss neue Regelbedingungen ausdrücklich abbilden; bestehende Nutzerportfolios nicht umstellen.

### 9.2 Was vorerst nur explorativ bleibt

- q0-/q2-Bereiche als Tabellen; aus ihnen zunächst höchstens später separat registrierte grobe Kandidaten ableiten.
- Ligaspezifische Regeln wegen der kleinen Fallzahlen nicht als erste automatisch aktivierte Kandidaten.
- Overround, Referenzmethode und Referenz-/Kaufquotenüberschneidung als Diagnostik.
- HT→HT_STABLE-Änderungen erst nach bestandenem Zeit- und Semantik-Audit.
- Kein gleichzeitiges Optimieren von P1, zwei Quoten, Land, Liga, HT-Score und Einstiegssekunde.

## 10. Zeitliche Validierung und Schutz vor Zufallstreffern

### 10.1 Aktueller Bestand

Die vorliegende Woche ist zunächst ein **Entwicklungs- und Auditdatensatz**. Die Spec hat keine Renditegewinner oder ergebnisoptimierten Grenzen daraus ausgewählt.

Bei dieser kurzen Zeitspanne keine zufällige 80/20-Aufteilung als belastbaren unabhängigen Test präsentieren. Ein zweiter Spieltag innerhalb derselben Woche ist noch kein Beleg für Stabilität über Zeit oder Saisons.

### 10.2 Zukünftige Testperiode

Nach Fertigstellung des Audits und der Regeln einen zukünftigen UTC-Startzeitpunkt und eine feste Testperiode registrieren. Vorschlag als konfigurierbarer Anfangswert: 28 vollständige Tage. Pro Variante mindestens 100 abgeschlossene unterschiedliche Spiele für die Kategorie „bewertbar“; bei weniger Spielen lautet das Ergebnis `INSUFFICIENT_DATA`.

Diese Grenzen sind keine Zusicherung statistischer Sicherheit. Bei geringer Tradefrequenz können deutlich längere Zeiträume nötig sein. Eine Fortsetzung wird als neue vorab definierte Beobachtungsperiode registriert; nicht täglich so lange auf positive Signifikanz prüfen, bis sie erscheint.

Die Testperiode erst nach einem vorab festgelegten Ergebnis-Nachlauf auswerten, beispielsweise 72 Stunden. Der Nachlauf ist eine Wartefrist und kein automatischer Ergebnisbeleg. Noch ungeklärte Spiele bleiben sichtbar.

Während der laufenden Periode dürfen Betriebsstatus und vorläufige Zahlen sichtbar sein. Zwischenstände sind ausdrücklich vorläufig; sie erlauben weder einen vorgezogenen Erfolgsabschluss noch eine nachträgliche Regeländerung innerhalb dieses Tests. Ein Abbruch wegen technischer Fehler wird mit Grund registriert und nicht als profitable abgeschlossene Studie gezählt.

### 10.3 Auswahl und Testdaten trennen

- Grenzen, Varianten, Entry-Policy, Kosten, Auswahlkriterium und Datumsgrenzen vor dem Test einfrieren.
- Alle Snapshots und Varianten eines Events bleiben in derselben Zeitgruppe. Keine Event-Duplikate zwischen Entwicklungs- und Testdaten.
- Wenn später Walk-forward eingesetzt wird: Nur bis zum jeweiligen Stichtag tatsächlich bekannte Ergebnisse dürfen für Regelwahl oder empirische Auswertung verwendet werden.
- Auch Normalisierung, Imputation, Quantilgrenzen oder Filterauswahl nicht aus späteren Testdaten ableiten.
- Nach Einsicht in Testergebnisse gilt die Periode als verbraucht für neue Hypothesen. Neue Versionen brauchen zukünftige Testdaten.
- Ein neuer Studienname darf die Historie bereits probierter Regeln nicht löschen.

Die Trennung von Auswahl und späterer Prüfung ist auch ohne ML notwendig. Sie folgt demselben Leakage-Prinzip wie in den [scikit-learn-Empfehlungen](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage): Informationen aus der Prüfung dürfen die zu prüfende Regel nicht zuvor beeinflussen.

### 10.4 Unsicherheit und Mehrfachvergleiche

- ROI-Intervalle mit vorab definierter, reproduzierbarer Bootstrap-Methode; ganze UTC-Tagesblöcke und darin alle zugehörigen Events gemeinsam ziehen. Bei Variantenvergleichen dieselben gezogenen Blöcke verwenden.
- Wenige Tagesblöcke und dominierende Wettbewerbe als Einschränkung melden. Wilson-Intervalle für einzelne Häufigkeiten nicht als ROI-Intervalle verwenden.
- Alle getesteten Varianten einschließlich verworfener, fehlerhafter und leerer Varianten registrieren.
- Keine unbereinigten 95%-Intervalle über 18 Varianten als 18 unabhängige Profitabilitätsbeweise interpretieren.
- Für einen bestätigenden Familienvergleich entweder eine zuvor ausgewählte primäre Variante auf frischen Daten testen oder ein dokumentiertes simultanes Verfahren verwenden. Beispiel: gemeinsame Block-Bootstrap-Bänder; kein wechselndes Verfahren je nach günstigem Ergebnis.
- Ohne solche Bestätigung bleiben Ranglisten explorativ. Optional berichtete p-Werte benötigen ein festgelegtes Nullmodell, geeignete Abhängigkeitsbehandlung und z. B. Holm-Korrektur; eine Korrektur allein heilt kein ungeeignetes Testmodell.
- Ein Vergleich „besser als R00“ und ein Vergleich „absolute Rendite > 0“ sind unterschiedliche Aussagen und getrennt auszugeben.

## 11. Backtest-Ergebnis und Ranking

### Pflichtmetriken je Variante und Zeitraum

- Anzahl eligible, NO_BET, angenommener Entries, abgeschlossener Trades, VOID und ungeklärter Trades.
- Anzahl unterschiedlicher Spiele, UTC-Tage, Länder und Wettbewerbe.
- Ergebnisanteile 0/1/2+, Covered-Hit-Rate, Gewinn-/Verlust-/Null-P/L-Anteil.
- Mittelwert Markt-P1, beobachtetes P1, erwartete und beobachtete Ein-Tor-Anzahl.
- Gesamteinsatz, Rückflüsse, Brutto-/Netto-P/L, ROI und Unsicherheitsintervall.
- Kapitalverlauf bei fixem Einsatz, maximaler Rückgang und längste beobachtete Verlustserie.
- Profit Factor; bei fehlenden Verlusten als nicht endlich/noch nicht aussagekräftig kennzeichnen.
- Median und Verteilung von Quoten, P1-Break-even, Win-ROI und Puffer.
- Vergleich zu den drei Referenzwettformen auf gleicher Teilmenge.
- Ergebnisabdeckung, Evidenzstufe und Anteil von Annahmen.
- Ergebnis nach Datum und Wettbewerb; stärkste Konzentrationen sichtbar machen.

Eine Variante darf nach Trefferquote sortiert werden, wenn der Nutzer genau das untersuchen möchte. Standardmäßig aber keine Bezeichnung „beste Strategie“ allein anhand der Trefferquote. Ranglisten zeigen mindestens N, ROI, Unsicherheit und Evidenzstatus unmittelbar nebeneinander.

### Zwei verschiedene Kapitaldarstellungen

1. **Signalvergleich mit fixem Einsatz:** Keine Behauptung, dass historische Reserve- und Abrechnungszeitpunkte vollständig bekannt sind. P/L-Serie in transparent dokumentierter Reihenfolge.
2. **Bankroll-Replay:** Nur bei belastbaren historischen Entscheidungs-/Ergebnisverfügbarkeitszeiten. Reservierungen, parallele offene Trades und Kapitalmangel tatsächlich berücksichtigen; Gleichstände deterministisch auflösen.

Ungeklärte Trades im Bankroll-Modus weiter reserviert halten. Keine künstliche sofortige Kapitalfreigabe am angenommenen Spielende.

## 12. Backtest → Paper Trading

### 12.1 Gemeinsamer Regelkern

Ein reiner, deterministischer Evaluator muss historische und Live-Beobachtungen gleich behandeln:

```text
versionierte Regel + Entry-Beobachtung + expliziter Entscheidungszeitpunkt
    -> BET / NO_BET / INVALID mit Gründen
```

Unterschiede zwischen historischer Evidenzstufe und Live-Ausführung sind vorgeschaltete, explizite Zulassungsprüfungen. Keine doppelten Definitionen derselben P1-Grenze in UI, Backtest und Paper-Worker.

Jede Definition enthält mindestens:

- `study_id`, `variant_id`, Version, Parent-Version und Konfigurationshash.
- Wettform, Entry-Policy, erlaubte Provider/Sportart und Feature-Schema.
- Inklusive/exklusive Grenzwerte, Missing-Value-Verhalten und erlaubte Evidenzstufe.
- Einsatz- und Kostenprofil, Frischegrenze und Fensterdefinition.
- Entwicklungs-Dataset-ID sowie vorab festgelegte zukünftige Testperiode.
- Begründung der Hypothese und Zeitpunkt ihrer Registrierung.

Unbekannte Felder oder nicht unterstützte Operatoren müssen bei Import mit einer verständlichen Fehlermeldung scheitern. Beliebige Python-Ausdrücke in Regeldateien nicht ausführen.

### 12.2 Bestehende Implementierung berücksichtigen

Wiederverwenden beziehungsweise gezielt erweitern:

- `intelligence/probability.py`: Markt-Wahrscheinlichkeiten und Quellenprüfung.
- `intelligence/strategy.py`: Einsatzaufteilung und Szenario-Auszahlungen.
- `paper/market.py`: kohärente Marktbeobachtung und Entry-Validierung.
- `paper/entries.py`, `paper/engine.py`, `paper/results.py`: Entscheidung und Abrechnung.
- `paper/journal.py`: Beobachtungen, Versionierung und Entscheidungsbelege.
- `storage/parquet_archive.py`, `storage/database.py`: geprüfte schreibfreie Quellenadapter beziehungsweise additive Erweiterungen für neue Live-Belege.

Das aktuelle Paper-Modell ist auf `ZERO_OR_2PLUS` und begrenzte Filterparameter zugeschnitten. Ein P1-Band benötigt auch eine Untergrenze; ein q0/q2-Band gegebenenfalls Obergrenzen; die HT_STABLE-Policy braucht die konkrete gemeinsame Beobachtungsidentität. Nicht so tun, als seien diese Varianten bereits durch die bestehenden Formularfelder vollständig abbildbar.

Die Einzelwetten R01/R02 sind historisch verpflichtende Referenzen. Wenn sie im Paper Trading aktiviert werden sollen, müssen Wettform, Reservierung und Settlement ausdrücklich unterstützt sein. Keine Einzelwette als gefälschten Zwei-Leg-Trade speichern. Bis zur korrekten Unterstützung bleiben sie reine Backtest-/Shadow-Referenzen mit eigenem eindeutig benanntem Simulationsnachweis.

### 12.3 Kandidatenstatus

Getrennte Zustände vorsehen:

`DRAFT → BACKTESTED_EXPLORATORY → PAPER_CANDIDATE → PAPER_RUNNING → REVIEWED`

Bewertung separat, z. B.:

`INSUFFICIENT_DATA`, `NO_ADVANTAGE_OBSERVED`, `NEGATIVE_RESULT`, `PROMISING_EXPLORATORY`, `POSITIVE_IN_LOCKED_PERIOD`.

Ein positiver Testzeitraum bedeutet keinen garantierten künftigen Gewinn. Datenqualitätsfehler sind kein negativer Strategiebefund und erhalten einen eigenen Fehlerstatus.

Der Nutzer darf schwache oder unbestätigte Varianten bewusst virtuell testen. Kandidatenexport ist kein Profitabilitätssiegel. Die ersten Paper-Tests dürfen gerade der Gewinnung besserer Daten dienen.

## 13. Hermes-Betrieb auf dem Container

„Hermes“ bezeichnet hier den ausführenden Agenten beziehungsweise die vorhandene Container-Automatisierung. Diese Spec setzt keine bestimmte Hermes-API und keinen bereits installierten Scheduler voraus. Die Umsetzung liefert einen stabilen CLI-/Dateivertrag und dokumentiert die Anbindung an die tatsächlich vorhandene Umgebung.

### 13.1 Wiederholbarer Ablauf

1. Konsistenten neuen Datenstand und Manifest erzeugen.
2. Audit ausführen und neue Schema-/Qualitätsprobleme melden.
3. Nur neue oder revidierte Beobachtungen und Ergebnisbelege ableiten.
4. Bereits registrierte Varianten auf ihrem zulässigen Datenbereich auswerten.
5. Neue Daten getrennt zur laufenden zukünftigen Testperiode hinzufügen.
6. Reports und Fortschritt atomar veröffentlichen.
7. Nach dem festgelegten Ende der Testperiode einen Abschlussbericht schreiben.

Vorschlag: täglicher Audit und inkrementelle Auswertung; wöchentlicher zusammenhängender Statusbericht. Der Rhythmus ist konfigurierbar. Keine neue Automatisierung allein durch Lesen dieser Spec starten.

### 13.2 Was Hermes innerhalb einer Studie darf

- Registrierte Varianten wiederholt und idempotent ausführen.
- Fehlende Resultate innerhalb der vereinbarten Tipico-Nachlaufregeln prüfen.
- Fehler, Datenlücken, Resultatrevisionen und Abweichungen von Backtest-/Paper-Parität melden.
- Nach Testabschluss Kandidaten für eine neue zukünftige Studie vorschlagen.
- Bei ausdrücklich aktiviertem Auto-Paper-Modus neue virtuelle Portfolios innerhalb des definierten Kandidaten- und Ressourcenbudgets anlegen.

### 13.3 Was nicht automatisch verändert werden darf

- Laufende Regeln oder deren Testschnitt nach Einsicht in Ergebnisse.
- Ergebnisbelege, historische Quoten oder das Paper-Ledger, um bessere Ergebnisse zu erzeugen.
- Bestehende Nutzerportfolios oder aktive Konkurrenzvarianten.
- Live-Marktprüfungen zur Umgehung fehlender Daten.
- Die Historie misslungener Experimente oder verbrauchter Testperioden.
- Echtgeldeinstellungen oder Wettabgabe.

Auto-Paper ist eine explizite Konfiguration, standardmäßig aus. Nach einmaliger Aktivierung darf Hermes im vereinbarten Rahmen handeln; nicht für jede einzelne Wiederholung erneut fragen. Historische Auswertungen und Kandidatenexport benötigen keine laufenden Portfolios.

### 13.4 Grenzen der Versuchsanzahl und Last

- Erster Studienplan: die 18 genannten Varianten, kein Parameterkreuzprodukt.
- Pro neu registrierter Suchrunde standardmäßig höchstens 24 eindeutige Konfigurationen. Die kumulative Zahl bereits versuchter Konfigurationen bleibt über alle Runden sichtbar.
- Höchstens 6 gleichzeitig aktive neue Paper-Varianten, vorhandene Nutzerportfolios nicht mitzählen oder verändern. Referenzen und eine kleine Auswahl klar unterschiedlicher Kandidaten bevorzugen.
- Jede neue Konfiguration nach Sichtung eines Tests benötigt einen neuen zukünftigen Bewertungszeitraum. Ein höheres Versuchslimit erzeugt keinen neuen unberührten Testdatensatz.
- Alle Varianten konsumieren dieselben Marktbeobachtungen. 18 Strategien dürfen nicht 18 zusätzliche Detailabfragen je Spiel auslösen.
- Keine parallelen Vollscans gegen die Live-SQLite. Ein Quellenleser, abgeleitete Daten im eigenen Forschungsbereich, begrenzte CPU-/RAM-Nutzung und niedrige Priorität gegenüber Collector/Paper.
- Schemaänderungen, fehlerhafte Joins und Datensatzinkonsistenzen stoppen den betreffenden Forschungsjob mit eindeutigem Status. Laufende Collector-Dienste sollen dadurch nicht beendet werden.

## 14. Oberfläche

Ein neuer Bereich „Tipico Backtest“ mit vier Unteransichten reicht zunächst:

1. **Datenbasis:** Zeitraum, unterschiedliche Spiele, verifizierte und explorative Kandidaten, Ergebnisabdeckung und größte Lücken.
2. **P1 & Muster:** P1-Vergleichstabelle, Trefferquote, Rendite, Unsicherheit und Drill-down nach Land/Liga/Datum/HT-Score.
3. **Variantenvergleich:** Feste Regeln mit N, P/L, ROI, Unsicherheit, Drawdown, Evidenzstufe und Referenzvergleich.
4. **Spielbeleg & Export:** Ausgewählte Beobachtung, Marktquellen, Entscheidung, Einsatzaufteilung, Ergebnisbeleg und exportierbare Regeldefinition.

Details und technische Herkunft aufklappbar. Im Überblick verständliche Kennzahlen. Fehlende Daten nicht als 0 anzeigen. Reiter nur bei Bedarf laden; kein Research-Job bei jedem Streamlit-Rerun. Responsive Verhalten der vorhandenen Oberfläche übernehmen.

Ein „P1 25–30 %“-Klick muss die tatsächlich verwendeten Spiele zeigen können, einschließlich ausgeschlossener beziehungsweise ungeklärter Fälle und deren Gründen. Downloads dürfen nicht heimlich eine andere Stichprobe als die sichtbare Tabelle verwenden.

## 15. Schnittstellen und Artefakte

### 15.1 Vorgeschlagene Modulgrenzen

Ein eigener Bereich wie `tipico_research/` oder ein klar abgegrenztes Unterpaket ist geeignet. Die vorhandene ML-Factory nicht zur Voraussetzung machen.

Sinnvolle Verantwortlichkeiten: Quellenadapter, Audit, Datensatzaufbau, Feature-Berechnung, versionierte Regeln, Replay, Kennzahlen, Registry, Reports und CLI. Vorhandene Fachfunktionen wiederverwenden, ohne UI oder schreibende Services beim Datenlesen zu starten.

CLI-Fähigkeiten, konkrete Namen dürfen an Projektkonventionen angepasst werden:

- `audit`: Quelle prüfen.
- `build-dataset`: versionierte Beobachtungen/Labels ableiten.
- `run-study`: registrierte Varianten ausführen.
- `report`: vorhandenen Run ausgeben.
- `export-candidates`: validierte Regeldefinitionen exportieren.
- `paper-dry-run`: dieselben Regeln auf einem eingefrorenen Live-Beleg prüfen, ohne einen Trade anzulegen.

Parameter mindestens für Quelle, Archivwurzel, Output, Cutoff, Zeitraum, Studienplan, Evidenzstufe, Seed und Ressourcenbudget. Keine erfundenen lauffähigen Befehle dokumentieren, bevor deren Implementierung existiert.

### 15.2 Pflichtausgaben je Run

| Datei | Inhalt |
|---|---|
| `RUN_MANIFEST.json` | Hashes, Versionen, Cutoffs, Regeln, Seed und tatsächlicher Status |
| `TIPICO_DATA_AUDIT.md` | Datenbestand, Fehler, Ausschlüsse, offene Punkte |
| `TIPICO_COVERAGE.csv` | Tage/Wettbewerbe, Nenner, Labelabdeckung |
| `TIPICO_REJECTED_OBSERVATIONS.csv` | Source-ID, Event, Snapshot und Ausschlussgründe |
| `TIPICO_BACKTEST_DATASET.parquet` | Kanonischer, nachvollziehbarer Datenstand |
| `TIPICO_P1_CALIBRATION.csv` | Markt-P1 gegenüber beobachteter Häufigkeit; rein deskriptiv |
| `TIPICO_PATTERN_BUCKETS.csv` | Weitere feste Quoten-/Score-Segmente |
| `TIPICO_VARIANT_RESULTS.csv` | Alle Varianten einschließlich leerer/gescheiterter Versuche |
| `TIPICO_BACKTEST_TRADES.parquet` | Einzelergebnisse und Einstiegsbelege; CSV bei Bedarf |
| `TIPICO_BACKTEST_STATUS.md` | Verständliche Ergebnisse und deren Grenzen |
| `PAPER_CANDIDATES.json` | Regelkonfigurationen mit Version, Hash und Bewertungsstatus |
| `HERMES_TIPICO_RUNBOOK.md` | Installierte Befehle, Ablauf, Wiederaufnahme und Fehlerbehandlung |

Forschungsergebnisse nach Run-ID ablegen. Alten Run nicht durch denselben Dateinamen überschreiben. Ein `latest`-Verweis darf auf den letzten vollständig abgeschlossenen Run zeigen. Stabile CSV-Spalten, UTF-8 und ISO-Zeitstempel verwenden.

Alle vorgeschlagenen neuen Artefakte gehören zur späteren Umsetzung. Bei Erstellung dieser Spec wurden nur die Quellkopie gelesen und dieses Markdown-Dokument geschrieben.

## 16. Abnahmekriterien und Tests

### Daten und Zeit

1. Quellhash vor/nach Audit unverändert; kein schreibender Initialisierungspfad.
2. Audit auf dieser Kopie reproduziert die Basismengen aus Abschnitt 3. Abweichungen durch zusätzliche Filter werden als Funnel erklärt, nicht durch gelockerte Regeln beseitigt.
3. `FINAL + running/break` ohne unabhängigen Endbeleg erzeugt kein Settlement.
4. `HALFTIME + running` erzeugt ohne zeitlichen Phasennachweis keinen automatisch verifizierten HT-Entry.
5. `NULL` bei Scope wird nicht zu `False`.
6. Fehlende Einzelquoten-Nachweise erzeugen eine Evidenzabstufung; ein alter Eintrag allein schaltet Live-Prüfungen nicht ab.
7. Aktuelle Teamkarten, Scores oder Phasen können nicht in eine frühere Beobachtung gelangen.
8. Keine doppelte Zählung bei Archive/Outbox/SQLite-Überlappung oder wiederholtem Lauf.
9. Ergebnisänderungen erzeugen eine nachvollziehbare Revision.

### Rechenlogik

10. q0=4/q2=2 reproduziert die Werte aus Abschnitt 6 einschließlich Cent-Abrechnung für alle drei Zielklassen.
11. Trefferquote, profitabler Trade und ROI sind unterschiedliche Felder; ein Fall mit hoher Trefferquote und negativem ROI wird korrekt dargestellt.
12. P1 wird aus belegten Paaren berechnet; widersprüchliche Perioden und die bekannten flachen P1-Abweichungen werden erkannt.
13. Grenzwerte wie exakt 25 % landen deterministisch in genau dem definierten Band.
14. Kosten und Stress verändern Auszahlung und ROI korrekt und werden nicht doppelt berücksichtigt.
15. Variantenvergleich und Referenzvergleich verwenden nachweislich dieselben Events und Zeitpunkte.

### Studien- und Paper-Logik

16. Gleiche Eingabe, Konfiguration und Version erzeugen identische Entscheidungen und Ergebnisse.
17. Ein Event kann nicht in Entwicklungs- und Testsplit zugleich erscheinen.
18. Eine Regeländerung erzeugt neue Version/Hash und keine rückwirkende Änderung von Trades.
19. Zukünftige Testdaten beeinflussen keine Auswahlgrenzen; verbrauchte Perioden werden dokumentiert.
20. Zu wenig Daten ergibt `INSUFFICIENT_DATA`, keine Gewinnerkarte.
21. Backtest und Paper-Dry-Run entscheiden auf demselben vollständigen Marktbeleg gleich. Abweichungen durch Evidenz- oder Ausführungsmodus sind ausdrücklich begründet.
22. Wiederholte Hermes-Läufe erzeugen keine doppelten Trades oder Studienregistrierungen.
23. Mehr Varianten erhöhen nicht proportional die Tipico-Netzwerkabfragen.
24. Bestehende Start-, Collector-, Navigation- und Paper-Tests bleiben im betroffenen Umfang erfolgreich.

Der Abschlussbericht muss reale Eingabedaten, synthetische Rechentests und noch nicht erfolgte Container-Abnahme unterscheiden. Ein leerer strenger Datensatz bei sauber dokumentierten alten Datenlücken ist ein zulässiges Ergebnis und kein Anlass, Nachweise zu erfinden.

## 17. Reihenfolge der Umsetzung

### M1 – Audit und rekonstruierbare Grundlage

Quellen lesend erschließen, Audit/Manifest erzeugen, zeitliche Phasen- und Ergebnisprüfung implementieren, Evidenzstufen trennen. Als ersten Zwischenstand zeigen, wie viele Spiele für welche Aussage tatsächlich verwendbar sind.

### M2 – Fester Snapshot-Backtest

HT_STABLE_ONCE, gemeinsame Cent-Berechnung, drei Referenzwettformen, individuelle Trade-Belege und Resultatrevisionen. Keine Musteroptimierung vor korrekter Abrechnung.

### M3 – P1-Muster und 18 registrierte Varianten

Deskriptive Tabellen, Variantenbibliothek, Unsicherheit, identische Vergleichsstichproben und klare Grenzen kleiner Datenmengen.

### M4 – Paper-Parität und bessere zukünftige Belege

Regelimport, gleiche Entry-Policy und Beobachtungsidentität, vollständige zukünftige Markt-/Phasenbelege und überprüfbares Settlement. Falls der aktuelle Collector hierfür Felder verliert, die relevanten kleinen Beobachtungsbelege gezielt ergänzen, nicht wieder sämtliche Raw-Daten dauerhaft sammeln.

### M5 – Hermes und Oberfläche

Inkrementelle Läufe, Registry, zukünftige Testperiode, Ressourcensteuerung, Runbook und kompakte UI. Auto-Paper nur gemäß expliziter Konfiguration.

## 18. Auftrag an das umsetzende Modell

Lies zuerst den aktuellen Repository-Stand und diese Spec. Die Datenbankbeschreibung ist eine geprüfte Momentaufnahme, kein Ersatz für einen eigenen Audit auf dem tatsächlichen Eingabestand.

Implementiere M1 bis M5 innerhalb des beschriebenen Umfangs. Nutze die vorhandene Tipico-Normalisierung, Einsatzberechnung und Paper-Infrastruktur. Fehlende historische Nachweise werden offengelegt; die Forschungsfunktion soll trotzdem mit klar gekennzeichneten explorativen Daten nützlich sein. Bei kleinen Stichproben keine zusätzliche Modell- oder Parameterkomplexität einführen.

Ziel ist am Ende eine nachvollziehbare Antwort wie:

> „In diesem vorab festgelegten P1-Bereich hatten wir N unterschiedliche Spiele. Markt-P1 lag im Mittel bei X, tatsächlich fiel in Y Prozent genau ein HZ2-Tor. Zu den gespeicherten Quoten ergab das Z Prozent simulierten ROI. Datenqualität, Ergebnisabdeckung und Unsicherheit sehen folgendermaßen aus. Diese Regel ist nun unverändert für den nächsten Zeitraum als Paper-Test registriert.“

Die Werte X/Y/Z sind Platzhalter für später berechnete Ergebnisse, keine Behauptung über den vorhandenen Datenbestand.

## Anhang A – Reproduzierbare Kernabfragen der Spec-Prüfung

Diese SQL-Abfragen dokumentieren die Grundlage der genannten Mengen. Ausführung über eine schreibgeschützte SQLite-Verbindung. Sie sind Audit-Belege, keine fertige Backtest-Implementierung.

```sql
PRAGMA query_only = ON;
PRAGMA quick_check;

SELECT snapshot_type, COUNT(*) AS snapshots,
       COUNT(DISTINCT event_id) AS games,
       MIN(observed_at) AS first_observed_utc,
       MAX(observed_at) AS last_observed_utc,
       SUM(q_zero_best > 1 AND q_two_plus_best > 1) AS with_pair,
       SUM(p1_market IS NOT NULL) AS with_p1
FROM snapshots
GROUP BY snapshot_type;

SELECT s.snapshot_type, COUNT(*) AS snapshots,
       SUM(r.event_id IS NOT NULL) AS with_result,
       SUM(r.event_id IS NOT NULL
           AND s.q_zero_best > 1 AND s.q_two_plus_best > 1
           AND s.p1_market IS NOT NULL) AS pair_p1_result
FROM snapshots s
LEFT JOIN match_results r USING(event_id)
WHERE s.snapshot_type IN ('HALFTIME', 'HT_STABLE')
GROUP BY s.snapshot_type;

-- Vorläufige, noch nicht vollständig verifizierte Kandidaten: 308 / 375.
SELECT s.snapshot_type, COUNT(*) AS preliminary_candidates
FROM snapshots s
JOIN match_results r USING(event_id)
WHERE s.snapshot_type IN ('HALFTIME', 'HT_STABLE')
  AND s.match_status = 'break' AND s.display_time = 'HZ'
  AND s.q_zero_best > 1 AND s.q_two_plus_best > 1
  AND s.p1_market IS NOT NULL
  AND s.score_home = s.ht_score_home
  AND s.score_away = s.ht_score_away
  AND s.ht_score_home = r.ht_home AND s.ht_score_away = r.ht_away
GROUP BY s.snapshot_type;

SELECT snapshot_type, match_status, display_time, COUNT(*) AS n
FROM snapshots
WHERE snapshot_type IN ('HALFTIME', 'HT_STABLE')
GROUP BY snapshot_type, match_status, display_time;

SELECT match_status, extra_time, penalties, COUNT(*) AS n
FROM snapshots WHERE snapshot_type = 'FINAL'
GROUP BY match_status, extra_time, penalties;

SELECT SUM(ft_home < ht_home OR ft_away < ht_away) AS negative_team_h2,
       SUM(second_half_goals != ft_home + ft_away - ht_home - ht_away)
           AS inconsistent_h2,
       SUM(first_half_goals != ht_home + ht_away) AS inconsistent_h1
FROM match_results;

WITH p AS (
  SELECT snapshot_id, event_id, snapshot_type, p1_market,
         (1.0 / remaining_under_15)
           / (1.0 / remaining_under_15 + 1.0 / remaining_over_15)
         - (1.0 / remaining_under_05)
           / (1.0 / remaining_under_05 + 1.0 / remaining_over_05)
           AS p1_from_flat_columns
  FROM snapshots
  WHERE snapshot_type IN ('HALFTIME', 'HT_STABLE')
    AND p1_market IS NOT NULL
    AND remaining_under_05 > 1 AND remaining_over_05 > 1
    AND remaining_under_15 > 1 AND remaining_over_15 > 1
)
SELECT * FROM p WHERE ABS(p1_market - p1_from_flat_columns) > 0.000001;
```

Die beiden externen Methodikquellen in Abschnitt 8 und 10 dienen ausschließlich der Erklärung von Unsicherheit und zeitlicher Trennung. Es werden keine externen Sportdaten für den Datensatz oder die Varianten verwendet.
