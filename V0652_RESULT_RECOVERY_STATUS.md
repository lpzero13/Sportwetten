# V0.6.5.2 – Ergebnis-Recovery, Matching und Nachtlauf

Stand: 08.09.2026. Lokaler Bestand `Tipico DB/tipico.db`, 2.318 Fußball-Events.

## Ergebnis

385 zusätzliche Spiele besitzen jetzt ein geprüftes Endergebnis und ein
nutzbares H2-Ergebnislabel. Die Änderungen wurden erst auf SQLite-Kopien
getestet und danach mit frischen FotMob-Abrufen in die lokale Datenbank übernommen.

| Kennzahl | Vorher | Nachher |
|---|---:|---:|
| Fußball-Events | 2.318 | 2.318 |
| FT freigegeben (`result_use_ft=1`) | 865 | 1.250 |
| H2 freigegeben (`result_use_h2=1`) | 864 | 1.249 |
| Ohne freigegebenes FT | 1.453 | 1.068 |

Alle 1.250 FT-freigegebenen Spiele haben in `events.canonical_status` den
Wert `FINISHED`. Der rohe Tipico-Zustand bleibt als Herkunftsinformation erhalten.
Das ist keine pauschale Umbenennung alter Zwischenstände.

Die FT-Abdeckung des gesamten Bestands beträgt 53,9 %. Das 90-%-Ziel ist
**nicht erreicht**. Ein freigegebenes H2-Label ist außerdem noch kein vollständiger
Backtest-Datensatz: passende historische Quoten und ein gültiger Einstiegspunkt
müssen für die jeweilige Strategie zusätzlich vorhanden sein.

## Prüfung mehrerer Tage

Datum nach `Europe/Berlin`, jeweils eindeutige Tipico-Event-ID, nicht Snapshot-Zeilen.

| Spieltag | Events | FT vorher | FT nachher | Zugewinn |
|---|---:|---:|---:|---:|
| 30.08.2026 | 391 | 157 | 230 | 73 |
| 31.08.2026 | 167 | 83 | 113 | 30 |
| 01.09.2026 | 117 | 33 | 58 | 25 |
| 02.09.2026 | 193 | 87 | 96 | 9 |
| 03.09.2026 | 104 | 48 | 55 | 7 |
| 04.09.2026 | 217 | 88 | 104 | 16 |
| 05.09.2026 | 618 | 261 | 374 | 113 |
| 06.09.2026 | 511 | 108 | 220 | 112 |

Damit sind von den zuvor diskutierten 357 offenen Spielen am 05.09. nun 113
zusätzlich gelöst; 244 bleiben offen. Der Gesamterfolg kombiniert verbessertes
Matching und einen erneuten Abruf inzwischen abgeschlossener Spiele; nicht jeder
Zugewinn kann allein einem einzelnen Codefehler zugeschrieben werden.

Konkrete Ergänzungen außerhalb des 05.09.:

- 01.09.: Swansea City – FC Watford, HT 2:0, FT 2:0.
- 01.09.: Huddersfield Town – Oxford United, HT 0:0, FT 3:1.
- 06.09.: Kawasaki Frontale – Shimizu S-Pulse, HT 1:0, FT 3:1.
- 06.09.: Real Salt Lake – Los Angeles FC, HT 1:2, FT 2:2.

## Behobene Ursachen

1. Vollständige FotMob-Namen werden bevorzugt; fehlende/leere `longName`-Felder
   fallen sauber auf andere Namen zurück. Tatsächlich gelieferte Quell-Aliasse
   und das originale Fixture-JSON bleiben im Tagesindex erhalten.
2. Die Kandidatensuche blockiert nicht mehr die anschließende kontrollierte
   Namensprüfung durch einen vorherigen Exact-Match-Zwang. Die Akzeptanzgrenzen
   für Teamidentität, Uhrzeit, Land, Wettbewerb und Mehrdeutigkeit bleiben bestehen.
3. Frische Tageslisten ersetzen auch den In-Memory-Cache. Fehlerhafte Antworten
   ohne gültige `leagues`-Liste werden nicht als erfolgreicher leerer Tag behandelt.
4. Explizite länderspezifische Liga-Bezeichnungen werden zusammengeführt, zum
   Beispiel MLS/Major League Soccer. Bekannte unterschiedliche Gruppen werden
   nicht über Ähnlichkeit zusammengeführt. Diese Liste ist begrenzt, nicht weltweit vollständig.
5. Kurz-/Langnamen zwischen Tagesliste und Detailantwort werden nur bei gleicher
   Provider-Match-ID, Liga-ID und beiden Team-IDs übertragen. Ein generisches
   `INT` im Detail kann nur bei zusätzlich passenden Liga-Bezeichnungen durch
   das explizite Land der Tagesliste ergänzt werden.
6. Fehlende Ländercodes und die Tipico-Bezeichnung `V.A. Emirate` sind ergänzt.
7. Eine erneute lokale Prüfung bewahrt die Herkunft vorhandener Provider-Ergebnisse.
   Terminale Evidenz hat Vorrang vor einem stehen gebliebenen Live-/Pre-Match-Zustand;
   eine Absage wird dadurch nicht automatisch aufgehoben.
8. `--all-due` ist nicht mehr auf 500 Spiele begrenzt. Lokal gesetzte Retry-Zeiten
   verhindern nicht mehr die unmittelbar anschließende Provider-Prüfung derselben Prüfliste.
9. Wiederholte Namens- und Länder-Normalisierung ist begrenzt zwischengespeichert.

## Sofascore und nächste offene Arbeit

Die vom Nutzer angegebene [Sofascore-Tagesseite](https://www.sofascore.com/de/football/2026-09-05)
zeigt im Browser tatsächlich beendete Spiele. Stichproben Newcastle–Bournemouth
(2:2) und Manchester City–Coventry (1:0) stimmen mit den abgerufenen FotMob-Werten überein.
Das ist keine vollständige unabhängige Zweitprüfung aller 385 Ergänzungen.

Direkte HTTP-Abrufe der Seite und der untersuchten Tages-Endpunkte lieferten
hier HTTP 403. Deshalb wurde **kein ungetesteter automatischer Sofascore-Fallback**
für den Container aktiviert. Browser-Verfügbarkeit beweist keinen stabilen Serverzugriff.

Im endgültigen Lauf blieben 317 Fälle ohne Kandidaten der Vorfilterung,
714 ohne akzeptierte Zuordnung, 36 Detail-Identitätskonflikte, ein noch nicht
beendetes Spiel und ein nicht regulär abrechenbares Spiel. Diese 1.069
Queue-Ergebnisse schließen auch das bereits FT-freigegebene Spiel ohne H2-Label ein;
sie sind daher nicht mit den 1.068 fehlenden FT-Ergebnissen gleichzusetzen.
`NO_CANDIDATE` und `UNMATCHED` beweisen nicht, dass FotMob das Spiel nicht anbietet.

Der nächste sinnvolle Schritt ist eine belegte Review-Liste der verbleibenden
Team-/Liga-Aliasse sowie ein separat nachgewiesener zweiter Provider-Zugang.
Weder ähnliche Namen noch das Alter eines Spiels rechtfertigen ein erfundenes FT.

## Verifikation und lokale Artefakte

- Frischer Apply-Lauf: 33,09 Sekunden, zehn Worker, 433 HTTP-200-Antworten, null Abruffehler.
- Ergebnis der optimierten Wiederholung auf Ausgangskopie: identische 385 Ergänzungen.
- Idempotenztest auf bereits ergänzter Kopie: null weitere Übernahmen, Ergebniszeilen unverändert.
- `PRAGMA quick_check`: `ok`.
- Vorher vollständig FT/H2-freigegebene Ergebnisse einschließlich Herkunft: unverändert.
- Anzahl Events, Event-States und Snapshots: unverändert.
- Freigegebene H2-Zeilen: vollständige Scores, FT je Team mindestens HT,
  `second_half_goals = FT_home + FT_away - HT_home - HT_away` geprüft.
- Regressionstests für Cache, ungültige Antworten, Aliasse, Kategorien, Heim/Gast,
  Mehrdeutigkeit, Provider-IDs, Herkunft und `--all-due` ergänzt.
- Vollständiger Projekt-Testsatz: `python -m pytest tests -q`, **242 bestanden,
  1 übersprungen**, 31,53 Sekunden im abschließenden Lauf.
- Shell-Syntax von Installer und Aktivierungsskript mit Git Bash geprüft.
- Der entfernte Proxmox-Container ist von hier nicht getestet worden.

Lokale Nachweise, nicht Bestandteil von Git:

- `work/result-recovery-20260908-applied/summary.json`
- `work/result-recovery-20260908-applied/run.json`
- `work/result-recovery-20260908-applied/gained.json`
- `work/result-recovery-20260908-applied/responses/` (Originalantworten)
- `work/result-recovery-20260908-applied/tipico-before.db` (SQLite-Sicherung)
- `work/result-recovery-20260908-c/unresolved.json`
- `work/result-recovery-20260908-c/idempotency.json`

Reproduzierbarer Test auf einer neuen Kopie:

```bash
.venv/bin/python scripts/result_recovery_canary.py \
  --source data/tipico.db --output work/result-canary-neu
```

## Container-Update

Im vorhandenen Projektverzeichnis mit den bisher verwendeten Installationsrechten:

```bash
git pull --ff-only
bash deploy/install_proxmox.sh
systemctl start wetten-result-recovery.service
journalctl -u wetten-result-recovery.service -n 100 --no-pager
systemctl list-timers wetten-result-backfill.timer --all
```

Der einmalige Vollbestandsdienst berücksichtigt auch alte Fälle mit späterem
Retry-Termin. Danach läuft der Tagesdienst um **01:00 und 07:00 Uhr Europe/Berlin**.
Noch nicht beendete Provider-Spiele werden nach fünf Stunden wieder fällig;
unklare Identitäten behalten längere Prüfintervalle. Weder Datenbank noch Rohantworten
werden auf GitHub geladen; der Container bearbeitet seinen eigenen Bestand.
