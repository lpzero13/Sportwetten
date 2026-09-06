"""Reproducible Tipico-only audit, backtest, reports and paper dry-runs."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .metrics import simulate_trade, summarise_trades, wilson_interval
from .source import SourceAudit, TipicoSource
from .variants import (
    Variant,
    enrich_variant_row,
    evaluate_variant_entry,
    registered_variants,
    variant_config_hash,
)


RUNNER_VERSION = "tipico_backtest_v0.6.3"
DEFAULT_STAKE = 10.0
DEFAULT_BOOTSTRAP_ITERATIONS = 500
MIN_SAMPLE_GAMES = 30
PAPER_SAMPLE_GAMES = 100

STUDY_ARTIFACTS = (
    "RUN_MANIFEST.json",
    "TIPICO_DATA_AUDIT.md",
    "TIPICO_COVERAGE.csv",
    "TIPICO_COMPETITION_COVERAGE.csv",
    "TIPICO_REJECTED_OBSERVATIONS.csv",
    "TIPICO_BACKTEST_DATASET.parquet",
    "TIPICO_P1_CALIBRATION.csv",
    "TIPICO_PATTERN_BUCKETS.csv",
    "TIPICO_VARIANT_RESULTS.csv",
    "TIPICO_VARIANT_REFERENCE_RESULTS.csv",
    "TIPICO_VARIANT_BREAKDOWN.csv",
    "TIPICO_BACKTEST_TRADES.parquet",
    "TIPICO_BACKTEST_STATUS.md",
    "PAPER_CANDIDATES.json",
    "HERMES_TIPICO_RUNBOOK.md",
)

COVERAGE_FIELDS = [
    "coverage_scope", "date_utc", "competition_id", "competition_country",
    "competition_name", "snapshots", "different_games", "with_quotes",
    "with_p1", "entry_eligible", "resolved", "unresolved_entry_eligible",
    "phase_confirmed", "a_verified", "b_legacy", "c_pending",
]

REJECTED_FIELDS = [
    "source_dataset_id", "observation_id", "snapshot_identity", "event_id",
    "snapshot_id", "snapshot_type", "observed_at_utc", "competition_id",
    "competition_country", "competition_name", "home_team", "away_team",
    "primary_reject_reason", "quality_flags", "evidence_level",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def _atomic_write(path: Path, writer: Any) -> None:
    """Publish a single artifact with a same-directory replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    _atomic_write(path, lambda temporary: temporary.write_text(payload, encoding="utf-8"))


def _write_text(path: Path, value: str) -> None:
    payload = value.rstrip() + "\n"
    _atomic_write(path, lambda temporary: temporary.write_text(payload, encoding="utf-8"))


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    values = list(rows)
    if fields is None:
        fields = []
        for row in values:
            for key in row:
                if key not in fields:
                    fields.append(key)
    if not fields:
        fields = ["status"]

    def write(temporary: Path) -> None:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in values:
                writer.writerow({key: _csv_value(row.get(key)) for key in fields})

    _atomic_write(path, write)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)
    if isinstance(value, bool):
        return int(value)
    return value


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> str:
    """Write a flat research table with deterministic compression."""

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - requirements install path
        raise RuntimeError("pyarrow is required for the Tipico research Parquet artifacts") from exc
    clean = [
        {
            key: _csv_value(value) if isinstance(value, (list, dict, tuple, set)) else value
            for key, value in row.items()
        }
        for row in rows
    ]
    table = pa.Table.from_pylist(clean) if clean else pa.table({"event_id": pa.array([], type=pa.string())})

    def write(temporary: Path) -> None:
        pq.write_table(table, temporary, compression="zstd")

    _atomic_write(path, write)
    return "pyarrow"


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _short(value: Any, digits: int = 4) -> str:
    number = _safe_float(value)
    return "—" if number is None else f"{number * 100:.{digits}f}%"


def _run_id(value: str | None, prefix: str = "tipico-v063") -> str:
    if value:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
            raise ValueError("run-id darf nur Buchstaben, Zahlen, Punkt, Unterstrich und Bindestrich enthalten")
        return value
    return prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _selection_dict(*, from_date: str | None, to_date: str | None, cutoff_utc: str | None) -> dict[str, Any]:
    return {
        "snapshot_type": "HT_STABLE",
        "from_date_utc": str(from_date)[:10] if from_date else None,
        "to_date_utc": str(to_date)[:10] if to_date else None,
        "cutoff_utc": cutoff_utc,
    }


def _source_stat(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "size_bytes": stat.st_size,
        "modified_at_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _load_source(
    source_path: str | Path,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    cutoff_utc: str | None = None,
) -> tuple[SourceAudit, list[dict[str, Any]], str, str]:
    path = Path(source_path).expanduser().resolve()
    with TipicoSource(path) as source:
        before = source.sha256
        audit = source.audit(from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc)
        rows = [enrich_variant_row(row) for row in source.observations(
            from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc,
        )]
        after = source.refresh_sha256()
    return audit, rows, before, after


def _flatten_dataset_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _csv_value(value) for key, value in row.items()}


def _coverage_item(rows: list[dict[str, Any]], *, scope: str, date: str | None = None,
                   competition: tuple[str, str, str] | None = None) -> dict[str, Any]:
    selected = [
        row for row in rows
        if (date is None or str(row.get("observed_date_utc") or "UNKNOWN") == date)
        and (competition is None or (
            str(row.get("competition_id") or "UNKNOWN"),
            str(row.get("competition_country") or "UNKNOWN"),
            str(row.get("competition_name") or "UNKNOWN"),
        ) == competition)
    ]
    events = {row.get("event_id") for row in selected if row.get("event_id") is not None}
    return {
        "coverage_scope": scope,
        "date_utc": date if scope == "DATE" else None,
        "competition_id": competition[0] if competition else None,
        "competition_country": competition[1] if competition else None,
        "competition_name": competition[2] if competition else None,
        "snapshots": len(selected),
        "different_games": len(events),
        "with_quotes": sum(row.get("q_zero") is not None and row.get("q_two_plus") is not None for row in selected),
        "with_p1": sum(row.get("p1_market") is not None for row in selected),
        "entry_eligible": sum(bool(row.get("entry_eligible")) for row in selected),
        "resolved": sum(bool(row.get("result_valid")) for row in selected),
        "unresolved_entry_eligible": sum(bool(row.get("entry_eligible")) and not row.get("result_valid") for row in selected),
        "phase_confirmed": sum(bool(row.get("phase_confirmed")) for row in selected),
        "a_verified": sum(row.get("evidence_level") == "A_VERIFIED_REPLAY" for row in selected),
        "b_legacy": sum(row.get("evidence_level") == "B_LEGACY_EXPLORATORY" for row in selected),
        "c_pending": sum(row.get("evidence_level") == "C_UNUSABLE_OR_PENDING" for row in selected),
    }


def _coverage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    days = sorted({str(row.get("observed_date_utc") or "UNKNOWN") for row in rows})
    competitions = sorted({
        (
            str(row.get("competition_id") or "UNKNOWN"),
            str(row.get("competition_country") or "UNKNOWN"),
            str(row.get("competition_name") or "UNKNOWN"),
        )
        for row in rows
    }, key=lambda value: (value[1], value[2], value[0]))
    return [
        *[_coverage_item(rows, scope="DATE", date=day) for day in days],
        *[_coverage_item(rows, scope="COMPETITION", competition=competition) for competition in competitions],
    ]


def _competition_coverage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in _coverage_rows(rows) if row["coverage_scope"] == "COMPETITION"]


def _rejected_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: row.get(key) for key in REJECTED_FIELDS} for row in rows if row.get("primary_reject_reason")]


def _in_range(value: Any, lower: float | None, upper: float | None) -> bool:
    number = _safe_float(value)
    if number is None:
        return False
    if lower is not None and number < lower:
        return False
    if upper is not None and number >= upper:
        return False
    return True


def _bucket_summary(rows: list[dict[str, Any]], *, dimension: str, label: str,
                    field: str, lower: float | None, upper: float | None,
                    stake: float, cost: float, quote_haircut: float,
                    variant: Variant | None = None) -> dict[str, Any]:
    variant = variant or registered_variants()[0]
    selected = [
        row for row in rows
        if row.get("entry_eligible") and _in_range(row.get(field), lower, upper)
    ]
    resolved = [row for row in selected if row.get("result_valid")]
    trades = [
        simulate_trade(row, variant, stake=stake, cost=cost, quote_haircut=quote_haircut)
        for row in resolved
    ]
    summary = summarise_trades(
        trades,
        population=sum(bool(row.get("entry_eligible")) for row in rows),
        unresolved=len(selected) - len(resolved),
        no_bet=0,
        candidate_rows=selected,
        bootstrap_iterations=DEFAULT_BOOTSTRAP_ITERATIONS,
    )
    exact_one = sum(row.get("outcome_class") == "H2_GOALS_1" for row in resolved)
    expected = sum(float(row.get("p1_market") or 0) for row in resolved)
    return {
        "dimension": dimension,
        "bucket": label,
        "lower_inclusive": lower,
        "upper_exclusive": upper,
        "eligible_observations": len(selected),
        "eligible_different_games": len({row.get("event_id") for row in selected}),
        "resolved_games": len({row.get("event_id") for row in resolved}),
        "unresolved_observations": len(selected) - len(resolved),
        "mean_market_p1": sum(float(row.get("p1_market") or 0) for row in resolved) / len(resolved) if resolved else None,
        "market_p1_min": min((float(row["p1_market"]) for row in resolved if row.get("p1_market") is not None), default=None),
        "market_p1_max": max((float(row["p1_market"]) for row in resolved if row.get("p1_market") is not None), default=None),
        "observed_p1": exact_one / len(resolved) if resolved else None,
        "observed_p1_low_95": wilson_interval(exact_one, len(resolved))[0],
        "observed_p1_high_95": wilson_interval(exact_one, len(resolved))[1],
        "expected_exact_one_count": expected,
        "observed_minus_expected_count": exact_one - expected if resolved else None,
        "observed_minus_market_p1": (exact_one / len(resolved) - (expected / len(resolved))) if resolved else None,
        "h2_goals_0": sum(row.get("outcome_class") == "H2_GOALS_0" for row in resolved),
        "h2_goals_1": exact_one,
        "h2_goals_2_plus": sum(row.get("outcome_class") == "H2_GOALS_2_PLUS" for row in resolved),
        "covered_hit_rate": summary["covered_hit_rate"],
        "profitable_trade_rate": summary["profitable_trade_rate"],
        "total_stake": summary["total_stake"],
        "total_payout": summary["total_payout"],
        "pnl": summary["pnl"],
        "roi": summary["roi"],
        "roi_bootstrap_low_95": summary["roi_bootstrap_low_95"],
        "roi_bootstrap_high_95": summary["roi_bootstrap_high_95"],
        "days": summary["days"],
        "competitions": summary["competitions"],
        "status": "VERY_LOW_SAMPLE" if summary["resolved_different_games"] < MIN_SAMPLE_GAMES else "OK",
    }


def _p1_rows(rows: list[dict[str, Any]], *, stake: float, cost: float, quote_haircut: float) -> list[dict[str, Any]]:
    buckets = [(index / 20, (index + 1) / 20) for index in range(20)]
    return [
        _bucket_summary(
            rows, dimension="P1", label=f"{lower:.2f}-{upper:.2f}", field="p1_market",
            lower=lower, upper=upper, stake=stake, cost=cost, quote_haircut=quote_haircut,
        )
        for lower, upper in buckets
    ]


def _pattern_rows(rows: list[dict[str, Any]], *, stake: float, cost: float, quote_haircut: float) -> list[dict[str, Any]]:
    # Explicit bins are fixed before looking at outcomes; they are not learned quantiles.
    buckets: list[tuple[str, str, str, str, float | None, float | None]] = [
        ("P1", "P1", "0-20%", "p1_market", 0.00, 0.20),
        ("P1", "P1", "20-25%", "p1_market", 0.20, 0.25),
        ("P1", "P1", "25-30%", "p1_market", 0.25, 0.30),
        ("P1", "P1", "30-35%", "p1_market", 0.30, 0.35),
        ("P1", "P1", "35-40%", "p1_market", 0.35, 0.40),
        ("P1", "P1", "40-100%", "p1_market", 0.40, 1.01),
        ("Q0", "Quote 0", "1.01-2.00", "q_zero", 1.01, 2.00),
        ("Q0", "Quote 0", "2.00-3.00", "q_zero", 2.00, 3.00),
        ("Q0", "Quote 0", "3.00-4.00", "q_zero", 3.00, 4.00),
        ("Q0", "Quote 0", "4.00-5.00", "q_zero", 4.00, 5.00),
        ("Q0", "Quote 0", "5.00+", "q_zero", 5.00, None),
        ("Q2_PLUS", "Quote 2+", "1.01-1.50", "q_two_plus", 1.01, 1.50),
        ("Q2_PLUS", "Quote 2+", "1.50-2.00", "q_two_plus", 1.50, 2.00),
        ("Q2_PLUS", "Quote 2+", "2.00-2.50", "q_two_plus", 2.00, 2.50),
        ("Q2_PLUS", "Quote 2+", "2.50-3.00", "q_two_plus", 2.50, 3.00),
        ("Q2_PLUS", "Quote 2+", "3.00+", "q_two_plus", 3.00, None),
        ("HT_TOTAL", "HT-Torsumme", "0", "ht_goals", 0.00, 1.00),
        ("HT_TOTAL", "HT-Torsumme", "1", "ht_goals", 1.00, 2.00),
        ("HT_TOTAL", "HT-Torsumme", "2+", "ht_goals", 2.00, None),
    ]
    return [
        {
            **_bucket_summary(
                rows, dimension=dimension, label=label, field=field,
                lower=lower, upper=upper, stake=stake, cost=cost, quote_haircut=quote_haircut,
            ),
            "dimension_label": dimension_label,
        }
        for dimension, dimension_label, label, field, lower, upper in buckets
    ]


def _audit_markdown(audit: SourceAudit, rows: list[dict[str, Any]], selection: dict[str, Any] | None = None) -> str:
    observation_count = len(rows)
    result_count = sum(bool(row.get("result_valid")) for row in rows)
    entry_count = sum(bool(row.get("entry_eligible")) for row in rows)
    funnel = {
        "ht_stable": observation_count,
        "phase_confirmed": sum(bool(row.get("phase_confirmed")) for row in rows),
        "both_purchase_quotes": sum(row.get("q_zero") is not None and row.get("q_two_plus") is not None for row in rows),
        "stored_p1_valid": sum(row.get("p1_market") is not None and 0 <= float(row["p1_market"]) <= 1 for row in rows if row.get("p1_market") is not None),
        "entry_eligible": entry_count,
        "result_valid": result_count,
        "a_verified_replay": sum(row.get("evidence_level") == "A_VERIFIED_REPLAY" for row in rows),
        "b_legacy_exploratory": sum(row.get("evidence_level") == "B_LEGACY_EXPLORATORY" for row in rows),
        "c_unusable_or_pending": sum(row.get("evidence_level") == "C_UNUSABLE_OR_PENDING" for row in rows),
    }
    reject_counts = Counter(str(row.get("primary_reject_reason")) for row in rows if row.get("primary_reject_reason"))
    samples = [
        {
            "source_dataset_id": row.get("source_dataset_id"),
            "observation_id": row.get("observation_id"),
            "event_id": row.get("event_id"),
            "snapshot_id": row.get("snapshot_id"),
            "evidence_level": row.get("evidence_level"),
            "phase_confirmed": row.get("phase_confirmed"),
            "market_semantics_verified": row.get("market_semantics_verified"),
            "result_valid": row.get("result_valid"),
        }
        for row in rows if row.get("entry_eligible") and row.get("result_valid")
    ][:5]
    lines = [
        "# Tipico Data Audit",
        "",
        f"Quelle: `{audit.source_path}`",
        f"SHA-256: `{audit.source_sha256}`",
        f"SQLite quick_check: `{audit.quick_check}`",
        f"Auswahl: `{json.dumps(selection or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Additiver HT_STABLE-Funnel",
        "",
        "```json",
        json.dumps(funnel, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Ausschlussgründe",
        "",
        "```json",
        json.dumps(dict(sorted(reject_counts.items())), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Quellbestand und Abdeckung",
        "",
        "```json",
        json.dumps({
            "tables": audit.table_counts,
            "snapshot_counts": audit.snapshot_counts,
            "result_counts": audit.result_counts,
            "coverage_by_day": audit.coverage_by_day,
            "coverage_by_competition": audit.coverage_by_competition,
        }, ensure_ascii=False, indent=2, default=_json_default),
        "```",
        "",
        "## Findings",
        "",
    ]
    if audit.findings:
        for finding in audit.findings:
            lines.extend([
                f"### {finding['severity']}: {finding['code']}",
                "",
                f"Evidenz: {finding['evidence']}",
                "",
                f"Risiko: {finding['risk']}",
                "",
            ])
    else:
        lines.append("Keine Findings in den vordefinierten Prüfungen.")
    lines.extend([
        "## Stichproben-Belege",
        "",
        "```json",
        json.dumps(samples, ensure_ascii=False, indent=2),
        "```",
        "",
        "Die Quelle wurde über SQLite `mode=ro` und `PRAGMA query_only=ON` gelesen. Fehlende Ergebnisse bleiben pending; es wird nichts imputiert.",
    ])
    return "\n".join(lines)


def _status_markdown(run_id: str, manifest: dict[str, Any], audit: SourceAudit,
                     variant_results: list[dict[str, Any]]) -> str:
    lines = [
        f"# Tipico Backtest Status – {run_id}",
        "",
        f"Stand: {manifest['completed_at_utc']}",
        f"Runner: `{manifest['runner_version']}`",
        f"Quelle: `{manifest['source_sha256']}`",
        "",
        "## Ergebnis",
        "",
        "Dies ist ein Tipico-only `HT_STABLE_ONCE` Snapshot-Replay. Es verwendet keine FotMob-Daten, kein ML und keine Echtgeldaktion. Die Varianten sind vorab festgelegte Hypothesen; die Rangfolge ist explorativ.",
        "",
        "## Variantenvergleich",
        "",
        "| ID | Variante | Eligible | Entries | settled N | resolved games | NO_BET | unresolved | P1 beobachtet | ROI | ROI-Bootstrap | Drawdown | Evidenz | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in variant_results:
        interval = "—"
        if item.get("roi_bootstrap_low_95") is not None:
            interval = f"{_short(item['roi_bootstrap_low_95'], 1)} bis {_short(item['roi_bootstrap_high_95'], 1)}"
        evidence = "; ".join(f"{key}={value}" for key, value in (item.get("evidence_levels") or {}).items())
        drawdown = _safe_float(item.get("max_drawdown"))
        drawdown_text = "—" if drawdown is None else f"{drawdown:.2f} €"
        lines.append(
            f"| {item['variant_id']} | {item['variant_label']} | {item.get('population_entry_eligible', 0)} | {item.get('entries', 0)} | {item.get('resolved_trades', 0)} | {item.get('resolved_different_games', 0)} | {item.get('no_bet', 0)} | {item.get('unresolved', 0)} | {_short(item.get('observed_p1'), 1)} | {_short(item.get('roi'), 1)} | {interval} | {drawdown_text} | {evidence} | {item.get('minimum_sample_status')} / {item.get('temporal_evidence_status')} |"
        )
    lines.extend([
        "",
        "## Datenqualität",
        "",
        f"HT_STABLE: {manifest['dataset']['observations']}; Phase bestätigt: {manifest['dataset']['phase_confirmed']}; Ergebnis valide: {manifest['dataset']['resolved']}; Entry-eligible: {manifest['dataset']['entry_eligible']}",
        f"A/B/C: {manifest['dataset']['evidence_levels']}",
        f"Quelle unverändert gelesen: `{manifest['source_unchanged']}`; Hash vor/nach: `{manifest['source_sha256_before']}` / `{manifest['source_sha256_after']}`",
        "",
        "Unter 30 unterschiedlichen abgeschlossenen Spielen ist eine Zelle `VERY_LOW_SAMPLE`; unter 100 wird kein Paper-Kandidat empfohlen. Unter 14 UTC-Tagen ist die zeitliche Evidenz `TEMPORAL_EVIDENCE_INSUFFICIENT`.",
        "",
        "## Einschränkungen",
        "",
    ])
    if audit.findings:
        lines.extend(f"- {finding['severity']}: {finding['code']} – {finding['evidence']}" for finding in audit.findings)
    else:
        lines.append("- Keine Findings in den vordefinierten Prüfungen.")
    lines.extend([
        "",
        "P1-Break-even und Win-ROI sind Umformungen derselben Quotenstruktur. Trefferquote, Covered-Hit-Rate und ROI werden deshalb getrennt ausgewiesen.",
        "Ein positives Ergebnis ist nur eine Hypothese für einen neuen zukünftigen Paper-Zeitraum. `PAPER_CANDIDATES.json` aktiviert nichts automatisch.",
    ])
    return "\n".join(lines)


def _runbook(run_root: Path, manifest: dict[str, Any]) -> str:
    source = manifest["source_path"]
    return f"""# Hermes Runbook – Tipico Backtest V0.6.3

## Zweck

Die Forschungsstrecke liest eine konsistente Tipico-SQLite-Kopie schreibgeschützt. Sie erzeugt versionierte Audit-, Dataset-, Replay- und Kandidatenartefakte ohne Netzwerkzugriff und ohne Änderung an Live- oder Paper-Tabellen.

## Befehle

```powershell
python scripts/tipico_backtest.py audit --source \"{source}\"
python scripts/tipico_backtest.py build-dataset --source \"{source}\"
python scripts/tipico_backtest.py run-study --source \"{source}\"
python scripts/tipico_backtest.py report --run \"{run_root}\"
python scripts/tipico_backtest.py export-candidates --run \"{run_root}\"
python scripts/tipico_backtest.py paper-dry-run --run \"{run_root}\" --variant P12 --event-id <event-id>
```

Für eine begrenzte Periode `--from-date`, `--to-date` und/oder `--cutoff-utc` verwenden. Die Studie läuft standardmäßig mit 10 EUR Fixeinsatz; `--cost` und `--quote-haircut` sind explizite Sensitivitätsparameter.

## Betrieb

1. Produktionsdatenbank über die SQLite-Backup-API oder eine andere konsistente Kopie sichern; eine aktive WAL-Datei nicht blind kopieren.
2. `audit` ausführen und Ergebnis-/Phasenlücken prüfen.
3. `run-study` mit neuer Run-ID starten. Ein vorhandener Run wird nie überschrieben.
4. `STUDY_REGISTRY.json` und `PAPER_CANDIDATES.json` als unveränderliche Studienhistorie behandeln.
5. Nur registrierte Varianten auf einem neuen zukünftigen Zeitraum als Paper-Test ausführen.
6. Ergebnisnachlauf separat durchführen; geänderte Ergebnisbelege ergeben eine neue Dataset-/Run-Version.

## Automatisierungsgrenzen

Hermes darf registrierte Varianten idempotent wiederholen und Reports erzeugen. Es darf keine Grenzen, Testzeiträume, Quoten, Resultate, Ledgers oder Portfolios anhand eines günstigen Ergebnisses verändern. Auto-Paper bleibt aus; R01/R02 sind im Kandidatenexport reine Shadow-Referenzen, weil der bestehende Paper-Worker nur die Kombinations-Wettform unterstützt.

## Ressourcen und Wiederaufnahme

Die Quelle wird in einem Prozess gelesen. Ein fehlgeschlagener Lauf bleibt als unvollständiger Ordner sichtbar und wird nicht als `LATEST_RUN` veröffentlicht. Für die Wiederaufnahme eine neue Run-ID verwenden; Studienartefakte werden atomar geschrieben.

## Run-Identität

Aktueller Run: `{manifest['run_id']}`
Dataset: `{manifest['source_sha256']}`
Output: `{run_root}`
"""


def _selected_dataset_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = {
        level: sum(row.get("evidence_level") == level for row in rows)
        for level in ("A_VERIFIED_REPLAY", "B_LEGACY_EXPLORATORY", "C_UNUSABLE_OR_PENDING")
    }
    return {
        "observations": len(rows),
        "different_events": len({row.get("event_id") for row in rows}),
        "phase_confirmed": sum(bool(row.get("phase_confirmed")) for row in rows),
        "with_quotes": sum(row.get("q_zero") is not None and row.get("q_two_plus") is not None for row in rows),
        "with_p1": sum(row.get("p1_market") is not None for row in rows),
        "entry_eligible": sum(bool(row.get("entry_eligible")) for row in rows),
        "resolved": sum(bool(row.get("result_valid")) for row in rows),
        "unresolved_entry_eligible": sum(bool(row.get("entry_eligible")) and not row.get("result_valid") for row in rows),
        "evidence_levels": evidence,
    }


def _base_manifest(*, run_id: str, run_kind: str, source_path: Path,
                   audit: SourceAudit, rows: list[dict[str, Any]],
                   source_before: str, source_after: str,
                   selection: dict[str, Any], stake: float, cost: float,
                   quote_haircut: float, seed: int,
                   bootstrap_iterations: int) -> dict[str, Any]:
    return {
        "schema_version": "tipico_research_run_v1",
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "run_kind": run_kind,
        "status": "RUNNING",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source_path),
        "source_sha256": source_before,
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "source_unchanged": source_before == source_after,
        "source_file": _source_stat(source_path),
        "source_quick_check": audit.quick_check,
        "source_tables": audit.table_counts,
        "selection": selection,
        "entry_policy": "HT_STABLE_ONCE",
        "stake_eur": stake,
        "cost_eur": cost,
        "quote_haircut": quote_haircut,
        "seed": seed,
        "bootstrap_iterations": bootstrap_iterations,
        "dataset": _selected_dataset_summary(rows),
        "audit_findings": audit.findings,
        "selection_rules": {
            "snapshot_type": "HT_STABLE",
            "phase": "break + HZ + current score equals HT score",
            "result": "terminal Tipico match_results with consistent HT/FT arithmetic",
            "p1": "stored P1 present, 0 <= P1 <= 1, no recomputation conflict or inconsistent pair",
            "combination": "q0 and q2 > 1; positive covered payout before costs",
            "missing_result": "PENDING/UNRESOLVED, never imputed as loss",
        },
        "outputs": {},
    }


def _write_common_audit_outputs(run_root: Path, audit: SourceAudit, rows: list[dict[str, Any]], selection: dict[str, Any]) -> None:
    _write_text(run_root / "TIPICO_DATA_AUDIT.md", _audit_markdown(audit, rows, selection))
    coverage = _coverage_rows(rows)
    _write_csv(run_root / "TIPICO_COVERAGE.csv", coverage, fields=COVERAGE_FIELDS)
    _write_csv(run_root / "TIPICO_COMPETITION_COVERAGE.csv", _competition_coverage_rows(rows), fields=COVERAGE_FIELDS)
    _write_csv(run_root / "TIPICO_REJECTED_OBSERVATIONS.csv", _rejected_rows(rows), fields=REJECTED_FIELDS)


def _finalise_basic_run(run_root: Path, manifest: dict[str, Any], *, complete_status: str) -> None:
    manifest["status"] = complete_status
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_json(run_root / "RUN_MANIFEST.json", manifest)
    _write_json(run_root / "RUN_COMPLETE.json", {
        "run_id": manifest["run_id"],
        "run_kind": manifest["run_kind"],
        "status": complete_status,
        "completed_at_utc": manifest["completed_at_utc"],
    })
    manifest["outputs"] = {"files": sorted(path.name for path in run_root.iterdir() if path.is_file())}
    _write_json(run_root / "RUN_MANIFEST.json", manifest)


def run_audit(source_path: str | Path, output_root: str | Path, *, run_id: str | None = None,
              from_date: str | None = None, to_date: str | None = None,
              cutoff_utc: str | None = None) -> dict[str, Any]:
    """Write a standalone, read-only audit run."""

    source_path = Path(source_path).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    actual_run_id = _run_id(run_id, "tipico-audit")
    run_root = output_root / actual_run_id
    if run_root.exists():
        raise FileExistsError(f"Run output already exists: {run_root}; choose a new run id")
    run_root.mkdir(parents=True, exist_ok=False)
    selection = _selection_dict(from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc)
    audit, rows, before, after = _load_source(
        source_path, from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc,
    )
    manifest = _base_manifest(
        run_id=actual_run_id, run_kind="AUDIT", source_path=source_path, audit=audit,
        rows=rows, source_before=before, source_after=after, selection=selection,
        stake=DEFAULT_STAKE, cost=0.0, quote_haircut=0.0, seed=0, bootstrap_iterations=0,
    )
    _write_json(run_root / "RUN_MANIFEST.json", manifest)
    _write_common_audit_outputs(run_root, audit, rows, selection)
    _finalise_basic_run(run_root, manifest, complete_status="AUDIT_COMPLETED")
    _write_json(output_root / "LATEST_AUDIT.json", {"run_id": actual_run_id, "run_path": str(run_root)})
    return {
        "run_id": actual_run_id, "run_path": str(run_root),
        "source_sha256": before, "observations": len(rows),
        "findings": len(audit.findings), "files": manifest["outputs"]["files"],
    }


def build_dataset(source_path: str | Path, output_root: str | Path, *, run_id: str | None = None,
                  from_date: str | None = None, to_date: str | None = None,
                  cutoff_utc: str | None = None) -> dict[str, Any]:
    """Build only the versioned observation/label dataset and its audit."""

    source_path = Path(source_path).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    actual_run_id = _run_id(run_id, "tipico-dataset")
    run_root = output_root / actual_run_id
    if run_root.exists():
        raise FileExistsError(f"Run output already exists: {run_root}; choose a new run id")
    run_root.mkdir(parents=True, exist_ok=False)
    selection = _selection_dict(from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc)
    audit, rows, before, after = _load_source(
        source_path, from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc,
    )
    manifest = _base_manifest(
        run_id=actual_run_id, run_kind="DATASET", source_path=source_path, audit=audit,
        rows=rows, source_before=before, source_after=after, selection=selection,
        stake=DEFAULT_STAKE, cost=0.0, quote_haircut=0.0, seed=0, bootstrap_iterations=0,
    )
    _write_json(run_root / "RUN_MANIFEST.json", manifest)
    _write_common_audit_outputs(run_root, audit, rows, selection)
    _write_parquet(run_root / "TIPICO_BACKTEST_DATASET.parquet", [_flatten_dataset_row(row) for row in rows])
    _finalise_basic_run(run_root, manifest, complete_status="DATASET_BUILT")
    return {
        "run_id": actual_run_id, "run_path": str(run_root),
        "source_sha256": before, "observations": len(rows),
        "resolved": sum(bool(row.get("result_valid")) for row in rows),
        "files": manifest["outputs"]["files"],
    }


def _candidate_payload(variants: tuple[Variant, ...], run_id: str,
                      variant_results: list[dict[str, Any]], *,
                      study_manifest: dict[str, Any]) -> dict[str, Any]:
    by_id = {row["variant_id"]: row for row in variant_results}
    candidates = []
    for variant in variants:
        result = by_id.get(variant.variant_id, {})
        sample = int(result.get("resolved_different_games") or 0)
        roi = _safe_float(result.get("roi"))
        if sample < PAPER_SAMPLE_GAMES:
            evaluation_status = "INSUFFICIENT_DATA"
        elif roi is None:
            evaluation_status = "NO_RESULT_OBSERVED"
        elif roi <= 0:
            evaluation_status = "NEGATIVE_RESULT"
        else:
            evaluation_status = "PROMISING_EXPLORATORY"
        candidates.append({
            "study_id": run_id,
            "variant_id": variant.variant_id,
            "version": "1",
            "lifecycle_state": "BACKTESTED_EXPLORATORY",
            "parent_version": None,
            "config_hash": variant_config_hash(variant),
            "definition": variant.as_dict(),
            "entry_policy": "HT_STABLE_ONCE",
            "allowed_provider": "TIPICO",
            "allowed_sport": "FOOTBALL",
            "missing_value_policy": "NO_BET",
            "cost_profile": {"cost_eur": study_manifest.get("cost_eur", 0.0)},
            "evaluation_status": evaluation_status,
            "recommended_for_paper": False,
            "paper_compatible": variant.paper_compatible,
            "different_games": sample,
            "backtest_roi": roi,
            "backtest_evidence": result.get("evidence_levels", {}),
            "requires_new_future_period": True,
            "future_test_period": {"status": "UNREGISTERED", "minimum_games": PAPER_SAMPLE_GAMES},
            "auto_activate": False,
        })
    return {
        "schema_version": "tipico_paper_candidates_v1",
        "study_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "auto_activate": False,
        "source_dataset_id": study_manifest.get("source_sha256"),
        "candidates": candidates,
    }


def _variant_result(rows: list[dict[str, Any]], variant: Variant, *, stake: float,
                    cost: float, quote_haircut: float, seed: int,
                    bootstrap_iterations: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    eligible = [row for row in rows if row.get("entry_eligible")]
    decisions = [evaluate_variant_entry(row, variant) for row in eligible]
    selected = [row for row, decision in zip(eligible, decisions) if decision["accepted"]]
    trades = [
        simulate_trade(row, variant, stake=stake, cost=cost, quote_haircut=quote_haircut)
        for row in selected if row.get("result_valid")
    ]
    for trade in trades:
        trade["decision"] = "BET"
        trade["decision_reason"] = "VARIANT_ACCEPTED"
        trade["variant_config_hash"] = variant_config_hash(variant)
    summary = summarise_trades(
        trades,
        population=len(eligible),
        unresolved=sum(not row.get("result_valid") for row in selected),
        no_bet=len(eligible) - len(selected),
        candidate_rows=selected,
        seed=seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    summary.update({
        "variant_id": variant.variant_id,
        "variant_label": variant.label,
        "variant_description": variant.description,
        "bet_form": variant.bet_form,
        "variant_config_hash": variant_config_hash(variant),
        "decision_bet": sum(decision["decision"] == "BET" for decision in decisions),
        "decision_no_bet": sum(decision["decision"] == "NO_BET" for decision in decisions),
        "decision_invalid": sum(decision["decision"] == "INVALID" for decision in decisions),
    })
    return summary, trades, selected


def _reference_results(rows: list[dict[str, Any]], variants: tuple[Variant, ...], *,
                       stake: float, cost: float, quote_haircut: float,
                       seed: int, bootstrap_iterations: int) -> list[dict[str, Any]]:
    references = variants[:3]
    output: list[dict[str, Any]] = []
    eligible = [row for row in rows if row.get("entry_eligible")]
    for index, subset_variant in enumerate(variants):
        subset = [row for row in eligible if evaluate_variant_entry(row, subset_variant)["accepted"]]
        for ref_index, reference in enumerate(references):
            resolved = [row for row in subset if row.get("result_valid")]
            trades = [
                simulate_trade(row, reference, stake=stake, cost=cost, quote_haircut=quote_haircut)
                for row in resolved
            ]
            summary = summarise_trades(
                trades, population=len(subset), unresolved=len(subset) - len(resolved), no_bet=0,
                candidate_rows=subset, seed=seed + index * 3 + ref_index,
                bootstrap_iterations=bootstrap_iterations,
            )
            output.append({
                "comparison_subset_variant_id": subset_variant.variant_id,
                "comparison_subset_label": subset_variant.label,
                "reference_variant_id": reference.variant_id,
                "reference_variant_label": reference.label,
                "reference_bet_form": reference.bet_form,
                **summary,
            })
    return output


def _variant_breakdown(rows: list[dict[str, Any]], variants: tuple[Variant, ...], *,
                      stake: float, cost: float, quote_haircut: float,
                      bootstrap_iterations: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    eligible = [row for row in rows if row.get("entry_eligible")]
    for variant in variants:
        selected = [row for row in eligible if evaluate_variant_entry(row, variant)["accepted"]]
        groups: list[tuple[str, str, list[dict[str, Any]]]] = []
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_country: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_competition: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in selected:
            by_date[str(row.get("observed_date_utc") or "UNKNOWN")].append(row)
            by_country[str(row.get("competition_country") or "UNKNOWN")].append(row)
            by_competition[(str(row.get("competition_country") or "UNKNOWN"), str(row.get("competition_name") or "UNKNOWN"))].append(row)
        groups.extend(("DATE", key, value) for key, value in sorted(by_date.items()))
        groups.extend(("COUNTRY", key, value) for key, value in sorted(by_country.items()))
        groups.extend(("COMPETITION", f"{key[0]} / {key[1]}", value) for key, value in sorted(by_competition.items()))
        for dimension, key, segment_rows in groups:
            resolved = [row for row in segment_rows if row.get("result_valid")]
            trades = [
                simulate_trade(row, variant, stake=stake, cost=cost, quote_haircut=quote_haircut)
                for row in resolved
            ]
            summary = summarise_trades(
                trades, population=len(segment_rows), unresolved=len(segment_rows) - len(resolved), no_bet=0,
                candidate_rows=segment_rows, seed=6301, bootstrap_iterations=bootstrap_iterations,
            )
            output.append({
                "variant_id": variant.variant_id,
                "variant_label": variant.label,
                "segment_dimension": dimension,
                "segment_key": key,
                "competition_country": segment_rows[0].get("competition_country") if segment_rows else None,
                "competition_name": segment_rows[0].get("competition_name") if segment_rows else None,
                "eligible_entries": len(segment_rows),
                "eligible_games": len({row.get("event_id") for row in segment_rows}),
                "resolved_trades": len(trades),
                "resolved_games": len({row.get("event_id") for row in resolved}),
                "unresolved": len(segment_rows) - len(resolved),
                "h2_goals_0": summary["h2_goals_0"],
                "h2_goals_1": summary["h2_goals_1"],
                "h2_goals_2_plus": summary["h2_goals_2_plus"],
                "observed_p1": summary["observed_p1"],
                "covered_hit_rate": summary["covered_hit_rate"],
                "profitable_trade_rate": summary["profitable_trade_rate"],
                "total_stake": summary["total_stake"],
                "pnl": summary["pnl"],
                "roi": summary["roi"],
                "status": summary["minimum_sample_status"],
                "evidence_levels": summary["evidence_levels"],
            })
    return output


def _update_registry(output_root: Path, manifest: dict[str, Any], variant_results: list[dict[str, Any]]) -> None:
    path = output_root / "STUDY_REGISTRY.json"
    registry: dict[str, Any] = {"schema_version": "tipico_study_registry_v1", "studies": []}
    if path.is_file():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                registry.update(parsed)
        except (OSError, ValueError, TypeError):
            raise RuntimeError(f"Studien-Registry ist nicht lesbar: {path}")
    studies = [item for item in registry.get("studies", []) if isinstance(item, dict)]
    studies = [item for item in studies if item.get("run_id") != manifest["run_id"]]
    studies.append({
        "run_id": manifest["run_id"],
        "run_kind": manifest["run_kind"],
        "status": manifest["status"],
        "created_at_utc": manifest["created_at_utc"],
        "completed_at_utc": manifest.get("completed_at_utc"),
        "source_sha256": manifest["source_sha256"],
        "source_unchanged": manifest["source_unchanged"],
        "run_path": str(output_root / manifest["run_id"]),
        "dataset": manifest["dataset"],
        "variant_config_hashes": [item.get("variant_config_hash") for item in variant_results],
    })
    studies.sort(key=lambda item: str(item.get("created_at_utc") or ""))
    unique_hashes = {value for study in studies for value in study.get("variant_config_hashes", []) if value}
    registry = {
        "schema_version": "tipico_study_registry_v1",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_studies": len(studies),
        "total_unique_configurations": len(unique_hashes),
        "studies": studies,
    }
    _write_json(path, registry)


def run_study(source_path: str | Path, output_root: str | Path, *, run_id: str | None = None,
              stake: float = DEFAULT_STAKE, cost: float = 0.0,
              quote_haircut: float = 0.0, seed: int = 6301,
              bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
              from_date: str | None = None, to_date: str | None = None,
              cutoff_utc: str | None = None) -> dict[str, Any]:
    """Run audit, dataset build, fixed variants, references and reports."""

    if stake <= 0:
        raise ValueError("stake muss größer als 0 sein")
    if cost < 0:
        raise ValueError("cost darf nicht negativ sein")
    if not 0 <= quote_haircut < 1:
        raise ValueError("quote-haircut muss zwischen 0 und kleiner 1 liegen")
    if bootstrap_iterations < 1:
        raise ValueError("bootstrap-iterations muss mindestens 1 sein")
    source_path = Path(source_path).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    actual_run_id = _run_id(run_id)
    run_root = output_root / actual_run_id
    if run_root.exists():
        raise FileExistsError(f"Run output already exists: {run_root}; choose a new run id")
    run_root.mkdir(parents=True, exist_ok=False)
    selection = _selection_dict(from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc)
    audit, rows, before, after = _load_source(
        source_path, from_date=from_date, to_date=to_date, cutoff_utc=cutoff_utc,
    )
    variants = registered_variants()
    manifest = _base_manifest(
        run_id=actual_run_id, run_kind="STUDY", source_path=source_path, audit=audit, rows=rows,
        source_before=before, source_after=after, selection=selection, stake=stake,
        cost=cost, quote_haircut=quote_haircut, seed=seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    manifest["variants"] = [
        {**variant.as_dict(), "version": "1", "config_hash": variant_config_hash(variant)}
        for variant in variants
    ]
    manifest["required_outputs"] = list(STUDY_ARTIFACTS)
    _write_json(run_root / "RUN_MANIFEST.json", manifest)
    _write_common_audit_outputs(run_root, audit, rows, selection)
    _write_parquet(run_root / "TIPICO_BACKTEST_DATASET.parquet", [_flatten_dataset_row(row) for row in rows])

    variant_results: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    for variant in variants:
        summary, trades, _selected = _variant_result(
            rows, variant, stake=stake, cost=cost, quote_haircut=quote_haircut,
            seed=seed, bootstrap_iterations=bootstrap_iterations,
        )
        variant_results.append(summary)
        all_trades.extend(trades)
    _write_csv(run_root / "TIPICO_P1_CALIBRATION.csv", _p1_rows(rows, stake=stake, cost=cost, quote_haircut=quote_haircut))
    _write_csv(run_root / "TIPICO_PATTERN_BUCKETS.csv", _pattern_rows(rows, stake=stake, cost=cost, quote_haircut=quote_haircut))
    _write_csv(run_root / "TIPICO_VARIANT_RESULTS.csv", variant_results)
    reference_results = _reference_results(
        rows, variants, stake=stake, cost=cost, quote_haircut=quote_haircut,
        seed=seed, bootstrap_iterations=bootstrap_iterations,
    )
    _write_csv(run_root / "TIPICO_VARIANT_REFERENCE_RESULTS.csv", reference_results)
    breakdown = _variant_breakdown(
        rows, variants, stake=stake, cost=cost, quote_haircut=quote_haircut,
        bootstrap_iterations=bootstrap_iterations,
    )
    _write_csv(run_root / "TIPICO_VARIANT_BREAKDOWN.csv", breakdown)
    _write_parquet(run_root / "TIPICO_BACKTEST_TRADES.parquet", all_trades)
    candidates = _candidate_payload(variants, actual_run_id, variant_results, study_manifest=manifest)
    _write_json(run_root / "PAPER_CANDIDATES.json", candidates)

    manifest["outputs"] = {
        "dataset_parquet_backend": "pyarrow",
        "files": [],
        "study_artifacts": list(STUDY_ARTIFACTS),
    }
    manifest["status"] = "COMPLETED"
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_json(run_root / "RUN_MANIFEST.json", manifest)
    _write_text(run_root / "TIPICO_BACKTEST_STATUS.md", _status_markdown(actual_run_id, manifest, audit, variant_results))
    _write_text(run_root / "HERMES_TIPICO_RUNBOOK.md", _runbook(run_root, manifest))
    _write_json(run_root / "RUN_COMPLETE.json", {
        "run_id": actual_run_id, "run_kind": "STUDY", "status": "COMPLETED",
        "completed_at_utc": manifest["completed_at_utc"],
    })
    manifest["outputs"]["files"] = sorted(path.name for path in run_root.iterdir() if path.is_file())
    _write_json(run_root / "RUN_MANIFEST.json", manifest)
    _update_registry(output_root, manifest, variant_results)
    _write_json(output_root / "LATEST_RUN.json", {
        "run_id": actual_run_id, "run_path": str(run_root),
        "created_at_utc": manifest["created_at_utc"],
        "completed_at_utc": manifest["completed_at_utc"],
    })
    return {
        "run_id": actual_run_id,
        "run_path": str(run_root),
        "source_sha256": before,
        "source_unchanged": before == after,
        "observations": len(rows),
        "resolved": sum(bool(row.get("result_valid")) for row in rows),
        "entry_eligible": sum(bool(row.get("entry_eligible")) for row in rows),
        "variants": len(variants),
        "files": manifest["outputs"]["files"],
    }


def latest_run(output_root: str | Path) -> Path | None:
    root = Path(output_root).expanduser().resolve()
    marker = root / "LATEST_RUN.json"
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            path = Path(payload["run_path"])
            if (
                path.is_dir()
                and (path / "RUN_MANIFEST.json").is_file()
                and (path / "TIPICO_BACKTEST_STATUS.md").is_file()
                and (path / "RUN_COMPLETE.json").is_file()
            ):
                return path
        except (OSError, ValueError, KeyError, TypeError):
            pass
    runs = [
        path for path in root.glob("tipico-v063-*")
        if path.is_dir()
        and (path / "RUN_MANIFEST.json").is_file()
        and (path / "TIPICO_BACKTEST_STATUS.md").is_file()
        and (path / "RUN_COMPLETE.json").is_file()
    ]
    return max(runs, key=lambda path: path.stat().st_mtime) if runs else None


def read_run(run_path: str | Path) -> dict[str, Any]:
    path = Path(run_path).expanduser().resolve()
    manifest = json.loads((path / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    return {
        "path": str(path),
        "manifest": manifest,
        "status": (path / "TIPICO_BACKTEST_STATUS.md").read_text(encoding="utf-8") if (path / "TIPICO_BACKTEST_STATUS.md").is_file() else "",
    }


def export_candidates(run_path: str | Path) -> Path:
    path = Path(run_path).expanduser().resolve()
    candidate = path / "PAPER_CANDIDATES.json"
    if not candidate.is_file():
        raise FileNotFoundError(f"No candidate file in {path}")
    return candidate


def _load_evidence(path: Path, *, event_id: str | None = None) -> dict[str, Any]:
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as parquet
            values = parquet.read_table(path).to_pylist()
        except (ImportError, OSError, ValueError) as exc:
            raise RuntimeError(f"Evidence-Parquet konnte nicht gelesen werden: {path}") from exc
        candidates = [row for row in values if event_id is None or str(row.get("event_id")) == str(event_id)]
        if not candidates:
            raise ValueError(f"Kein Evidence-Eintrag für event_id={event_id!r} in {path}")
        return dict(candidates[0])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Evidence-JSON konnte nicht gelesen werden: {path}") from exc
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        for key in ("observation", "entry_observation", "entry", "data", "evidence"):
            if isinstance(payload.get(key), (dict, list)):
                values = payload[key]
                break
        else:
            values = payload
    else:
        raise ValueError("Evidence muss ein JSON-Objekt oder eine Liste sein")
    if isinstance(values, list):
        candidates = [row for row in values if isinstance(row, dict) and (event_id is None or str(row.get("event_id")) == str(event_id))]
        if not candidates:
            raise ValueError(f"Kein Evidence-Eintrag für event_id={event_id!r} in {path}")
        return dict(candidates[0])
    if not isinstance(values, dict):
        raise ValueError("Evidence enthält kein Objekt")
    if event_id is not None and str(values.get("event_id")) != str(event_id):
        raise ValueError(f"Evidence event_id={values.get('event_id')!r} passt nicht zu {event_id!r}")
    return dict(values)


def _entry_evidence_view(row: dict[str, Any]) -> dict[str, Any]:
    # Keep settlement labels/results out of the dry-run output.
    fields = (
        "source_dataset_id", "observation_id", "snapshot_identity", "snapshot_id",
        "event_id", "snapshot_type", "observed_at_utc", "available_at_utc",
        "decision_at_utc", "kickoff_at", "competition_id", "competition_country",
        "competition_name", "home_team", "away_team", "match_status",
        "display_time", "period", "score_home", "score_away", "ht_score_home",
        "ht_score_away", "extra_time", "penalties", "scope_explicit",
        "phase_confirmed", "phase_evidence", "q_zero", "q_two_plus",
        "q_zero_market_id", "q_zero_outcome_id", "q_two_plus_market_id",
        "q_two_plus_outcome_id", "p0_market", "p1_market", "p2plus_market",
        "p0_recomputed", "p01_recomputed", "p1_recomputed", "p1_break_even",
        "p1_buffer", "win_roi", "market_semantics_verified", "payload_hash",
        "entry_eligible", "evidence_level",
    )
    return {key: row.get(key) for key in fields if key in row}


def paper_dry_run(*, evidence_path: str | Path | None = None,
                  run_path: str | Path | None = None, event_id: str | None = None,
                  variant_id: str = "R00") -> dict[str, Any]:
    """Evaluate a frozen entry evidence without writing a trade or ledger row."""

    if evidence_path is None and run_path is None:
        raise ValueError("paper-dry-run benötigt --evidence oder --run")
    variant = next((item for item in registered_variants() if item.variant_id == variant_id), None)
    if variant is None:
        raise ValueError(f"Unbekannte Variante: {variant_id}")
    if evidence_path is not None:
        row = _load_evidence(Path(evidence_path).expanduser().resolve(), event_id=event_id)
    else:
        run = Path(run_path).expanduser().resolve()
        dataset = run / "TIPICO_BACKTEST_DATASET.parquet"
        if not dataset.is_file():
            raise FileNotFoundError(f"Kein Dataset-Parquet in {run}")
        row = _load_evidence(dataset, event_id=event_id)
    row = enrich_variant_row(row)
    decision = evaluate_variant_entry(row, variant, execution_mode="paper")
    return {
        "schema_version": "tipico_paper_dry_run_v1",
        "mode": "PAPER_DRY_RUN",
        "persisted": False,
        "trade_created": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "variant": {
            "variant_id": variant.variant_id,
            "version": "1",
            "config_hash": variant_config_hash(variant),
            "definition": variant.as_dict(),
        },
        "decision": decision,
        "entry_evidence": _entry_evidence_view(row),
    }
