# V0.6.5 – Tipico Result Finalization

> Hinweis: Dieser V0.6.5-Status ist der Vorgängerstatus. Der vollständige
> V0.6.5.1-Bestandslauf, die Ziel-DB-Nachpflege und die aktuellen Kennzahlen
> stehen in [V0651_FULL_DATABASE_RESULT_RECOVERY_STATUS.md](V0651_FULL_DATABASE_RESULT_RECOVERY_STATUS.md).

Stand: 07.09.2026

## Ergebnis

Die V0.6.5-Spec ist im Projekt umgesetzt. Rohbeobachtungen bleiben unverändert; die Nachpflege schreibt ausschließlich kanonische Status-, Ergebnisqualitäts-, Provenienz- und Auditfelder.

## Umgesetzt

- `ended`/`finished`/`final` werden als kanonischer Spielstatus `FINISHED` behandelt, wenn ein terminaler Beleg vorliegt.
- `no_longer_live` wird nicht durch Alter oder das letzte Zwischenresultat zu einem Endstand.
- FT- und H2-Freigabe sind getrennt. Ein verifiziertes FT ohne HT bleibt sichtbar, erhält aber kein H2-Label und wird nicht für H2-Paper-Settlement verwendet.
- Negative, nicht-ganzzahlige und `FT < HT`-Scores werden abgelehnt.
- Verlängerung, Elfmeterschießen, unbekannter Scope, Absage, Abbruch, HT-Konflikte und widersprüchliche FT-Belege bleiben gesperrt bzw. sichtbar.
- Lokale Tipico-Evidenz wird vor FotMob geprüft. Der bestehende FotMob-Backfill bleibt die einzige Provider-Pipeline.
- FotMob-Evidenz enthält jetzt Provider-Record, Rohstatus, Scope, Regelversion sowie FT/H2-Freigaben.
- Gleichlautende Wiederholungen sind idempotent; Score-Korrekturen und Konflikte erzeugen einen Audit-Eintrag.
- Additive Migrationen erweitern Alt-Datenbanken ohne historische Snapshots oder States umzubenennen.
- Der tägliche `wetten-result-backfill.timer` startet jetzt die Finalisierung plus FotMob-Reconciliation. Ein DB-Lock verhindert parallele Bestandsläufe.
- Der Datenbereich enthält Ergebnismetriken und einen Event-Grain-Explorer mit Filtern für Datum, Land, Liga, Rohstatus, kanonischen Status, Quelle und Ergebnisstatus sowie HT/FT-/Evidenzdetails.
- Backtest und Paper Trading lesen Ergebnisquelle, Qualität, Revision und Freigaben; unaufgelöste Ergebnisse erhalten keine H2-Klasse und werden nicht abgerechnet.

## CLI

```text
python scripts/result_finalization.py audit --root .
python scripts/result_finalization.py reconcile --root . --dry-run --mode cached
python scripts/result_finalization.py reconcile --root . --apply --all-due --mode worker --refresh-index
python scripts/result_finalization.py recheck --root . --event-id EVENT_ID --apply --mode worker
```

Jeder Lauf schreibt `RESULT_FINALIZATION_STATUS.md`, `RESULT_FINALIZATION_AUDIT.csv`, `RESULT_FINALIZATION_CHANGES.csv` und `RESULT_FINALIZATION_UNRESOLVED.csv` in `data/result_finalization/` (bzw. in `--out-dir`).

## Verifikation

Der schreibgeschützte Audit der lokalen Tipico-Kopie umfasste 2.318 Fußball-Events. Die Audit-Entscheidung erkannte 579 kanonisch beendete Events, 482 Live-/Halbzeitfälle, 390 geplante Events, 866 unbekannte Fälle und 1 Absage. Als Ergebnisstatus wurden 579 `VERIFIED`, 1 `UNAVAILABLE` und 1.738 `PENDING` ausgewiesen. Diese Zahlen sind Audit-Entscheidungen und keine stillschweigend in die Originaldatenbank geschriebenen Änderungen.

Ein isolierter Schreibtest auf einer DB-Kopie hat additive Migration, lokale Resultatreparatur, Evidenz, Queue und Änderungsprotokoll geprüft. Die Originaldatei `Tipico DB/tipico.db` wurde in diesem Verifikationslauf nicht verändert.

Teststand nach Implementierung:

- fokussierte Finalisierung/Backfill/Paper-Tests: **36 passed**
- vollständige Regression: **224 passed, 1 skipped**
- Syntaxprüfung der geänderten Python-Module: **PASS**
- `git diff --check`: **PASS**

Ein echter Container-PASS ist nicht behauptet: Der Proxmox-Container ist aus dieser Umgebung nicht erreichbar. Nach dem Deployment dort muss der Worker-Lauf mit `systemctl status wetten-result-backfill.service` und den erzeugten Reports beobachtet werden.
