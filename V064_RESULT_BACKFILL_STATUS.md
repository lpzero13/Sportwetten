# V0.6.4 – Tipico-Ergebnis-Nachpflege über FotMob

Stand: 2026-09-06
Status: **lokal validiert · Container-Deployment vorbereitet**

## Ziel

Tipico-Fußballereignisse ohne vollständigen Endstand werden nach einer
Schonfrist erneut gegen FotMob geprüft. Nur ein eindeutig zugeordnetes,
beendetes Spiel mit vollständigem Halbzeit- und Endstand sowie nachgewiesenem
Regular-Time-Scope darf in `match_results` ergänzt werden.

Die Nachpflege ist absichtlich additiv:

- vollständige Tipico-Ergebnisse werden nie durch FotMob überschrieben;
- Quoten, Snapshots und Halbzeit-/Statistikdaten werden nicht verändert;
- FotMob-Prüfungen werden separat in `result_backfill_evidence` gespeichert;
- offene, nicht zuordenbare oder noch nicht abgeschlossene Fälle bleiben in
  `result_backfill_queue` und erhalten einen kontrollierten Retry-Termin;
- fehlende Halbzeitdaten, Extra-Time-/Penalty-Fälle und unbekannter Scope werden
  standardmäßig nicht als reguläres Ergebnis übernommen.

## Container-Ablauf

Die neue systemd-Kombination besteht aus:

- `wetten-result-backfill.service`: einmaliger Worker-Lauf mit zehn Workern;
- `wetten-result-backfill.timer`: täglicher Start um 03:15 Uhr mit bis zu 15
  Minuten Zufallsversatz und `Persistent=true`;
- optionalem Nachladen fehlender FotMob-Tagesindex-Tage;
- Standardwerten `RESULT_BACKFILL_LIMIT=500` und
  `RESULT_BACKFILL_ALLOW_UNKNOWN_SCOPE=false`.

Der Dienst wird beim erneuten Installationslauf eingerichtet:

```bash
cd /opt/tipico-observer
git pull
sudo bash deploy/install_proxmox.sh
```

Prüfung auf dem Container:

```bash
systemctl status wetten-result-backfill.timer
systemctl start wetten-result-backfill.service
journalctl -u wetten-result-backfill.service -n 100 --no-pager
```

## Lokale Verifizierung

Getestete Quelldatei: `Tipico DB/tipico.db` (lokale Kopie, nicht Teil des
Repositories).

| Prüfung | Ergebnis |
|---|---:|
| Tipico-Events | 2.318 |
| bereits vorhandene `match_results` | 421 |
| fehlende/unvollständige Endstände vor dem Test | 1.897 |
| Netzwerk-Dry-Run, 25 Kandidaten | 6 validierte Treffer, 18 ohne Tagesindex-Kandidat, 1 nicht zugeordnet |
| isolierte Schreibkopie, 50 Kandidaten | 11 Ergebnisse ergänzt, 37 ohne Kandidat, 2 nicht zugeordnet |
| fehlende Endstände nach dem Schreibtest | 1.886 |
| gespeicherte Evidenzzeilen in der Schreibkopie | 50, davon 11 `VALID_REGULATION` |

Der Schreibtest lief auf einer separaten Kopie unter
`work/result-backfill-test-20260906/tipico.db`; die lokale Originaldatei blieb
unverändert. Damit ist der Datenbank-Schreibpfad nachgewiesen, ohne die
Backtest-Ausgangsbasis zu verändern.

## Qualitäts- und Regressionstests

- fokussierter Backfill-/FotMob-Lauf: **42 passed, 1 skipped**;
- vollständige Regression im Projekt-Virtualenv: **215 passed, 1 skipped**;
- Python-Kompilierung der neuen Worker-/Datenbankpfade erfolgreich;
- `bash -n deploy/install_proxmox.sh` erfolgreich;
- `git diff --check` ohne inhaltliche Fehler.

Die erste lokale Stichprobe ist bewusst kein Voll-Backfill. Der Container
arbeitet die Queue täglich in Batches ab; Fälle ohne aktuellen FotMob-Kandidaten
werden nach dem Retry-Plan erneut geprüft. Datenbank und Reports bleiben dabei
im Container und werden nicht in Git versioniert.
