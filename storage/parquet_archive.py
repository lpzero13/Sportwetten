"""Batch Parquet archive for the small, strategy-relevant snapshot rows."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - installation is declared in requirements
    pa = None
    pq = None

from intelligence.models import MarketAnalysis
from models.market import EventDetails
from models.snapshot import Snapshot
from storage.database import Database


ARCHIVE_SCHEMA_VERSION = "tipico_snapshot_v1"


def _field(name: str, type_: Any, *, nullable: bool = True) -> Any:
    return pa.field(name, type_, nullable=nullable) if pa is not None else None


SNAPSHOT_SCHEMA = (
    pa.schema(
        [
            _field("schema_version", pa.string(), nullable=False),
            _field("snapshot_id", pa.int64(), nullable=False),
            _field("event_id", pa.string(), nullable=False),
            _field("competition_id", pa.string()),
            _field("competition_name", pa.string()),
            _field("competition_country", pa.string()),
            _field("home_team", pa.string()),
            _field("away_team", pa.string()),
            _field("kickoff_at", pa.string()),
            _field("snapshot_type", pa.string(), nullable=False),
            _field("captured_at", pa.string(), nullable=False),
            _field("match_minute", pa.int64()),
            _field("score_home", pa.int64()),
            _field("score_away", pa.int64()),
            _field("ht_home", pa.int64()),
            _field("ht_away", pa.int64()),
            _field("first_half_goals", pa.int64()),
            _field("second_half_goals", pa.int64()),
            _field("second_half_goal_class", pa.string()),
            _field("match_status", pa.string()),
            _field("display_time", pa.string()),
            _field("snapshot_quality", pa.string()),
            _field("market_count", pa.int64(), nullable=False),
            _field("outcome_count", pa.int64(), nullable=False),
            _field("q_zero_best", pa.float64()),
            _field("q_zero_source_type", pa.string()),
            _field("q_zero_market_id", pa.string()),
            _field("q_zero_outcome_id", pa.string()),
            _field("q_two_plus_best", pa.float64()),
            _field("q_two_plus_source_type", pa.string()),
            _field("q_two_plus_market_id", pa.string()),
            _field("q_two_plus_outcome_id", pa.string()),
            _field("remaining_under_05", pa.float64()),
            _field("remaining_over_05", pa.float64()),
            _field("remaining_under_15", pa.float64()),
            _field("remaining_over_15", pa.float64()),
            _field("p0_market", pa.float64()),
            _field("p1_market", pa.float64()),
            _field("p2plus_market", pa.float64()),
            _field("p1_break_even", pa.float64()),
            _field("p1_buffer", pa.float64()),
            _field("win_roi", pa.float64()),
            _field("normalizer_version", pa.string()),
            _field("strategy_version", pa.string()),
            _field("relevant_markets_json", pa.string()),
            _field("goal_at", pa.string()),
            _field("reopen_at", pa.string()),
            _field("reopen_delay_seconds", pa.float64()),
            _field("raw_payload_path", pa.string()),
            _field("payload_hash", pa.string()),
        ]
    )
    if pa is not None
    else None
)


def _parse_minute(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(char for char in str(value) if char.isdigit())
    return int(digits) if digits else None


def _selected(analysis: MarketAnalysis | None, target: str) -> Any | None:
    if analysis is None:
        return None
    market = analysis.zero_equivalence if target == "zero" else analysis.two_plus_equivalence
    return market.best_odds.selected if market.best_odds is not None else None


def _quote_at(
    analysis: MarketAnalysis | None,
    canonical_type: str,
    line: float,
    *,
    fallback_types: tuple[str, ...] = (),
) -> float | None:
    if analysis is None:
        return None
    types = (canonical_type, *fallback_types)
    candidates = [
        item for item in analysis.normalized_outcomes
        if item.canonical_type in types
        and item.line is not None
        and abs(float(item.line) - line) < 1e-9
        and item.is_open
    ]
    if not candidates:
        return None
    return float(candidates[0].odds) if candidates[0].odds is not None else None


def _relevant_markets(analysis: MarketAnalysis | None) -> str:
    if analysis is None:
        return "[]"
    selected = []
    for item in (
        _selected(analysis, "zero"),
        _selected(analysis, "two"),
    ):
        if item is not None:
            selected.append(
                {
                    "canonical_type": item.canonical_type,
                    "scope": item.scope,
                    "period": item.period,
                    "line": item.line,
                    "odds": item.odds,
                    "market_id": item.market_id,
                    "outcome_id": item.outcome_id,
                    "raw_market_type": item.raw_market_type,
                    "raw_market_caption": item.raw_market_caption,
                    "raw_outcome_caption": item.raw_outcome_caption,
                }
            )
    for item in analysis.normalized_outcomes:
        if item.canonical_type in {
            "REMAINING_TOTAL_UNDER", "REMAINING_TOTAL_OVER",
        } and item.is_open and item.odds is not None:
            selected.append(
                {
                    "canonical_type": item.canonical_type,
                    "line": item.line,
                    "odds": item.odds,
                    "market_id": item.market_id,
                    "outcome_id": item.outcome_id,
                    "raw_market_type": item.raw_market_type,
                }
            )
    return json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_snapshot_payload(
    details: EventDetails,
    analysis: MarketAnalysis | None,
    snapshot: Snapshot,
) -> dict[str, Any]:
    """Flatten only the strategy-relevant part of an EventDetails response."""

    event = details.event
    zero = _selected(analysis, "zero")
    two = _selected(analysis, "two")
    ht_home = event.ht_score_home
    ht_away = event.ht_score_away
    total_home = event.score_home
    total_away = event.score_away
    first_half_goals = (
        int(ht_home + ht_away)
        if ht_home is not None and ht_away is not None
        else None
    )
    second_half_goals = snapshot.second_half_goals
    if (
        second_half_goals is None
        and total_home is not None and total_away is not None
        and ht_home is not None and ht_away is not None
    ):
        second_half_goals = int(total_home + total_away - ht_home - ht_away)
    strategy = analysis.strategy if analysis is not None else None
    probability = analysis.probability if analysis is not None else None
    payload: dict[str, Any] = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "snapshot_id": int(snapshot.snapshot_id or 0),
        "event_id": str(event.event_id),
        "competition_id": str(event.competition_id) if event.competition_id is not None else None,
        "competition_name": event.competition_name,
        "competition_country": event.competition_country,
        "home_team": event.home_team,
        "away_team": event.away_team,
        "kickoff_at": event.kickoff_time,
        "snapshot_type": snapshot.snapshot_type,
        "captured_at": snapshot.observed_at,
        "match_minute": _parse_minute(event.display_minute),
        "score_home": event.score_home,
        "score_away": event.score_away,
        "ht_home": ht_home,
        "ht_away": ht_away,
        "first_half_goals": first_half_goals,
        "second_half_goals": second_half_goals,
        "second_half_goal_class": snapshot.second_half_goal_class,
        "match_status": event.status,
        "display_time": event.display_minute,
        "snapshot_quality": snapshot.snapshot_quality,
        "market_count": int(details.market_count),
        "outcome_count": int(details.outcome_count),
        "q_zero_best": zero.odds if zero is not None else None,
        "q_zero_source_type": zero.raw_market_type if zero is not None else None,
        "q_zero_market_id": zero.market_id if zero is not None else None,
        "q_zero_outcome_id": zero.outcome_id if zero is not None else None,
        "q_two_plus_best": two.odds if two is not None else None,
        "q_two_plus_source_type": two.raw_market_type if two is not None else None,
        "q_two_plus_market_id": two.market_id if two is not None else None,
        "q_two_plus_outcome_id": two.outcome_id if two is not None else None,
        "remaining_under_05": _quote_at(
            analysis, "REMAINING_TOTAL_UNDER", 0.5, fallback_types=("MATCH_TOTAL_UNDER",)
        ),
        "remaining_over_05": _quote_at(
            analysis, "REMAINING_TOTAL_OVER", 0.5, fallback_types=("MATCH_TOTAL_OVER",)
        ),
        "remaining_under_15": _quote_at(
            analysis, "REMAINING_TOTAL_UNDER", 1.5, fallback_types=("MATCH_TOTAL_UNDER",)
        ),
        "remaining_over_15": _quote_at(
            analysis, "REMAINING_TOTAL_OVER", 1.5, fallback_types=("MATCH_TOTAL_OVER",)
        ),
        "p0_market": probability.p0 if probability is not None else None,
        "p1_market": probability.p1 if probability is not None else None,
        "p2plus_market": probability.p2_plus if probability is not None else None,
        "p1_break_even": strategy.p1_max if strategy is not None else None,
        "p1_buffer": strategy.p1_buffer if strategy is not None else None,
        "win_roi": strategy.win_roi if strategy is not None else None,
        "normalizer_version": analysis.normalized_outcomes[0].normalizer_version
        if analysis is not None and analysis.normalized_outcomes else None,
        "strategy_version": strategy.strategy_version if strategy is not None else None,
        "relevant_markets_json": _relevant_markets(analysis),
        "goal_at": snapshot.goal_at,
        "reopen_at": snapshot.reopen_at,
        "reopen_delay_seconds": snapshot.reopen_delay_seconds,
        "raw_payload_path": snapshot.raw_payload_path,
        "payload_hash": snapshot.payload_hash,
    }
    return payload


class ParquetArchive:
    """Export pending snapshot rows in crash-safe, date-partitioned batches."""

    def __init__(self, root: Path | str, *, compression: str = "zstd", logger: logging.Logger | None = None) -> None:
        self.root = Path(root)
        self.snapshot_root = self.root / "tipico" / "snapshots"
        self.compression = str(compression or "zstd").lower()
        self.logger = logger or logging.getLogger("tipico")
        self._lock = threading.RLock()
        self.last_export_at: str | None = None
        self.last_error: str | None = None

    @property
    def total_size_bytes(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return total

    def size_for_date(self, date_text: str) -> int:
        """Return the archive size for one UTC date partition."""

        total = 0
        partition = self.snapshot_root / f"year={date_text[:4]}" / f"month={date_text[5:7]}" / f"date={date_text}"
        if not partition.exists():
            return 0
        for path in partition.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return total

    def cleanup_temporary_files(self) -> int:
        """Remove only incomplete atomic-write leftovers from the archive."""

        removed = 0
        if not self.root.exists():
            return removed
        for path in self.root.rglob("*.tmp"):
            if not path.is_file():
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _partition(self, captured_at: str) -> Path:
        parsed = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        parsed = parsed.astimezone(timezone.utc)
        return (
            self.snapshot_root
            / f"year={parsed:%Y}"
            / f"month={parsed:%m}"
            / f"date={parsed:%Y-%m-%d}"
        )

    @staticmethod
    def _batch_hash(rows: list[Any]) -> str:
        value = "|".join(f"{row['snapshot_id']}:{row['payload_hash']}" for row in rows)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _write_table(self, path: Path, rows: list[Mapping[str, Any]]) -> None:
        if pa is None or pq is None:
            raise RuntimeError("pyarrow is required for Parquet snapshot export")
        table = pa.Table.from_pylist([dict(row) for row in rows], schema=SNAPSHOT_SCHEMA)
        temporary = path.with_name(path.name + ".tmp")
        if temporary.exists():
            temporary.unlink()
        pq.write_table(
            table,
            temporary,
            compression="zstd" if self.compression in {"zstd", "zst"} else self.compression,
            use_dictionary=True,
        )
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    def _file_matches(self, path: Path, snapshot_ids: set[int]) -> bool:
        if pa is None or pq is None or not path.exists():
            return False
        try:
            table = pq.read_table(path, columns=["snapshot_id"])
            found = {int(value.as_py()) for value in table.column("snapshot_id")}
            return found == snapshot_ids
        except Exception:
            return False

    def export_pending(self, database: Database, *, batch_size: int = 100) -> dict[str, int | str | None]:
        """Export at most one batch; failed writes leave the outbox untouched."""

        with self._lock:
            rows = database.pending_snapshot_outbox(batch_size)
            if not rows:
                return {"batches": 0, "snapshots_exported": 0, "pending": 0, "last_export_at": self.last_export_at, "errors": 0}
            grouped: dict[str, list[Any]] = defaultdict(list)
            for row in rows:
                grouped[str(row["captured_at"])[:10]].append(row)
            exported = 0
            batches = 0
            errors = 0
            for date_text, date_rows in grouped.items():
                ids = {int(row["snapshot_id"]) for row in date_rows}
                partition = self._partition(str(date_rows[0]["captured_at"]))
                partition.mkdir(parents=True, exist_ok=True)
                filename = f"tipico_snapshots_{date_text}_{self._batch_hash(date_rows)}.parquet"
                path = partition / filename
                try:
                    if not self._file_matches(path, ids):
                        records = [json.loads(str(row["payload_json"])) for row in date_rows]
                        self._write_table(path, records)
                    stamp = datetime.now(timezone.utc).isoformat()
                    exported += database.mark_snapshots_exported(ids, str(path), stamp)
                    database.delete_exported_snapshot_outbox()
                    self.last_export_at = stamp
                    batches += 1
                except Exception as exc:
                    errors += 1
                    self.last_error = str(exc)
                    database.mark_snapshot_outbox_error(ids, str(exc))
                    self.logger.exception("Parquet snapshot export failed for %s", date_text)
            pending = len(database.pending_snapshot_outbox(batch_size))
            return {
                "batches": batches,
                "snapshots_exported": exported,
                "pending": pending,
                "last_export_at": self.last_export_at,
                "errors": errors,
            }

    def write_records(self, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """Write migration records directly as one or more date-partitioned batches."""

        rows = [dict(item) for item in records]
        if not rows:
            return {"files": [], "rows": 0}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get("captured_at") or "")[:10]].append(row)
        files: list[str] = []
        for date_text, date_rows in grouped.items():
            date_rows.sort(key=lambda row: int(row.get("snapshot_id") or 0))
            partition = self._partition(date_rows[0]["captured_at"])
            partition.mkdir(parents=True, exist_ok=True)
            value = "|".join(str(row.get("snapshot_id")) for row in date_rows)
            name = f"tipico_snapshots_migration_{date_text}_{hashlib.sha256(value.encode()).hexdigest()[:16]}.parquet"
            path = partition / name
            if not self._file_matches(path, {int(row["snapshot_id"]) for row in date_rows}):
                self._write_table(path, date_rows)
            files.append(str(path))
        return {"files": files, "rows": len(rows)}
