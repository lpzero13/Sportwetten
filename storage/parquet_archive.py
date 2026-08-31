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
STRATEGY_ARCHIVE_SCHEMA_VERSION = "tipico_strategy_snapshots_v2"


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


STRATEGY_SCHEMA = (
    pa.schema(
        [
            _field("schema_version", pa.string(), nullable=False),
            _field("parser_version", pa.string(), nullable=False),
            _field("internal_match_id", pa.string()),
            _field("tipico_event_id", pa.string(), nullable=False),
            _field("snapshot_id", pa.int64(), nullable=False),
            _field("snapshot_type", pa.string(), nullable=False),
            _field("captured_at", pa.string(), nullable=False),
            _field("strategy_type", pa.string()),
            _field("strategy_status", pa.string()),
            _field("strategy_label", pa.string()),
            _field("strategy_version", pa.string()),
            _field("normalizer_version", pa.string()),
            _field("probability_status", pa.string()),
            _field("score_home", pa.int64()),
            _field("score_away", pa.int64()),
            _field("minute", pa.int64()),
            _field("period", pa.string()),
            _field("q0_best", pa.float64()),
            _field("q0_market_id", pa.string()),
            _field("q0_outcome_id", pa.string()),
            _field("q0_market_type", pa.string()),
            _field("q0_source_label", pa.string()),
            _field("q2_plus_best", pa.float64()),
            _field("q2_plus_market_id", pa.string()),
            _field("q2_plus_outcome_id", pa.string()),
            _field("q2_plus_market_type", pa.string()),
            _field("q2_plus_source_label", pa.string()),
            _field("market_p0", pa.float64()),
            _field("market_p1", pa.float64()),
            _field("market_p2_plus", pa.float64()),
            _field("p1_break_even", pa.float64()),
            _field("p1_buffer", pa.float64()),
            _field("win_roi", pa.float64()),
            _field("covered_payout", pa.float64()),
            _field("covered_profit", pa.float64()),
            _field("stake_total", pa.float64()),
            _field("stake_zero", pa.float64()),
            _field("stake_two_plus", pa.float64()),
            _field("payout_zero", pa.float64()),
            _field("payout_two_plus", pa.float64()),
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


def _internal_match_id(event_id: str) -> str:
    """Keep the strategy archive join key identical to ``fotmob.storage``."""

    digest = hashlib.sha256(f"TIPICO:{event_id}".encode("utf-8")).hexdigest()[:24]
    return f"match_{digest}"


def _snapshot_period(snapshot_type: Any, minute: Any) -> str:
    kind = str(snapshot_type or "").upper()
    if kind == "PRE_KICKOFF":
        return "PRE_MATCH"
    if kind in {"HALFTIME", "HT_STABLE"}:
        return "FIRST_HALF"
    try:
        value = int(minute)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return "FIRST_HALF" if value <= 45 else "SECOND_HALF"


def build_strategy_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project an outbox snapshot onto ``tipico_strategy_snapshots_v2``."""

    source = dict(payload)
    event_id = str(source.get("tipico_event_id") or source.get("event_id") or "")
    q0 = source.get("q0_best", source.get("q_zero_best"))
    q2 = source.get("q2_plus_best", source.get("q_two_plus_best"))
    payout_zero = source.get("payout_zero")
    payout_two = source.get("payout_two_plus")
    covered_payout = None
    if payout_zero is not None and payout_two is not None:
        try:
            covered_payout = min(float(payout_zero), float(payout_two))
        except (TypeError, ValueError):
            covered_payout = None
    return {
        "schema_version": STRATEGY_ARCHIVE_SCHEMA_VERSION,
        "parser_version": "tipico_strategy_archive_v2",
        "internal_match_id": source.get("internal_match_id") or _internal_match_id(event_id),
        "tipico_event_id": event_id,
        "snapshot_id": int(source.get("snapshot_id") or 0),
        "snapshot_type": source.get("snapshot_type"),
        "captured_at": source.get("captured_at"),
        "strategy_type": source.get("strategy_type") or "ZERO_OR_2PLUS",
        "strategy_status": source.get("strategy_status"),
        "strategy_label": source.get("strategy_label"),
        "strategy_version": source.get("strategy_version"),
        "normalizer_version": source.get("normalizer_version"),
        "probability_status": source.get("probability_status"),
        "score_home": source.get("score_home"),
        "score_away": source.get("score_away"),
        "minute": source.get("match_minute"),
        "period": source.get("period") or _snapshot_period(source.get("snapshot_type"), source.get("match_minute")),
        "q0_best": q0,
        "q0_market_id": source.get("q0_market_id") or source.get("q_zero_market_id"),
        "q0_outcome_id": source.get("q0_outcome_id") or source.get("q_zero_outcome_id"),
        "q0_market_type": source.get("q0_market_type") or source.get("q_zero_source_type"),
        "q0_source_label": source.get("q0_source_label"),
        "q2_plus_best": q2,
        "q2_plus_market_id": source.get("q2_plus_market_id") or source.get("q_two_plus_market_id"),
        "q2_plus_outcome_id": source.get("q2_plus_outcome_id") or source.get("q_two_plus_outcome_id"),
        "q2_plus_market_type": source.get("q2_plus_market_type") or source.get("q_two_plus_source_type"),
        "q2_plus_source_label": source.get("q2_plus_source_label"),
        "market_p0": source.get("market_p0", source.get("p0_market")),
        "market_p1": source.get("market_p1", source.get("p1_market")),
        "market_p2_plus": source.get("market_p2_plus", source.get("p2plus_market")),
        "p1_break_even": source.get("p1_break_even"),
        "p1_buffer": source.get("p1_buffer"),
        "win_roi": source.get("win_roi"),
        "covered_payout": source.get("covered_payout", covered_payout),
        "covered_profit": source.get("covered_profit"),
        "stake_total": source.get("stake_total"),
        "stake_zero": source.get("stake_zero"),
        "stake_two_plus": source.get("stake_two_plus"),
        "payout_zero": payout_zero,
        "payout_two_plus": payout_two,
    }


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
        "internal_match_id": _internal_match_id(str(event.event_id)),
        "tipico_event_id": str(event.event_id),
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
        "q0_best": zero.odds if zero is not None else None,
        "q0_market_id": zero.market_id if zero is not None else None,
        "q0_outcome_id": zero.outcome_id if zero is not None else None,
        "q0_market_type": zero.raw_market_type if zero is not None else None,
        "q0_source_label": strategy.source_zero if strategy is not None else (zero.source_label if zero is not None else None),
        "q_two_plus_best": two.odds if two is not None else None,
        "q_two_plus_source_type": two.raw_market_type if two is not None else None,
        "q_two_plus_market_id": two.market_id if two is not None else None,
        "q_two_plus_outcome_id": two.outcome_id if two is not None else None,
        "q2_plus_best": two.odds if two is not None else None,
        "q2_plus_market_id": two.market_id if two is not None else None,
        "q2_plus_outcome_id": two.outcome_id if two is not None else None,
        "q2_plus_market_type": two.raw_market_type if two is not None else None,
        "q2_plus_source_label": strategy.source_two_plus if strategy is not None else (two.source_label if two is not None else None),
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
        "strategy_type": strategy.strategy_type if strategy is not None else None,
        "strategy_status": strategy.status if strategy is not None else None,
        "strategy_label": strategy.label if strategy is not None else None,
        "probability_status": probability.status if probability is not None else None,
        "covered_payout": (
            min(strategy.payout_zero, strategy.payout_two_plus)
            if strategy is not None
            and strategy.payout_zero is not None
            and strategy.payout_two_plus is not None
            else None
        ),
        "covered_profit": strategy.covered_profit if strategy is not None else None,
        "stake_total": strategy.total_stake if strategy is not None else None,
        "stake_zero": strategy.stake_zero if strategy is not None else None,
        "stake_two_plus": strategy.stake_two_plus if strategy is not None else None,
        "payout_zero": strategy.payout_zero if strategy is not None else None,
        "payout_two_plus": strategy.payout_two_plus if strategy is not None else None,
        "period": _snapshot_period(snapshot.snapshot_type, snapshot.match_minute),
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
        self.strategy_root = self.root / "tipico" / "strategy"
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

    def _write_strategy_table(self, path: Path, rows: list[Mapping[str, Any]]) -> None:
        if pa is None or pq is None or STRATEGY_SCHEMA is None:
            raise RuntimeError("pyarrow is required for Parquet strategy export")
        records = [build_strategy_payload(row) for row in rows]
        temporary = path.with_name(path.name + ".tmp")
        if temporary.exists():
            temporary.unlink()
        table = pa.Table.from_pylist(records, schema=STRATEGY_SCHEMA)
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
                strategy_partition = (
                    self.strategy_root
                    / f"year={date_text[:4]}"
                    / f"month={date_text[5:7]}"
                    / f"date={date_text}"
                )
                strategy_partition.mkdir(parents=True, exist_ok=True)
                strategy_path = strategy_partition / (
                    f"tipico_strategy_{date_text}_{self._batch_hash(date_rows)}.parquet"
                )
                try:
                    records = [json.loads(str(row["payload_json"])) for row in date_rows]
                    if not self._file_matches(path, ids):
                        self._write_table(path, records)
                    if not self._file_matches(strategy_path, ids):
                        self._write_strategy_table(strategy_path, records)
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
            strategy_partition = (
                self.strategy_root
                / f"year={date_text[:4]}"
                / f"month={date_text[5:7]}"
                / f"date={date_text}"
            )
            strategy_partition.mkdir(parents=True, exist_ok=True)
            strategy_name = f"tipico_strategy_migration_{date_text}_{hashlib.sha256(value.encode()).hexdigest()[:16]}.parquet"
            strategy_path = strategy_partition / strategy_name
            if not self._file_matches(strategy_path, {int(row["snapshot_id"]) for row in date_rows}):
                self._write_strategy_table(strategy_path, date_rows)
            files.append(str(path))
        return {"files": files, "rows": len(rows)}
