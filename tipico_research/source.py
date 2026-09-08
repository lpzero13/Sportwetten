"""Read-only Tipico source adapter and historical observation builder."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable


P1_TOLERANCE = 1e-6
TERMINAL_STATUSES = {
    "FINISHED", "FINAL", "ENDED", "END", "COMPLETED", "SETTLED",
    "FULL_TIME", "COMPLETE", "NO_LONGER_LIVE_FINAL",
}


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result


def _parse_utc(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _utc_iso(value: Any) -> str | None:
    parsed = _parse_utc(value)
    return parsed.isoformat() if parsed else None


def _flag_is_zero(value: Any) -> bool:
    """Return whether a nullable provider flag explicitly means false/zero."""

    if value is None:
        return False
    if value is False:
        return True
    if isinstance(value, (int, float)):
        return value == 0
    return str(value).strip().lower() in {"0", "false", "no", "off"}


def _json_digest(value: Any) -> str | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _utc_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:10] if len(str(value)) >= 10 else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _read_json(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _normalised_pair(items: Iterable[dict[str, Any]], line: float) -> dict[str, Any] | None:
    """Return one same-market under/over pair for a remaining-goals line."""

    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in items:
        if str(item.get("canonical_type")) not in {
            "REMAINING_TOTAL_UNDER", "REMAINING_TOTAL_OVER",
        }:
            continue
        item_line = _float(item.get("line"))
        if item_line is None or abs(item_line - line) > P1_TOLERANCE:
            continue
        market_id = str(item.get("market_id") or "")
        outcome_id = str(item.get("outcome_id") or "")
        odds = _float(item.get("odds"))
        if not market_id or not outcome_id or odds is None or odds <= 1:
            continue
        side = "under" if item["canonical_type"].endswith("UNDER") else "over"
        candidate = {
            "market_id": market_id,
            "outcome_id": outcome_id,
            "odds": odds,
            "line": line,
            "canonical_type": item["canonical_type"],
            "raw_market_type": item.get("raw_market_type"),
            "raw_market_caption": item.get("raw_market_caption"),
            "raw_outcome_caption": item.get("raw_outcome_caption"),
            "scope": item.get("scope"),
            "period": item.get("period"),
        }
        # The compact serializer may contain the same outcome twice: first as
        # a selected quote and later with the complete period/scope metadata.
        # Prefer the richer representation while keeping the provider price
        # and outcome identity unchanged.
        existing = grouped[market_id].get(side)
        candidate_score = (
            bool(candidate.get("period")), bool(candidate.get("scope")),
            bool(candidate.get("raw_market_type")), bool(candidate.get("raw_market_caption")),
        )
        existing_score = (
            bool(existing and existing.get("period")), bool(existing and existing.get("scope")),
            bool(existing and existing.get("raw_market_type")), bool(existing and existing.get("raw_market_caption")),
        )
        if existing is None or candidate_score > existing_score:
            grouped[market_id][side] = candidate
    candidates = [
        value for value in grouped.values()
        if "under" in value and "over" in value
    ]
    if not candidates:
        return None
    # A complete pair is more useful than a partial/duplicate archive entry.
    selected = max(candidates, key=lambda pair: (
        pair["under"]["odds"] + pair["over"]["odds"],
        pair["under"]["market_id"],
    ))
    metadata_reconstructed = False
    for field in ("period", "scope"):
        under_value = selected["under"].get(field)
        over_value = selected["over"].get(field)
        if under_value is None and over_value is not None:
            selected["under"][field] = over_value
            metadata_reconstructed = True
        elif over_value is None and under_value is not None:
            selected["over"][field] = under_value
            metadata_reconstructed = True
    return {
        "line": line,
        "market_id": selected["under"]["market_id"],
        "under": selected["under"],
        "over": selected["over"],
        "metadata_reconstructed": metadata_reconstructed,
    }


def _probability_pair(pair: dict[str, Any] | None) -> float | None:
    if not pair:
        return None
    under = _float(pair.get("under", {}).get("odds"))
    over = _float(pair.get("over", {}).get("odds"))
    if under is None or over is None or under <= 1 or over <= 1:
        return None
    reciprocal_under = 1.0 / under
    reciprocal_over = 1.0 / over
    denominator = reciprocal_under + reciprocal_over
    return reciprocal_under / denominator if denominator > 0 else None


def _p1_from_pair_data(pair_05: dict[str, Any] | None, pair_15: dict[str, Any] | None) -> tuple[float | None, float | None, float | None]:
    p0 = _probability_pair(pair_05)
    p01 = _probability_pair(pair_15)
    if p0 is None or p01 is None:
        return p0, p01, None
    return p0, p01, p01 - p0


def _pair_semantics_verified(pair: dict[str, Any] | None) -> bool:
    """Require a same-market pair with explicit, matching period and scope."""

    if not pair:
        return False
    under = pair.get("under") or {}
    over = pair.get("over") or {}
    if not under or not over or under.get("market_id") != over.get("market_id"):
        return False
    period_under = str(under.get("period") or "").strip().upper()
    period_over = str(over.get("period") or "").strip().upper()
    scope_under = str(under.get("scope") or "").strip().upper()
    scope_over = str(over.get("scope") or "").strip().upper()
    return bool(period_under and period_under == period_over and scope_under and scope_under == scope_over)


def _score_equal(row: dict[str, Any]) -> bool:
    return all(
        row.get(left) is not None and row.get(left) == row.get(right)
        for left, right in (("score_home", "ht_score_home"), ("score_away", "ht_score_away"))
    )


@dataclass(slots=True)
class SourceAudit:
    source_path: str
    source_sha256: str
    quick_check: str
    table_counts: dict[str, int]
    snapshot_counts: list[dict[str, Any]]
    result_counts: list[dict[str, Any]]
    coverage_by_day: list[dict[str, Any]]
    coverage_by_competition: list[dict[str, Any]]
    findings: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "quick_check": self.quick_check,
            "table_counts": self.table_counts,
            "snapshot_counts": self.snapshot_counts,
            "result_counts": self.result_counts,
            "coverage_by_day": self.coverage_by_day,
            "coverage_by_competition": self.coverage_by_competition,
            "findings": self.findings,
        }


class TipicoSource:
    """A non-mutating connection to a Tipico SQLite copy."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Tipico SQLite source not found: {self.path}")
        self._connection: sqlite3.Connection | None = None
        self._sha256_value: str | None = None

    def __enter__(self) -> "TipicoSource":
        # ``mode=ro`` is important: Database() would run migrations and is not
        # allowed on a historical source.  Do not use immutable=1 because a
        # container source can be copied while its WAL is still active.
        uri = self.path.as_uri() + "?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        return self

    def __exit__(self, *_: Any) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("TipicoSource must be used as a context manager")
        return self._connection

    @property
    def sha256(self) -> str:
        if self._sha256_value is not None:
            return self._sha256_value
        digest = hashlib.sha256()
        with self.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        self._sha256_value = digest.hexdigest()
        return self._sha256_value

    def refresh_sha256(self) -> str:
        """Re-read the file hash for the before/after immutability check."""

        self._sha256_value = None
        return self.sha256

    def tables(self) -> set[str]:
        return {
            str(row[0]) for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    def count_rows(self, table: str) -> int:
        if table not in self.tables():
            return 0
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def quick_check(self) -> str:
        return str(self.connection.execute("PRAGMA quick_check").fetchone()[0])

    def _snapshot_rows(self, snapshot_type: str | None = None) -> list[dict[str, Any]]:
        if "snapshots" not in self.tables():
            return []
        where = "WHERE snapshot_type = ?" if snapshot_type else ""
        params: tuple[Any, ...] = (snapshot_type,) if snapshot_type else ()
        rows = self.connection.execute(
            f"SELECT * FROM snapshots {where} ORDER BY observed_at, snapshot_id",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def _result_rows(self) -> dict[str, dict[str, Any]]:
        if "match_results" not in self.tables():
            return {}
        return {
            str(row["event_id"]): dict(row)
            for row in self.connection.execute("SELECT * FROM match_results")
        }

    def _events(self) -> dict[str, dict[str, Any]]:
        if "events" not in self.tables():
            return {}
        return {
            str(row["event_id"]): dict(row)
            for row in self.connection.execute("SELECT * FROM events")
        }

    def _one_observation(self, raw: dict[str, Any], result: dict[str, Any] | None,
                         event: dict[str, Any] | None) -> dict[str, Any]:
        items = _read_json(raw.get("relevant_markets_json"))
        pair_05 = _normalised_pair(items, 0.5)
        pair_15 = _normalised_pair(items, 1.5)
        p0_recomputed, p01_recomputed, p1_recomputed = _p1_from_pair_data(pair_05, pair_15)
        p1_recompute_valid = bool(
            p0_recomputed is not None and p01_recomputed is not None and p1_recomputed is not None
            and -P1_TOLERANCE <= p0_recomputed <= 1 + P1_TOLERANCE
            and -P1_TOLERANCE <= p01_recomputed <= 1 + P1_TOLERANCE
            and p0_recomputed <= p01_recomputed + P1_TOLERANCE
            and -P1_TOLERANCE <= p1_recomputed <= 1 + P1_TOLERANCE
        )
        pair_semantics_verified = bool(
            _pair_semantics_verified(pair_05) and _pair_semantics_verified(pair_15)
        )
        pair_metadata_reconstructed = bool(
            (pair_05 and pair_05.get("metadata_reconstructed"))
            or (pair_15 and pair_15.get("metadata_reconstructed"))
        )
        q0 = _float(raw.get("q_zero_best"))
        q2 = _float(raw.get("q_two_plus_best"))
        stored_p1 = _float(raw.get("p1_market"))
        if q0 is None and pair_05:
            # This is a fallback for older rows; source type remains explicit.
            q0 = _float(pair_05["under"].get("odds"))
        if q2 is None and pair_15:
            q2 = _float(pair_15["over"].get("odds"))
        reciprocal_sum = (
            (1.0 / q0) + (1.0 / q2)
            if q0 is not None and q2 is not None and q0 > 1 and q2 > 1
            else None
        )
        p1_break_even = 1.0 - reciprocal_sum if reciprocal_sum is not None else None
        win_roi = 1.0 / reciprocal_sum - 1.0 if reciprocal_sum and reciprocal_sum > 0 else None
        p1_conflict = (
            stored_p1 is not None and p1_recomputed is not None
            and abs(stored_p1 - p1_recomputed) > P1_TOLERANCE
        )
        result_status = str((result or {}).get("final_status") or "").upper()
        quality_status = str((result or {}).get("result_status") or "").upper() or None
        result_source = (result or {}).get("result_source") or ("TIPICO_ORIGINAL" if result else None)
        final_home = _int((result or {}).get("ft_home"))
        final_away = _int((result or {}).get("ft_away"))
        ht_home_result = _int((result or {}).get("ht_home"))
        ht_away_result = _int((result or {}).get("ht_away"))
        ht_home = _int(raw.get("ht_score_home"))
        ht_away = _int(raw.get("ht_score_away"))
        score_home = _int(raw.get("score_home"))
        score_away = _int(raw.get("score_away"))
        phase_confirmed = (
            str(raw.get("match_status") or "").lower() == "break"
            and str(raw.get("display_time") or "").strip().upper() == "HZ"
            and _score_equal(raw)
        )
        result_terminal = result_status in TERMINAL_STATUSES and final_home is not None and final_away is not None
        result_ht_match = (
            result is not None and ht_home is not None and ht_away is not None
            and ht_home_result is not None and ht_away_result is not None
            and (ht_home, ht_away) == (ht_home_result, ht_away_result)
        )
        result_extra_time = (result or {}).get("extra_time")
        result_penalties = (result or {}).get("penalties")
        scope_explicit = _flag_is_zero(raw.get("extra_time")) and _flag_is_zero(raw.get("penalties"))
        scope_conflict = (
            raw.get("extra_time") is not None and not _flag_is_zero(raw.get("extra_time"))
        ) or (
            raw.get("penalties") is not None and not _flag_is_zero(raw.get("penalties"))
        )
        if result_terminal and final_home is not None and final_away is not None and ht_home is not None and ht_away is not None:
            h2_goals = final_home + final_away - ht_home - ht_away
        else:
            h2_goals = _int((result or {}).get("second_half_goals"))
        legacy_result_valid = bool(
            result_terminal and result_ht_match and h2_goals is not None and h2_goals >= 0
            and not scope_conflict
            and (result_extra_time is None or _flag_is_zero(result_extra_time))
            and (result_penalties is None or _flag_is_zero(result_penalties))
        )
        ft_result_valid = bool(
            result_terminal and final_home is not None and final_away is not None
            and final_home >= 0 and final_away >= 0
            and (result or {}).get("result_use_ft", 1) not in (False, 0, "0")
            and quality_status not in {"CONFLICT", "PENDING", "UNAVAILABLE"}
        )
        result_valid = bool(
            legacy_result_valid
            and (result or {}).get("result_use_h2", 1) not in (False, 0, "0")
            and quality_status not in {"CONFLICT", "PENDING", "UNAVAILABLE"}
        )
        if result is not None and (result or {}).get("result_use_h2") is None:
            result_valid = legacy_result_valid
        market_semantics = bool(
            pair_semantics_verified and q0 is not None and q2 is not None and p1_recompute_valid
        )
        p1_valid = stored_p1 is not None and 0 <= stored_p1 <= 1
        p1_pair_invalid = bool(pair_05 and pair_15 and not p1_recompute_valid)
        entry_eligible = bool(
            phase_confirmed and q0 is not None and q2 is not None and q0 > 1 and q2 > 1
            and p1_valid and not p1_conflict and not p1_pair_invalid and not scope_conflict
            and p1_break_even is not None and p1_break_even > 0
        )
        evidence_level = "C_UNUSABLE_OR_PENDING"
        if entry_eligible and result_valid:
            evidence_level = (
                "A_VERIFIED_REPLAY"
                if scope_explicit and market_semantics
                else "B_LEGACY_EXPLORATORY"
            )
        elif entry_eligible or (p1_valid and result_valid):
            evidence_level = "B_LEGACY_EXPLORATORY"

        reject_reasons: list[str] = []
        if result is None:
            reject_reasons.append("RESULT_MISSING")
        elif not result_valid:
            reject_reasons.append("RESULT_UNRESOLVED_OR_CONFLICT")
        if not phase_confirmed:
            reject_reasons.append("PHASE_UNCONFIRMED")
        if not _score_equal(raw):
            reject_reasons.append("HT_SCORE_MISSING_OR_CONFLICT")
        if q0 is None or q2 is None or q0 <= 1 or q2 <= 1:
            reject_reasons.append("MISSING_OR_INVALID_PURCHASE_QUOTES")
        if not p1_valid:
            reject_reasons.append("P1_MISSING_OR_INVALID")
        if p1_conflict:
            reject_reasons.append("P1_RECOMPUTATION_CONFLICT")
        if scope_conflict:
            reject_reasons.append("SETTLEMENT_SCOPE_CONFLICT")
        elif not scope_explicit:
            reject_reasons.append("SETTLEMENT_SCOPE_UNKNOWN")
        if p1_pair_invalid:
            reject_reasons.append("P1_PAIR_INCONSISTENT")
        if p1_valid and not p1_conflict and not market_semantics:
            reject_reasons.append("P1_REFERENCE_PAIR_UNVERIFIED")
        historical_period = raw.get("period")
        if historical_period is None and pair_05:
            historical_period = (pair_05.get("under") or {}).get("period")
        snapshot_id = _int(raw.get("snapshot_id"))
        source_dataset_id = self.sha256
        observation_id = _json_digest({
            "source_dataset_id": source_dataset_id,
            "snapshot_id": snapshot_id,
            "event_id": str(raw.get("event_id") or ""),
        })
        return {
            "source_dataset_id": source_dataset_id,
            "observation_id": observation_id,
            "snapshot_identity": f"{source_dataset_id}:{snapshot_id}" if snapshot_id is not None else None,
            "snapshot_id": snapshot_id,
            "event_id": str(raw.get("event_id") or ""),
            "snapshot_type": raw.get("snapshot_type"),
            "observed_at_utc": _utc_iso(raw.get("observed_at")) or raw.get("observed_at"),
            "available_at_utc": _utc_iso(raw.get("available_at") or raw.get("available_at_utc")),
            "decision_at_utc": _utc_iso(raw.get("observed_at")) or raw.get("observed_at"),
            "observed_date_utc": _utc_date(raw.get("observed_at")),
            "kickoff_at": raw.get("kickoff_time") or (event or {}).get("kickoff_time"),
            "competition_id": raw.get("competition_id") or (event or {}).get("competition_id"),
            "competition_name": raw.get("competition_name") or (event or {}).get("competition_name"),
            "competition_country": raw.get("competition_country") or (event or {}).get("competition_country"),
            "home_team": raw.get("home_team") or (event or {}).get("home_team"),
            "away_team": raw.get("away_team") or (event or {}).get("away_team"),
            "match_status": raw.get("match_status"),
            "display_time": raw.get("display_time"),
            # Never copy the mutable current-event phase into a historical
            # observation.  If the compact market evidence contains a period,
            # it is retained as historical market metadata instead.
            "period": historical_period,
            "score_home": score_home,
            "score_away": score_away,
            "ht_score_home": ht_home,
            "ht_score_away": ht_away,
            "extra_time": raw.get("extra_time"),
            "penalties": raw.get("penalties"),
            "scope_explicit": scope_explicit,
            "scope_assumed": not scope_explicit and not scope_conflict,
            "phase_confirmed": phase_confirmed,
            "phase_evidence": "PROVIDER_BREAK_DISPLAY_HZ_SCORE_MATCH" if phase_confirmed else "UNCONFIRMED",
            "result_terminal": result_terminal,
            "result_ht_match": result_ht_match,
            "result_valid": result_valid,
            "ft_result_valid": ft_result_valid,
            "result_status": quality_status or ("VERIFIED" if result_valid else "PENDING" if result is None else "LEGACY"),
            "result_use_ft": bool(ft_result_valid),
            "result_use_h2": bool(result_valid),
            "result_reason": (result or {}).get("result_reason") if result else "RESULT_MISSING",
            "result_source": result_source,
            "result_evidence_id": (result or {}).get("result_evidence_id") if result else None,
            "result_revision": (result or {}).get("result_revision") if result else None,
            "result_resolved_at": (result or {}).get("result_resolved_at") if result else None,
            "final_score_home": final_home,
            "final_score_away": final_away,
            "result_extra_time": result_extra_time,
            "result_penalties": result_penalties,
            "result_fingerprint": _json_digest(result),
            "h2_goals": h2_goals if result_valid else None,
            "outcome_class": (
                "H2_GOALS_0" if h2_goals == 0 else
                "H2_GOALS_1" if h2_goals == 1 else
                "H2_GOALS_2_PLUS" if h2_goals is not None and h2_goals >= 2 else None
            ),
            "q_zero": q0,
            "q_two_plus": q2,
            "q_zero_source_type": raw.get("q_zero_source_type"),
            "q_two_plus_source_type": raw.get("q_two_plus_source_type"),
            "q_zero_market_id": raw.get("q_zero_market_id"),
            "q_two_plus_market_id": raw.get("q_two_plus_market_id"),
            "q_zero_outcome_id": raw.get("q_zero_outcome_id"),
            "q_two_plus_outcome_id": raw.get("q_two_plus_outcome_id"),
            "remaining_under_05": raw.get("remaining_under_05"),
            "remaining_over_05": raw.get("remaining_over_05"),
            "remaining_under_15": raw.get("remaining_under_15"),
            "remaining_over_15": raw.get("remaining_over_15"),
            "p0_market": _float(raw.get("p0_market")),
            "p1_market": stored_p1,
            "p2plus_market": _float(raw.get("p2plus_market")),
            "p0_recomputed": p0_recomputed,
            "p01_recomputed": p01_recomputed,
            "p1_recomputed": p1_recomputed,
            "p1_recompute_valid": p1_recompute_valid,
            "p1_recompute_delta": abs(stored_p1 - p1_recomputed) if stored_p1 is not None and p1_recomputed is not None else None,
            "p1_break_even": p1_break_even,
            "p1_buffer": p1_break_even - stored_p1 if p1_break_even is not None and stored_p1 is not None else None,
            "win_roi": win_roi,
            "market_pair_05": pair_05,
            "market_pair_15": pair_15,
            "market_semantics_verified": market_semantics,
            "market_semantics_reason": (
                "SAME_MARKET_EXPLICIT_PERIOD_SCOPE_RECONSTRUCTED_METADATA" if market_semantics and pair_metadata_reconstructed
                else "SAME_MARKET_EXPLICIT_PERIOD_SCOPE" if market_semantics
                else "MISSING_OR_MISMATCHED_MARKET_PERIOD_SCOPE"
            ),
            "market_semantics_metadata_reconstructed": pair_metadata_reconstructed,
            "snapshot_quality": raw.get("snapshot_quality"),
            "normalizer_version": raw.get("normalizer_version"),
            "strategy_version": raw.get("strategy_version"),
            "relevant_markets_json": raw.get("relevant_markets_json") or "[]",
            "archive_path": raw.get("archive_path"),
            "payload_hash": raw.get("payload_hash"),
            "entry_eligible": entry_eligible,
            "evidence_level": evidence_level,
            "primary_reject_reason": reject_reasons[0] if reject_reasons else None,
            "quality_flags": reject_reasons,
        }

    def observations(
        self,
        *,
        snapshot_type: str = "HT_STABLE",
        from_date: str | None = None,
        to_date: str | None = None,
        cutoff_utc: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self._result_rows()
        events = self._events()
        rows = self._snapshot_rows(snapshot_type)
        from_day = str(from_date)[:10] if from_date else None
        to_day = str(to_date)[:10] if to_date else None
        cutoff = _parse_utc(cutoff_utc) if cutoff_utc else None
        observations = [
            self._one_observation(raw, results.get(str(raw.get("event_id"))), events.get(str(raw.get("event_id"))))
            for raw in rows
        ]
        selected: list[dict[str, Any]] = []
        for row in observations:
            day = str(row.get("observed_date_utc") or "")
            observed = _parse_utc(row.get("observed_at_utc"))
            if from_day and day < from_day:
                continue
            if to_day and day > to_day:
                continue
            if cutoff and (observed is None or observed > cutoff):
                continue
            selected.append(row)
        return selected

    def audit(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        cutoff_utc: str | None = None,
    ) -> SourceAudit:
        tables = self.tables()
        table_counts = {name: self.count_rows(name) for name in sorted(tables)}
        snapshot_counts = []
        if "snapshots" in tables:
            snapshot_counts = [
                dict(row) for row in self.connection.execute(
                    """SELECT snapshot_type, COUNT(*) AS rows,
                              COUNT(DISTINCT event_id) AS games,
                              MIN(observed_at) AS first_observed_utc,
                              MAX(observed_at) AS last_observed_utc,
                              SUM(q_zero_best > 1 AND q_two_plus_best > 1) AS with_pair,
                              SUM(p1_market IS NOT NULL) AS with_p1
                       FROM snapshots GROUP BY snapshot_type ORDER BY snapshot_type"""
                )
            ]
        result_counts = []
        if "match_results" in tables:
            result_counts = [
                dict(row) for row in self.connection.execute(
                    """SELECT final_status, extra_time, penalties, COUNT(*) AS rows,
                              SUM(ht_home IS NOT NULL AND ht_away IS NOT NULL
                                  AND ft_home IS NOT NULL AND ft_away IS NOT NULL) AS scores
                       FROM match_results GROUP BY final_status, extra_time, penalties
                       ORDER BY final_status"""
                )
            ]
        observations = self.observations(
            from_date=from_date,
            to_date=to_date,
            cutoff_utc=cutoff_utc,
        )
        day_map: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "date_utc": None, "snapshots": 0, "with_quotes": 0,
            "with_p1": 0, "with_ft_result": 0, "with_result": 0,
            "entry_eligible": 0,
        })
        comp_map: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
            "competition_country": None, "competition_name": None,
            "snapshots": 0, "with_ft_result": 0, "with_result": 0,
            "entry_eligible": 0,
        })
        for row in observations:
            date = row["observed_date_utc"] or "UNKNOWN"
            day = day_map[date]
            day["date_utc"] = date
            day["snapshots"] += 1
            day["with_quotes"] += int(row["q_zero"] is not None and row["q_two_plus"] is not None)
            day["with_p1"] += int(row["p1_market"] is not None)
            day["with_ft_result"] += int(row["ft_result_valid"])
            day["with_result"] += int(row["result_valid"])
            day["entry_eligible"] += int(row["entry_eligible"])
            key = (str(row["competition_country"] or "UNKNOWN"), str(row["competition_name"] or "UNKNOWN"))
            comp = comp_map[key]
            comp["competition_country"], comp["competition_name"] = key
            comp["snapshots"] += 1
            comp["with_ft_result"] += int(row["ft_result_valid"])
            comp["with_result"] += int(row["result_valid"])
            comp["entry_eligible"] += int(row["entry_eligible"])
        findings: list[dict[str, Any]] = []
        def add(code: str, severity: str, evidence: str, risk: str) -> None:
            findings.append({"code": code, "severity": severity, "evidence": evidence, "risk": risk})
        missing_ft_results = sum(not row["ft_result_valid"] for row in observations)
        missing_results = sum(not row["result_valid"] for row in observations)
        unconfirmed_phase = sum(not row["phase_confirmed"] for row in observations)
        scope_unknown = sum(
            row["extra_time"] is None or row["penalties"] is None
            for row in observations
        )
        p1_conflicts = sum("P1_RECOMPUTATION_CONFLICT" in row["quality_flags"] for row in observations)
        if missing_results:
            add("RESULT_COVERAGE_LIMITED", "HIGH", f"{missing_results} von {len(observations)} HT_STABLE-Zeilen sind nicht als vollständiges Ergebnis auflösbar.", "Rendite- und Trefferquoten können durch Ergebnisabdeckung verzerrt werden.")
        if missing_ft_results and missing_ft_results != missing_results:
            add("FT_COVERAGE_SEPARATE", "MEDIUM", f"{missing_ft_results} von {len(observations)} HT_STABLE-Zeilen haben keinen freigegebenen FT-Endstand; H2-Abdeckung ist separat.", "FT-Anzeige und H2-Label dürfen nicht gleichgesetzt werden.")
        if unconfirmed_phase:
            add("HALFTIME_PHASE_CONFLICTS", "HIGH", f"{unconfirmed_phase} HT_STABLE-Zeilen haben keinen bestätigten break/HZ/Score-Zustand.", "Ein Snapshot kann außerhalb des vorgesehenen Einstiegszeitpunkts liegen.")
        if scope_unknown:
            add("SETTLEMENT_SCOPE_UNKNOWN", "HIGH", f"{scope_unknown} Beobachtungen haben keinen expliziten Extra-Time-/Penalty-Ausschluss.", "Reguläre HZ2-Abrechnung darf nicht stillschweigend angenommen werden.")
        if p1_conflicts:
            add("P1_RECOMPUTATION_CONFLICTS", "HIGH", f"{p1_conflicts} Beobachtungen weichen zwischen gespeicherten und aus Paaren rekonstruierten P1-Werten ab.", "P1-Bänder könnten auf uneinheitlichen Marktquellen beruhen.")
        pair_conflicts = sum("P1_PAIR_INCONSISTENT" in row["quality_flags"] for row in observations)
        if pair_conflicts:
            add("P1_PAIR_INCONSISTENCIES", "HIGH", f"{pair_conflicts} Beobachtungen haben nicht konsistente U/O-Referenzpaare.", "P1-basierte Regeln dürfen diese Zeilen nicht als saubere Rekonstruktion verwenden.")
        duplicate_groups = 0
        if "snapshots" in tables:
            duplicate_groups = int(self.connection.execute(
                """SELECT COUNT(*) FROM (
                       SELECT event_id, snapshot_type
                       FROM snapshots
                       GROUP BY event_id, snapshot_type
                       HAVING COUNT(*) > 1
                   )"""
            ).fetchone()[0])
        if duplicate_groups:
            add("DUPLICATE_EVENT_SNAPSHOT_KEYS", "HIGH", f"{duplicate_groups} Event/Snapshot-Typ-Gruppen enthalten mehrere Zeilen.", "Eine Mehrfachbeobachtung darf nicht unbemerkt als unabhängige Spielstichprobe gezählt werden.")
        if self.count_rows("odds_history") == 0:
            add("NO_TICK_HISTORY", "MEDIUM", "odds_history enthält 0 Zeilen.", "Der Backtest ist ein Snapshot-Replay und beweist keine kontinuierliche Preisverfügbarkeit.")
        if self.count_rows("canonical_outcomes") == 0:
            add("NO_CANONICAL_HISTORY", "MEDIUM", "canonical_outcomes enthält 0 Zeilen.", "Marktsemantik muss aus dem schlanken Snapshot-Archiv geprüft werden.")
        return SourceAudit(
            source_path=str(self.path),
            source_sha256=self.sha256,
            quick_check=self.quick_check(),
            table_counts=table_counts,
            snapshot_counts=snapshot_counts,
            result_counts=result_counts,
            coverage_by_day=sorted(day_map.values(), key=lambda item: str(item["date_utc"])),
            coverage_by_competition=sorted(comp_map.values(), key=lambda item: (-item["snapshots"], str(item["competition_name"]))),
            findings=findings,
        )
