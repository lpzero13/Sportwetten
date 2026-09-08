"""Tipico result finalization and historical recovery.

This module is the V0.6.5 orchestration layer.  It deliberately keeps raw
Tipico observations immutable and writes only derived, auditable result
quality fields.  Local Tipico evidence is evaluated before the existing
FotMob backfill worker is called.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import uuid
import math
from dataclasses import fields
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from services.result_backfill import ResultBackfillRunner


RULE_VERSION = "v0.6.5"
TERMINAL_STATUS_TOKENS = frozenset(
    {
        "finished",
        "ended",
        "complete",
        "completed",
        "final",
        "full_time",
        "fulltime",
        "ft",
        "settled",
    }
)
VOID_STATUS_TOKENS = frozenset({"cancelled", "canceled", "postponed", "abandoned"})
ACTIVE_STATUS_TOKENS = frozenset({"running", "live", "extra_time"})
HALFTIME_STATUS_TOKENS = frozenset({"break", "half_time", "halftime", "ht"})
NO_LONGER_LIVE_TOKENS = frozenset({"no_longer_live", "no longer live"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _token(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _score(value: Any) -> int | None:
    """Parse only non-negative integral scores; reject bools and decimals."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip()
    if not text or not text.isdigit():
        return None
    parsed = int(text)
    return parsed if parsed >= 0 else None


def _flag(value: Any) -> bool | None:
    if value is None or isinstance(value, bool) and value not in (True, False):
        return None
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "ja"}:
        return True
    if text in {"0", "false", "no", "nein"}:
        return False
    return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _raw_child(raw: Mapping[str, Any], *keys: str) -> Any:
    lowered = {str(key).casefold(): key for key in raw}
    for key in keys:
        actual = lowered.get(key.casefold())
        if actual is not None:
            return raw[actual]
    return None


def canonical_game_status(status: Any, period: Any = None, *, has_final_evidence: bool = False) -> str:
    """Map provider vocabulary without treating disappearance as a finish."""

    status_token = _token(status)
    period_token = _token(period)
    if status_token in TERMINAL_STATUS_TOKENS or period_token in TERMINAL_STATUS_TOKENS:
        return "FINISHED"
    if status_token in VOID_STATUS_TOKENS or period_token in VOID_STATUS_TOKENS:
        return status_token.upper() if status_token in VOID_STATUS_TOKENS else period_token.upper()
    if has_final_evidence:
        return "FINISHED"
    if status_token in ACTIVE_STATUS_TOKENS or period_token in ACTIVE_STATUS_TOKENS:
        return "LIVE"
    if status_token in HALFTIME_STATUS_TOKENS or period_token in HALFTIME_STATUS_TOKENS:
        return "HALFTIME"
    if status_token in {"pre_match", "prematch", "scheduled"} or period_token in {"pre_match", "prematch", "scheduled"}:
        return "SCHEDULED"
    if status_token in NO_LONGER_LIVE_TOKENS or period_token in NO_LONGER_LIVE_TOKENS:
        return "FINISHED" if has_final_evidence else "UNKNOWN"
    return "UNKNOWN"


def _scope_status(*records: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Determine scope only from explicit Tipico fields, never by age."""

    extra: list[bool] = []
    penalties: list[bool] = []
    for record in records:
        raw = _json_object(record.get("raw_state_json") or record.get("raw_data_json"))
        extra_value = record.get("extra_time")
        if extra_value is None:
            extra_value = _raw_child(raw, "extraTime", "extra_time", "overtime")
        penalty_value = record.get("penalties")
        if penalty_value is None:
            penalty_value = _raw_child(raw, "penalties", "penaltyShootout", "wentToPenalties")
        parsed_extra = _flag(extra_value)
        parsed_penalty = _flag(penalty_value)
        if parsed_extra is not None:
            extra.append(parsed_extra)
        if parsed_penalty is not None:
            penalties.append(parsed_penalty)
    if any(penalties):
        return "PENALTIES", ["penalties_confirmed"]
    if any(extra):
        return "EXTRA_TIME", ["extra_time_confirmed"]
    if extra and penalties and all(not value for value in extra) and all(not value for value in penalties):
        return "REGULATION", ["regular_time_explicit"]
    return "UNKNOWN", ["scope_not_explicit"]


def _row_value(row: Mapping[str, Any] | None, *names: str) -> Any:
    if row is None:
        return None
    for name in names:
        try:
            value = row[name]
        except (IndexError, KeyError):
            continue
        if value is not None:
            return value
    return None


@dataclass(frozen=True, slots=True)
class LocalEvidence:
    source_record_type: str
    source_record_id: str
    observed_at: str | None
    raw_status: str | None
    raw_period: str | None
    ft_home: int | None
    ft_away: int | None
    ht_home: int | None
    ht_away: int | None
    extra_time: Any = None
    penalties: Any = None
    raw_state_json: Any = None
    record: Mapping[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return _token(self.raw_status) in TERMINAL_STATUS_TOKENS or _token(self.raw_period) in TERMINAL_STATUS_TOKENS

    @property
    def fingerprint(self) -> tuple[int | None, int | None]:
        return self.ft_home, self.ft_away


@dataclass(frozen=True, slots=True)
class FinalizationDecision:
    event_id: str
    canonical_status: str
    raw_status: str | None
    result_status: str
    result_use_ft: bool
    result_use_h2: bool
    reason: str
    flags: tuple[str, ...]
    evidence: LocalEvidence | None
    ht_source: str | None
    ft_source: str | None
    ht_home: int | None
    ht_away: int | None
    ft_home: int | None
    ft_away: int | None
    scope_status: str
    h2_goals: int | None


def _as_dict(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {str(key): row[key] for key in row.keys()}


def _terminal_record(record: Mapping[str, Any], source_type: str, source_id: str) -> LocalEvidence | None:
    if source_type == "FINAL_SNAPSHOT":
        quality = _token(_row_value(record, "snapshot_quality"))
        if any(token in quality for token in ("partial", "fallback", "failed")):
            return None
    status = _row_value(record, "status", "match_status", "final_status")
    period = _row_value(record, "period")
    if _token(status) not in TERMINAL_STATUS_TOKENS and _token(period) not in TERMINAL_STATUS_TOKENS:
        return None
    return LocalEvidence(
        source_record_type=source_type,
        source_record_id=source_id,
        observed_at=_row_value(record, "observed_at", "last_updated_at", "last_seen_at", "finished_at"),
        raw_status=str(status) if status is not None else None,
        raw_period=str(period) if period is not None else None,
        ft_home=_score(_row_value(record, "ft_home", "score_home")),
        ft_away=_score(_row_value(record, "ft_away", "score_away")),
        ht_home=_score(_row_value(record, "ht_home", "ht_score_home")),
        ht_away=_score(_row_value(record, "ht_away", "ht_score_away")),
        extra_time=_row_value(record, "extra_time"),
        penalties=_row_value(record, "penalties"),
        raw_state_json=_row_value(record, "raw_state_json", "raw_data_json"),
        record=record,
    )


def _event_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _local_terminal_evidence(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    tables: set[str],
    existing: Mapping[str, Any] | None,
) -> tuple[list[LocalEvidence], list[str]]:
    event_id = str(event["event_id"])
    evidence: list[LocalEvidence] = []
    flags: list[str] = []
    event_record = _as_dict(event)
    item = _terminal_record(event_record, "EVENT", event_id)
    if item is not None:
        evidence.append(item)

    if "current_event_state" in tables:
        row = connection.execute(
            "SELECT * FROM current_event_state WHERE event_id = ?", (event_id,)
        ).fetchone()
        item = _terminal_record(_as_dict(row), "CURRENT_EVENT_STATE", f"current:{event_id}")
        if item is not None:
            evidence.append(item)

    if "event_states" in tables:
        rows = connection.execute(
            """SELECT * FROM event_states WHERE event_id = ?
               ORDER BY observed_at DESC, id DESC""",
            (event_id,),
        ).fetchall()
        for row in rows:
            item = _terminal_record(_as_dict(row), "EVENT_STATE", str(row["id"]))
            if item is not None:
                evidence.append(item)

    if "snapshots" in tables:
        rows = connection.execute(
            """SELECT * FROM snapshots
               WHERE event_id = ? AND upper(snapshot_type) = 'FINAL'
               ORDER BY observed_at DESC, snapshot_id DESC""",
            (event_id,),
        ).fetchall()
        for row in rows:
            item = _terminal_record(_as_dict(row), "FINAL_SNAPSHOT", str(row["snapshot_id"]))
            if item is not None:
                evidence.append(item)
            elif row["score_home"] is not None or row["score_away"] is not None:
                quality = _token(_row_value(_as_dict(row), "snapshot_quality"))
                flags.append(
                    "FINAL_SNAPSHOT_QUALITY_UNSUITABLE"
                    if any(token in quality for token in ("partial", "fallback", "failed"))
                    else "FINAL_SNAPSHOT_NOT_TERMINAL"
                )

    if existing:
        item = _terminal_record(_as_dict(existing), "MATCH_RESULT", event_id)
        if item is not None:
            evidence.append(item)

    # Prefer observations that actually contain a complete FT.  A terminal
    # status without a score still normalizes the game status, but cannot
    # create a result label.
    complete = [item for item in evidence if item.ft_home is not None and item.ft_away is not None]
    if complete:
        fingerprints = {item.fingerprint for item in complete}
        if len(fingerprints) > 1:
            flags.append("MULTIPLE_TERMINAL_FT_VALUES")
    return evidence, flags


def _half_time_candidates(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    tables: set[str],
    evidence: list[LocalEvidence],
    existing: Mapping[str, Any] | None,
) -> tuple[tuple[int, int] | None, str | None, list[str]]:
    event_id = str(event["event_id"])
    candidates: list[tuple[tuple[int, int], str]] = []
    for item in evidence:
        if item.ht_home is not None and item.ht_away is not None:
            candidates.append(((item.ht_home, item.ht_away), item.source_record_type))
    if existing and _score(_row_value(existing, "ht_home")) is not None and _score(_row_value(existing, "ht_away")) is not None:
        candidates.append(((_score(_row_value(existing, "ht_home")) or 0, _score(_row_value(existing, "ht_away")) or 0), "MATCH_RESULT"))

    if "event_states" in tables:
        rows = connection.execute(
            """SELECT * FROM event_states WHERE event_id = ?
               AND (upper(period) IN ('HALF_TIME', 'HALFTIME', 'HT', 'BREAK')
                    OR upper(display_time) = 'HZ')
               ORDER BY observed_at ASC, id ASC""",
            (event_id,),
        ).fetchall()
        for row in rows:
            data = _as_dict(row)
            home = _score(_row_value(data, "ht_score_home", "score_home"))
            away = _score(_row_value(data, "ht_score_away", "score_away"))
            if home is not None and away is not None:
                candidates.append(((home, away), "EVENT_STATE_HALFTIME"))

    if "snapshots" in tables:
        rows = connection.execute(
            """SELECT * FROM snapshots WHERE event_id = ?
               AND upper(snapshot_type) IN ('HALFTIME', 'HT_STABLE')
               AND COALESCE(snapshot_quality, '') != 'FAILED'
               ORDER BY observed_at ASC, snapshot_id ASC""",
            (event_id,),
        ).fetchall()
        for row in rows:
            data = _as_dict(row)
            home = _score(_row_value(data, "ht_score_home", "score_home"))
            away = _score(_row_value(data, "ht_score_away", "score_away"))
            if home is not None and away is not None:
                candidates.append(((home, away), "SNAPSHOT_HALFTIME"))

    flags: list[str] = []
    unique = {pair for pair, _ in candidates}
    frozen: tuple[int, int] | None = None
    if "paper_trades" in tables:
        row = connection.execute(
            """SELECT ht_score_home, ht_score_away FROM paper_trades
               WHERE event_id = ? AND ht_score_home IS NOT NULL AND ht_score_away IS NOT NULL
               ORDER BY created_at ASC, paper_trade_id ASC LIMIT 1""",
            (event_id,),
        ).fetchone()
        if row is not None:
            frozen = (_score(row["ht_score_home"]) or 0, _score(row["ht_score_away"]) or 0)
    if frozen is not None and frozen not in unique:
        flags.append("FROZEN_ENTRY_HT_CONFLICT")
    if len(unique) > 1:
        flags.append("HALFTIME_EVIDENCE_CONFLICT")
    if not unique:
        return None, None, flags
    selected = frozen if frozen in unique else next(iter(unique))
    source = next((source for pair, source in candidates if pair == selected), None)
    return selected, source, flags


def _choose_evidence(evidence: list[LocalEvidence], flags: list[str]) -> LocalEvidence | None:
    if not evidence:
        return None
    complete = [item for item in evidence if item.ft_home is not None and item.ft_away is not None]
    if complete:
        fingerprints = {item.fingerprint for item in complete}
        if len(fingerprints) > 1:
            return max(complete, key=lambda item: item.observed_at or "")
        # Prefer a terminal event state/current state over the mutable event
        # row, but keep the latest observation for the audit timestamp.
        return max(complete, key=lambda item: item.observed_at or "")
    return max(evidence, key=lambda item: item.observed_at or "")


def decide_event(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    *,
    tables: set[str] | None = None,
) -> FinalizationDecision:
    tables = tables or _event_tables(connection)
    event_id = str(event["event_id"])
    existing_row = connection.execute(
        "SELECT * FROM match_results WHERE event_id = ?", (event_id,)
    ).fetchone() if "match_results" in tables else None
    existing = _as_dict(existing_row)
    evidence, evidence_flags = _local_terminal_evidence(connection, event, tables, existing or None)
    terminal_evidence = [item for item in evidence if item.terminal]
    evidence = terminal_evidence
    flags = list(evidence_flags)
    selected = _choose_evidence(evidence, flags)
    raw_status = str(_row_value(event, "status") or "") or None
    raw_period = str(_row_value(event, "period") or "") or None
    canonical = canonical_game_status(raw_status, raw_period, has_final_evidence=selected is not None)
    if selected is None and _token(raw_status) in TERMINAL_STATUS_TOKENS:
        canonical = "FINISHED"

    ft_home = selected.ft_home if selected else _score(_row_value(existing, "ft_home"))
    ft_away = selected.ft_away if selected else _score(_row_value(existing, "ft_away"))
    ht_pair, ht_source, ht_flags = _half_time_candidates(
        connection, event, tables, evidence, existing or None
    )
    flags.extend(ht_flags)
    ht_home = ht_pair[0] if ht_pair else _score(_row_value(existing, "ht_home"))
    ht_away = ht_pair[1] if ht_pair else _score(_row_value(existing, "ht_away"))

    scope_records: list[Mapping[str, Any]] = []
    if selected is not None and selected.record is not None:
        scope_records.append(selected.record)
    scope_records.append(event)
    if existing:
        scope_records.append(existing)
    scope, scope_flags = _scope_status(*scope_records)
    flags.extend(scope_flags)

    complete_ft = ft_home is not None and ft_away is not None
    # A rejected non-terminal FINAL snapshot is only a diagnostic flag.  It
    # must not invalidate a separate, genuinely terminal event/result record
    # that was found in the same local evidence set.
    blocking_evidence_flags = {
        "MULTIPLE_TERMINAL_FT_VALUES",
        "INVALID_FT_SCORE",
    }
    valid_ft = complete_ft and selected is not None and not any(
        flag in blocking_evidence_flags for flag in evidence_flags
    )
    score_invalid = complete_ft and (ft_home < 0 or ft_away < 0)
    if score_invalid:
        flags.append("INVALID_FT_SCORE")
        valid_ft = False
    h2_goals: int | None = None
    valid_h2 = False
    ft_below_ht = False
    if ht_home is not None and ht_away is not None and complete_ft:
        if ft_home < ht_home or ft_away < ht_away:
            flags.append("FT_BELOW_HT_SCORE")
            ft_below_ht = True
        elif not ht_flags and scope == "REGULATION" and valid_ft:
            h2_goals = ft_home + ft_away - ht_home - ht_away
            valid_h2 = h2_goals >= 0

    if any(flag in flags for flag in ("MULTIPLE_TERMINAL_FT_VALUES", "INVALID_FT_SCORE")):
        result_status = "CONFLICT" if "MULTIPLE_TERMINAL_FT_VALUES" in flags else "PARTIAL"
        use_ft = False
        use_h2 = False
        reason = "RESULT_CONFLICT" if result_status == "CONFLICT" else "INVALID_FT_SCORE"
    elif not selected or not complete_ft:
        if _token(raw_status) in NO_LONGER_LIVE_TOKENS:
            reason = "NO_LONGER_LIVE_WITHOUT_FINAL_EVIDENCE"
        elif canonical in {"CANCELLED", "CANCELED", "POSTPONED", "ABANDONED"}:
            reason = f"EVENT_{canonical}"
        else:
            reason = "FINAL_SCORE_MISSING"
        result_status = "UNAVAILABLE" if canonical in {"CANCELLED", "CANCELED", "POSTPONED", "ABANDONED"} else "PENDING"
        use_ft = False
        use_h2 = False
    elif scope in {"EXTRA_TIME", "PENALTIES"}:
        reason = "NON_REGULATION_SCOPE"
        result_status = "PARTIAL"
        use_ft = False
        use_h2 = False
    elif scope != "REGULATION":
        reason = "SCOPE_UNKNOWN"
        result_status = "PARTIAL"
        use_ft = False
        use_h2 = False
    elif "HALFTIME_EVIDENCE_CONFLICT" in flags or "FROZEN_ENTRY_HT_CONFLICT" in flags:
        reason = "HALFTIME_EVIDENCE_CONFLICT"
        result_status = "PARTIAL"
        use_ft = True
        use_h2 = False
    elif ft_below_ht:
        reason = "INVALID_FT_BELOW_HT"
        result_status = "PARTIAL"
        use_ft = False
        use_h2 = False
    elif not valid_h2:
        reason = "HT_MISSING" if ht_home is None or ht_away is None else "INVALID_H2_DERIVATION"
        result_status = "VERIFIED"
        use_ft = True
        use_h2 = False
    else:
        reason = "VERIFIED_REGULATION"
        result_status = "VERIFIED"
        use_ft = True
        use_h2 = True

    ft_source = selected.source_record_type if selected is not None else (_row_value(existing, "result_ft_source", "result_source"))
    if existing and selected is not None and selected.source_record_type == "MATCH_RESULT":
        ft_source = _row_value(existing, "result_ft_source", "result_source") or "TIPICO_ORIGINAL"
    return FinalizationDecision(
        event_id=event_id,
        canonical_status=canonical,
        raw_status=raw_status,
        result_status=result_status,
        result_use_ft=bool(use_ft),
        result_use_h2=bool(use_h2),
        reason=reason,
        flags=tuple(sorted(set(flags))),
        evidence=selected,
        ht_source=ht_source or (_row_value(existing, "result_ht_source") if existing else None),
        ft_source=str(ft_source) if ft_source is not None else None,
        ht_home=ht_home,
        ht_away=ht_away,
        ft_home=ft_home,
        ft_away=ft_away,
        scope_status=scope,
        h2_goals=h2_goals if use_h2 else None,
    )


def _evidence_id(event_id: str, decision: FinalizationDecision) -> str:
    item = decision.evidence
    seed = "|".join(
        (
            event_id,
            item.source_record_type if item else "NONE",
            item.source_record_id if item else "NONE",
            str(item.fingerprint if item else ""),
            decision.result_status,
            decision.reason,
        )
    )
    return f"tipico-result-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def _known_end_time(evidence: LocalEvidence | None) -> str | None:
    """Return an explicit end timestamp, never the time we discovered it."""

    if evidence is None or evidence.record is None:
        return None
    value = _row_value(
        evidence.record,
        "ended_at",
        "end_time",
        "end_at",
        "final_whistle_at",
    )
    if value is not None:
        return str(value)
    raw = _json_object(evidence.raw_state_json)
    value = _raw_child(raw, "endedAt", "endTime", "finalWhistleAt")
    return str(value) if value is not None else None


def _queue_upsert(
    connection: sqlite3.Connection,
    event_id: str,
    *,
    status: str,
    validation_status: str,
    attempt_count: int,
    now: str,
    next_attempt_at: str | None,
    last_error: str | None = None,
    resolved_at: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO result_backfill_queue (
               event_id, status, attempt_count, next_attempt_at, last_attempt_at,
               last_provider_match_id, identity_status, validation_status,
               last_error, resolved_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, NULL, 'TIPICO_LOCAL', ?, ?, ?, ?)
           ON CONFLICT(event_id) DO UPDATE SET
               status = excluded.status,
               attempt_count = excluded.attempt_count,
               next_attempt_at = excluded.next_attempt_at,
               last_attempt_at = excluded.last_attempt_at,
               identity_status = excluded.identity_status,
               validation_status = excluded.validation_status,
               last_error = excluded.last_error,
               resolved_at = excluded.resolved_at,
               updated_at = excluded.updated_at""",
        (event_id, status, attempt_count, next_attempt_at, now, validation_status, last_error, resolved_at, now),
    )


def _record_evidence(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    decision: FinalizationDecision,
    *,
    evidence_id: str,
    checked_at: str,
) -> None:
    item = decision.evidence
    values = {
        "evidence_id": evidence_id,
        "event_id": str(event["event_id"]),
        "provider": "TIPICO",
        "provider_match_id": None,
        "fetched_at": checked_at,
        "response_status": None,
        "provider_endpoint": None,
        "payload_hash": None,
        "match_confidence": 1.0,
        "identity_status": "LOCAL_EVIDENCE",
        "validation_status": decision.result_status or "PENDING",
        "validation_flags_json": json.dumps(list(decision.flags), ensure_ascii=False),
        "provider_status": item.raw_status if item else event.get("status"),
        "scope_status": decision.scope_status,
        "ht_home": decision.ht_home,
        "ht_away": decision.ht_away,
        "ft_home": decision.ft_home,
        "ft_away": decision.ft_away,
        "second_half_goals": decision.h2_goals,
        "second_half_goal_class": (
            "0" if decision.h2_goals == 0
            else "1" if decision.h2_goals == 1
            else "2_PLUS" if decision.h2_goals is not None else None
        ),
        "competition_id": event.get("competition_id"),
        "competition_name": event.get("competition_name"),
        "competition_country": event.get("competition_country"),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "kickoff_at": event.get("kickoff_time"),
        "source_context": "TIPICO_RESULT_FINALIZATION",
        "raw_payload_path": None,
        "source_record_type": item.source_record_type if item else "EVENT",
        "source_record_id": item.source_record_id if item else str(event["event_id"]),
        "observed_at": item.observed_at if item else None,
        "raw_status": item.raw_status if item else event.get("status"),
        "raw_period": item.raw_period if item else event.get("period"),
        "result_status": decision.result_status,
        "result_use_ft": int(decision.result_use_ft),
        "result_use_h2": int(decision.result_use_h2),
        "rule_version": RULE_VERSION,
        "created_at": checked_at,
    }
    columns = tuple(values)
    connection.execute(
        f"INSERT OR IGNORE INTO result_backfill_evidence ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _update_event_status(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    decision: FinalizationDecision,
    *,
    checked_at: str,
) -> None:
    connection.execute(
        """UPDATE events
           SET canonical_status = ?, raw_status = COALESCE(raw_status, status),
               status_normalized_at = ?
           WHERE event_id = ?""",
        (decision.canonical_status, checked_at, str(event["event_id"])),
    )


def _record_change(
    connection: sqlite3.Connection,
    event_id: str,
    *,
    run_id: str | None = None,
    action: str,
    previous: Mapping[str, Any] | None,
    new_status: str | None,
    new_ft_home: int | None,
    new_ft_away: int | None,
    new_source: str | None,
    reason: str,
    evidence_id: str,
    changed_at: str,
    revision: int | None = None,
) -> None:
    previous_revision = int(_row_value(previous, "result_revision") or 0)
    connection.execute(
        """INSERT INTO result_finalization_changes (
               run_id, event_id, revision, changed_at, action, previous_status, new_status,
               previous_ft_home, previous_ft_away, new_ft_home, new_ft_away,
               previous_source, new_source, reason, evidence_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            event_id,
            previous_revision + 1 if revision is None and previous is not None else (
                revision if revision is not None else previous_revision
            ),
            changed_at,
            action,
            _row_value(previous, "result_status", "final_status"),
            new_status,
            _score(_row_value(previous, "ft_home")),
            _score(_row_value(previous, "ft_away")),
            new_ft_home,
            new_ft_away,
            _row_value(previous, "result_source"),
            new_source,
            reason,
            evidence_id,
        ),
    )


def _persist_decision(
    database: Any,
    event: Mapping[str, Any],
    decision: FinalizationDecision,
    *,
    apply: bool,
    checked_at: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    evidence_id = _evidence_id(str(event["event_id"]), decision)
    item = decision.evidence
    connection = getattr(database, "connection", database)
    result = connection.execute(
        "SELECT * FROM match_results WHERE event_id = ?", (str(event["event_id"]),)
    ).fetchone()
    previous = _as_dict(result) or None
    outcome: dict[str, Any] = {
        "event_id": str(event["event_id"]),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "competition_name": event.get("competition_name"),
        "competition_country": event.get("competition_country"),
        "raw_status": decision.raw_status,
        "canonical_status": decision.canonical_status,
        "result_status": decision.result_status,
        "result_use_ft": decision.result_use_ft,
        "result_use_h2": decision.result_use_h2,
        "reason": decision.reason,
        "flags": list(decision.flags),
        "source_record_type": item.source_record_type if item else None,
        "source_record_id": item.source_record_id if item else None,
        "observed_at": item.observed_at if item else None,
        "ht": [decision.ht_home, decision.ht_away] if decision.ht_home is not None and decision.ht_away is not None else None,
        "ft": [decision.ft_home, decision.ft_away] if decision.ft_home is not None and decision.ft_away is not None else None,
        "scope_status": decision.scope_status,
        "evidence_id": evidence_id,
        "would_change": bool(
            decision.canonical_status == "FINISHED"
            or decision.result_use_ft
            or decision.result_status in {"CONFLICT", "PARTIAL"}
        ),
        "applied": False,
    }
    if not apply:
        return outcome

    now = checked_at
    with database._lock, database.connection:
        _update_event_status(database.connection, event, decision, checked_at=checked_at)
        _record_evidence(database.connection, event, decision, evidence_id=evidence_id, checked_at=checked_at)
        if decision.result_status == "CONFLICT":
            previous_revision = int(_row_value(previous, "result_revision") or 0)
            same_conflict = bool(
                previous
                and str(previous.get("result_status") or "") == "CONFLICT"
                and str(previous.get("result_reason") or "") == decision.reason
                and int(previous.get("result_use_ft") or 0) == 0
                and int(previous.get("result_use_h2") or 0) == 0
            )
            if previous:
                database.connection.execute(
                    """UPDATE match_results
                       SET result_status = 'CONFLICT', result_use_ft = 0,
                           result_use_h2 = 0, result_reason = ?,
                           result_last_checked_at = ?, result_rule_version = ?,
                           result_revision = ?
                       WHERE event_id = ?""",
                    (
                        decision.reason, checked_at, RULE_VERSION,
                        previous_revision if same_conflict else previous_revision + 1,
                        str(event["event_id"]),
                    ),
                )
            if not same_conflict:
                _record_change(
                    database.connection,
                    str(event["event_id"]),
                    run_id=run_id,
                    action="CONFLICT_REVIEW_REQUIRED",
                    previous=previous,
                    new_status="CONFLICT",
                    new_ft_home=decision.ft_home,
                    new_ft_away=decision.ft_away,
                    new_source=None,
                    reason=decision.reason,
                    evidence_id=evidence_id,
                    changed_at=checked_at,
                    revision=previous_revision + 1 if previous is not None else 0,
                )
            _queue_upsert(
                database.connection,
                str(event["event_id"]),
                status="CONFLICT",
                validation_status="RESULT_CONFLICT",
                attempt_count=0,
                now=checked_at,
                next_attempt_at=None,
            )
            outcome["applied"] = True
            outcome["action"] = "CONFLICT"
            return outcome

        # No final score is still a useful status/evidence repair, but it must
        # never create a fabricated match_results row.
        if decision.ft_home is None or decision.ft_away is None or decision.evidence is None:
            # Due immediately so the same run can hand the case to the
            # provider backfill. A provider miss writes the controlled retry.
            next_at = checked_at
            queue_status = "UNAVAILABLE" if decision.result_status == "UNAVAILABLE" else "PENDING_LOCAL"
            _queue_upsert(
                database.connection,
                str(event["event_id"]),
                status=queue_status,
                validation_status=decision.reason,
                attempt_count=0,
                now=checked_at,
                next_attempt_at=next_at,
                last_error=decision.reason,
            )
            outcome["applied"] = True
            outcome["action"] = "STATUS_ONLY" if decision.canonical_status == "FINISHED" else "PENDING"
            return outcome

        # A structurally impossible score is evidence of a bad observation,
        # not a partial result that downstream consumers may display as a
        # usable label.  Keep the evidence and queue the event for review.
        if decision.reason in {"INVALID_FT_BELOW_HT", "INVALID_FT_SCORE"}:
            _queue_upsert(
                database.connection,
                str(event["event_id"]),
                status="PENDING_LOCAL",
                validation_status=decision.reason,
                attempt_count=0,
                now=checked_at,
                next_attempt_at=_iso((_parse_time(checked_at) or _now()) + timedelta(hours=24)),
                last_error=decision.reason,
            )
            outcome["applied"] = True
            outcome["action"] = "REJECTED_INVALID_RESULT"
            return outcome

        incoming_source = "TIPICO_LOCAL_EVIDENCE"
        if previous and str(previous.get("result_source") or "").upper() == "TIPICO_ORIGINAL":
            incoming_source = "TIPICO_ORIGINAL"
        reused_result = bool(previous and item and item.source_record_type == "MATCH_RESULT")
        if reused_result:
            # Reading an existing provider result is not new Tipico evidence.
            incoming_source = previous.get("result_source") or incoming_source
        existing_ft = (_score(_row_value(previous, "ft_home")), _score(_row_value(previous, "ft_away"))) if previous else (None, None)
        if previous and existing_ft[0] is not None and existing_ft[1] is not None and existing_ft != (decision.ft_home, decision.ft_away):
            # A provider correction must be reviewed rather than silently
            # rewriting the canonical score.
            previous_revision = int(_row_value(previous, "result_revision") or 0)
            database.connection.execute(
                """UPDATE match_results
                   SET result_status = 'CONFLICT', result_use_ft = 0,
                       result_use_h2 = 0, result_reason = ?,
                       result_last_checked_at = ?, result_rule_version = ?,
                       result_revision = ?
                   WHERE event_id = ?""",
                (
                    "LOCAL_RESULT_CONFLICT", checked_at, RULE_VERSION,
                    previous_revision + 1, str(event["event_id"]),
                ),
            )
            _record_change(
                database.connection, str(event["event_id"]),
                run_id=run_id,
                action="CONFLICT_REVIEW_REQUIRED", previous=previous,
                new_status="CONFLICT", new_ft_home=decision.ft_home,
                new_ft_away=decision.ft_away, new_source=incoming_source,
                reason="LOCAL_RESULT_CONFLICT", evidence_id=evidence_id,
                changed_at=checked_at, revision=previous_revision + 1,
            )
            _queue_upsert(
                database.connection, str(event["event_id"]), status="CONFLICT",
                validation_status="RESULT_CONFLICT", attempt_count=0,
                now=checked_at, next_attempt_at=None,
            )
            outcome["applied"] = True
            outcome["action"] = "CONFLICT"
            return outcome

        observed_at = item.observed_at if item and item.observed_at else checked_at
        values = {
            "event_id": str(event["event_id"]),
            "competition_id": event.get("competition_id"),
            "competition_name": event.get("competition_name"),
            "competition_country": event.get("competition_country"),
            "home_team": event.get("home_team") or previous.get("home_team") if previous else event.get("home_team"),
            "away_team": event.get("away_team") or previous.get("away_team") if previous else event.get("away_team"),
            "kickoff_at": event.get("kickoff_time") or (previous or {}).get("kickoff_at"),
            "ht_home": decision.ht_home,
            "ht_away": decision.ht_away,
            "ft_home": decision.ft_home,
            "ft_away": decision.ft_away,
            "first_half_goals": decision.ht_home + decision.ht_away if decision.ht_home is not None and decision.ht_away is not None else None,
            "second_half_goals": decision.h2_goals,
            "second_half_goal_class": ("0" if decision.h2_goals == 0 else "1" if decision.h2_goals == 1 else "2_PLUS") if decision.h2_goals is not None else None,
            "final_status": "finished",
            # ``finished_at`` is retained for compatibility with older
            # consumers and means the terminal observation time here.  The
            # actual end is separate and remains NULL unless explicitly
            # supplied by the evidence.
            "finished_at": observed_at,
            "extra_time": 0 if decision.scope_status == "REGULATION" else None,
            "penalties": 0 if decision.scope_status == "REGULATION" else None,
            "result_source": incoming_source,
            "result_evidence_id": evidence_id,
            "result_confidence": 1.0,
            "result_scope_status": decision.scope_status,
            "result_resolved_at": checked_at,
            "result_last_checked_at": checked_at,
            "result_status": decision.result_status,
            "result_use_ft": int(decision.result_use_ft),
            "result_use_h2": int(decision.result_use_h2),
            "result_reason": decision.reason,
            "result_raw_status": decision.raw_status,
            "result_ht_source": decision.ht_source or incoming_source,
            "result_ft_source": incoming_source,
            "ended_at": _known_end_time(item),
            "end_observed_at": observed_at,
            "result_rule_version": RULE_VERSION,
        }
        if previous:
            if reused_result:
                values["result_evidence_id"] = previous.get("result_evidence_id")
                values["result_ft_source"] = previous.get("result_ft_source") or incoming_source
                if (previous.get("ht_home"), previous.get("ht_away")) == (decision.ht_home, decision.ht_away):
                    values["result_ht_source"] = previous.get("result_ht_source") or incoming_source
            unchanged = all(
                previous.get(key) == values.get(key)
                for key in (
                    "ht_home", "ht_away", "ft_home", "ft_away", "final_status",
                    "result_status", "result_use_ft", "result_use_h2",
                    "result_reason", "result_scope_status", "result_source",
                )
            )
            previous_revision = int(_row_value(previous, "result_revision") or 0)
            values["result_revision"] = previous_revision if unchanged else previous_revision + 1
            assignments = ", ".join(f"{key} = ?" for key in values if key != "event_id")
            if not unchanged:
                database.connection.execute(
                    f"UPDATE match_results SET {assignments} WHERE event_id = ?",
                    tuple(values[key] for key in values if key != "event_id") + (str(event["event_id"]),),
                )
            action = "NOOP_EXISTING" if unchanged else (
                "NORMALIZED_EXISTING" if str(previous.get("final_status") or "").casefold() != "finished" else "ENRICHED_EXISTING"
            )
        else:
            values["result_revision"] = 0
            columns = tuple(values)
            database.connection.execute(
                f"INSERT INTO match_results ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(values[column] for column in columns),
            )
            action = "INSERTED_LOCAL_RESULT"
        if action != "NOOP_EXISTING":
            _record_change(
                database.connection, str(event["event_id"]), action=action,
                run_id=run_id,
                previous=previous, new_status=decision.result_status,
                new_ft_home=decision.ft_home, new_ft_away=decision.ft_away,
                new_source=incoming_source, reason=decision.reason,
                evidence_id=evidence_id, changed_at=checked_at,
                revision=int(values.get("result_revision") or 0),
            )
        _queue_upsert(
            database.connection, str(event["event_id"]), status="APPLIED",
            validation_status=decision.reason, attempt_count=0,
            now=checked_at, next_attempt_at=None, resolved_at=checked_at,
        )
        outcome["applied"] = True
        outcome["action"] = action
    return outcome


class ResultFinalizationRunner:
    """Run local Tipico reconciliation and then the existing provider worker."""

    def __init__(self, settings: Any, database: Any, *, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.database = database
        self.connection: sqlite3.Connection = getattr(database, "connection", database)
        self.logger = logger or logging.getLogger("tipico.result_finalization")

    def _candidate_events(
        self,
        *,
        now: datetime,
        limit: int,
        event_id: str | None = None,
    ) -> list[sqlite3.Row]:
        before = _iso(now - timedelta(hours=float(getattr(self.settings, "result_backfill_grace_hours", 3.0))))
        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(events)").fetchall()
        }
        result_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(match_results)").fetchall()
        }
        tables = _event_tables(self.connection)
        canonical_expr = "COALESCE(e.canonical_status, '')" if "canonical_status" in columns else "''"
        if "result_use_h2" in result_columns:
            result_need = """
                r.event_id IS NULL OR r.ft_home IS NULL OR r.ft_away IS NULL
                OR COALESCE(r.result_status, 'PENDING') != 'VERIFIED'
                OR COALESCE(r.result_use_ft, 0) = 0
                OR COALESCE(r.result_use_h2, 0) = 0
                OR (lower(COALESCE(e.status, '')) IN ('ended', 'no_longer_live', 'finished', 'final')
                    AND {canonical_expr} != 'FINISHED')
            """.format(canonical_expr=canonical_expr)
        else:
            result_need = "r.event_id IS NULL OR r.ft_home IS NULL OR r.ft_away IS NULL"
        queue_join = "LEFT JOIN result_backfill_queue q ON q.event_id = e.event_id" if "result_backfill_queue" in tables else ""
        if "result_backfill_queue" in tables:
            queue_filter = """AND (
                    q.event_id IS NULL
                    {quality_retry}
                    OR q.next_attempt_at IS NULL
                    OR q.next_attempt_at <= ?
                )""".format(
                quality_retry=(
                    "OR (q.status IN ('APPLIED', 'CONFIRMED') "
                    "AND COALESCE(r.result_use_h2, 0) = 0)"
                    if "result_use_h2" in result_columns
                    else ""
                )
            )
        else:
            queue_filter = ""
        query = f"""SELECT e.* FROM events e
                   {queue_join}
                   LEFT JOIN match_results r ON r.event_id = e.event_id
                   WHERE lower(e.sport) = 'soccer'
                     AND (e.kickoff_time IS NULL OR e.kickoff_time <= ?)
                     {queue_filter}
                     AND ({result_need})
                     AND (? IS NULL OR e.event_id = ?)
                   ORDER BY CASE lower(COALESCE(e.status, ''))
                                WHEN 'ended' THEN 0
                                WHEN 'finished' THEN 1
                                WHEN 'no_longer_live' THEN 2
                                ELSE 3 END,
                            e.kickoff_time ASC, e.event_id ASC
                   LIMIT ?"""
        params: list[Any] = [before]
        if "result_backfill_queue" in tables:
            params.append(_iso(now))
        params.extend((event_id, event_id, max(1, int(limit))))
        return list(self.connection.execute(query, tuple(params)))

    def local_pass(
        self,
        *,
        apply: bool,
        limit: int,
        now: datetime | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        started = now or _now()
        tables = _event_tables(self.connection)
        rows = self._candidate_events(now=started, limit=limit, event_id=event_id)
        outcomes: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for row in rows:
            event = _as_dict(row)
            decision = decide_event(self.connection, event, tables=tables)
            outcome = _persist_decision(
                self.database,
                event,
                decision,
                apply=apply,
                checked_at=_iso(started),
            )
            outcomes.append(outcome)
            counts[decision.result_status] += 1
        return {
            "events_selected": len(rows),
            "outcomes": outcomes,
            "counts": dict(sorted(counts.items())),
            "applied": sum(1 for item in outcomes if item.get("applied")),
            "local_results": sum(1 for item in outcomes if item.get("action") in {"INSERTED_LOCAL_RESULT", "NORMALIZED_EXISTING", "ENRICHED_EXISTING"}),
            "status_repairs": sum(1 for item in outcomes if item.get("action") == "STATUS_ONLY"),
            "h2_ready": sum(1 for item in outcomes if item.get("result_use_h2")),
            "unresolved": sum(1 for item in outcomes if item.get("result_status") in {"PENDING", "UNAVAILABLE", "CONFLICT"}),
        }

    def run(
        self,
        *,
        apply: bool = False,
        limit: int | None = None,
        workers: int | None = None,
        mode: str | None = None,
        refresh_index: bool = False,
        all_due: bool = False,
        now: datetime | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        started = now or _now()
        batch_limit = max(1, int(limit or getattr(self.settings, "result_backfill_limit", 500)))
        if all_due:
            batch_limit = max(batch_limit, int(self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]))
        local = self.local_pass(
            apply=apply,
            limit=batch_limit,
            now=started,
            event_id=event_id,
        )
        # Freeze the due inventory before local reconciliation updates retry
        # timestamps. Otherwise the provider phase skips precisely the games
        # the local phase could not resolve, and --all-due never means all.
        provider_ids = [item["event_id"] for item in local["outcomes"]]
        provider = ResultBackfillRunner(self.settings, self.database, logger=self.logger).run(
            apply=apply,
            limit=batch_limit,
            workers=workers,
            mode=mode,
            refresh_index=refresh_index,
            now=started,
            event_id=event_id,
            event_ids=provider_ids,
        )
        finish = _now()
        return {
            # CACHED_ONLY and BLOCKED_BY_POLICY are intentionally not folded
            # into PASS: a run that could not perform its provider checks is
            # not a complete historical reconciliation.
            "status": provider.get("status", "PARTIAL"),
            "rule_version": RULE_VERSION,
            "started_at": _iso(started),
            "finished_at": _iso(finish),
            "duration_seconds": round((finish - started).total_seconds(), 3),
            "dry_run": not apply,
            "database_path": str(getattr(self.database, "path", "")),
            "local": local,
            "provider": provider,
            "events_selected": local["events_selected"],
            "applied": local["applied"] + int(provider.get("applied") or 0),
            "h2_ready": local["h2_ready"],
        }


RECOVERY_VERSION = "v0.6.5.1"
RUNNING_STALE_AFTER_SECONDS = 6 * 60 * 60
TECHNICAL_BLOCK_STATUSES = frozenset({
    "ERROR", "BLOCKED_BY_POLICY", "CACHED_ONLY", "DISABLED", "FETCH_ERROR", "HTTP_ERROR",
})
OPEN_ITEM_STAGES = frozenset({"PENDING_LOCAL", "PROVIDER_PENDING", "BLOCKED_PROVIDER", "LOCAL_PROCESSING"})


def _config_fingerprint(settings: Any) -> str:
    """Hash non-secret Settings fields for reproducible recovery manifests."""

    values: dict[str, Any] = {}
    for field in fields(type(settings)):
        key = str(field.name)
        value = getattr(settings, key, None)
        if any(token in key.casefold() for token in ("password", "secret", "token", "cookie", "auth")):
            continue
        values[key] = str(value) if isinstance(value, Path) else value
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _historical_cutoff(as_of_utc: str) -> datetime:
    parsed = _parse_time(as_of_utc) or _now()
    return parsed - timedelta(hours=24)


def _research_flags(database_path: Path) -> tuple[dict[str, dict[str, bool]], dict[str, Any]]:
    """Use the existing Tipico research source to classify entry/backtest readiness."""

    try:
        from tipico_research.source import TipicoSource

        with TipicoSource(database_path) as source:
            source_before = source.sha256
            observations = source.observations()
            source_after = source.refresh_sha256()
        result: dict[str, dict[str, bool]] = {}
        for observation in observations:
            event_id = str(observation.get("event_id") or "")
            if not event_id:
                continue
            item = result.setdefault(
                event_id,
                {"entry_eligible": False, "entry_h2": False, "backtest_ready": False},
            )
            entry = bool(observation.get("entry_eligible"))
            result_valid = bool(observation.get("result_valid"))
            item["entry_eligible"] = item["entry_eligible"] or entry
            item["entry_h2"] = item["entry_h2"] or (entry and result_valid)
            item["backtest_ready"] = item["backtest_ready"] or (
                entry and result_valid and observation.get("evidence_level") in {
                    "A_VERIFIED_REPLAY", "B_LEGACY_EXPLORATORY"
                }
            )
        return result, {
            "status": "OK",
            "source_sha256_before": source_before,
            "source_sha256_after": source_after,
            "source_unchanged": source_before == source_after,
            "observations": len(observations),
        }
    except Exception as exc:  # pragma: no cover - source availability varies by deployment
        return {}, {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "observations": 0,
        }


def _event_rows_for_ids(connection: sqlite3.Connection, event_ids: Sequence[str]) -> list[sqlite3.Row]:
    unique = tuple(dict.fromkeys(str(value) for value in event_ids if str(value)))
    if not unique:
        return []
    rows: list[sqlite3.Row] = []
    for start in range(0, len(unique), 400):
        chunk = unique[start:start + 400]
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(connection.execute(
            f"SELECT * FROM events WHERE event_id IN ({placeholders})",
            chunk,
        ).fetchall())
    order = {event_id: index for index, event_id in enumerate(unique)}
    rows.sort(key=lambda row: order.get(str(row["event_id"]), len(order)))
    return rows


def _result_flags(connection: sqlite3.Connection, event_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT result_use_ft, result_use_h2, result_revision, result_status, result_reason FROM match_results WHERE event_id = ?",
        (str(event_id),),
    ).fetchone()
    if row is None:
        return {
            "result_use_ft": None,
            "result_use_h2": None,
            "result_revision": None,
            "result_status": "PENDING",
            "result_reason": "RESULT_MISSING",
        }
    return dict(row)


def _historical_item_ids(connection: sqlite3.Connection, as_of_utc: str) -> list[str]:
    cutoff = _historical_cutoff(as_of_utc).isoformat()
    rows = connection.execute(
        """SELECT event_id FROM events
           WHERE lower(sport) = 'soccer' AND kickoff_time IS NOT NULL AND kickoff_time <= ?
           ORDER BY kickoff_time, event_id""",
        (cutoff,),
    ).fetchall()
    return [str(row["event_id"]) for row in rows]


def _run_item_rows(connection: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return list(connection.execute(
        """SELECT i.*, e.kickoff_time, e.competition_name, e.competition_country,
                  e.home_team, e.away_team, e.status AS raw_status,
                  r.result_use_ft AS current_result_use_ft,
                  r.result_use_h2 AS current_result_use_h2,
                  r.result_revision AS current_result_revision,
                  r.result_status AS current_result_status,
                  r.result_reason AS current_result_reason
           FROM result_finalization_items i
           LEFT JOIN events e ON e.event_id = i.event_id
           LEFT JOIN match_results r ON r.event_id = i.event_id
           WHERE i.run_id = ? ORDER BY e.kickoff_time, i.event_id""",
        (str(run_id),),
    ).fetchall())


def _coverage_ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator <= 0 else numerator / denominator


def _missing_to_target(numerator: int, denominator: int, target: float = 0.90) -> int | None:
    return None if denominator <= 0 else max(0, math.ceil(target * denominator) - numerator)


def _coverage_for_group(
    rows: Sequence[Mapping[str, Any]],
    *,
    scope: str,
    label: str | None,
    entry_flags: Mapping[str, Mapping[str, bool]] | None,
) -> dict[str, Any]:
    all_count = len(rows)
    historical = [row for row in rows if row.get("cohort") == "HISTORICAL"]
    not_due = [row for row in rows if row.get("cohort") == "NOT_DUE"]
    age_unknown = [row for row in rows if row.get("cohort") == "AGE_UNKNOWN"]
    excluded = [row for row in historical if row.get("stage") == "EXCLUDED"]
    eligible = [row for row in historical if row.get("stage") != "EXCLUDED"]
    ft_ready = sum(int(row.get("after_result_use_ft") == 1) for row in eligible)
    h2_ready = sum(int(row.get("after_result_use_h2") == 1) for row in eligible)
    before_ft = sum(int(row.get("before_result_use_ft") == 1) for row in eligible)
    before_h2 = sum(int(row.get("before_result_use_h2") == 1) for row in eligible)
    entry_flags = entry_flags or {}

    def _research_flag(row: Mapping[str, Any], name: str, stored_column: str) -> bool:
        event_flags = entry_flags.get(str(row.get("event_id")))
        if event_flags is not None and name in event_flags:
            return bool(event_flags.get(name))
        return row.get(stored_column) == 1

    entry = sum(int(_research_flag(row, "entry_eligible", "after_entry_eligible")) for row in eligible)
    entry_h2 = sum(int(_research_flag(row, "entry_h2", "after_entry_h2_usable")) for row in eligible)
    backtest = sum(int(_research_flag(row, "backtest_ready", "after_backtest_ready")) for row in eligible)
    entry_before = sum(int(row.get("before_entry_eligible") == 1) for row in eligible)
    entry_h2_before = sum(int(row.get("before_entry_h2_usable") == 1) for row in eligible)
    backtest_before = sum(int(row.get("before_backtest_ready") == 1) for row in eligible)
    blocked = sum(int(row.get("stage") == "BLOCKED_PROVIDER") for row in eligible)
    provider_open = sum(int(row.get("stage") in {"PROVIDER_PENDING", "BLOCKED_PROVIDER"}) for row in eligible)
    complete_stages = {"COMPLETE", "EXCLUDED"}
    processing_complete = all(row.get("stage") in complete_stages for row in eligible)
    local_pending_stages = {"PENDING_LOCAL", "LOCAL_PROCESSING"}
    local_checked = sum(int(row.get("stage") not in local_pending_stages) for row in historical)
    unprocessed = sum(int(row.get("stage") not in complete_stages) for row in historical)
    unresolved = len(eligible) - sum(int(row.get("stage") == "COMPLETE" and row.get("after_result_use_ft") == 1) for row in eligible)
    ft_status = "NOT_EVALUATED" if not eligible else "MET" if ft_ready / len(eligible) >= 0.90 else "NOT_MET"
    h2_status = "NOT_EVALUATED" if not entry else "MET" if entry_h2 / entry >= 0.90 else "NOT_MET"
    return {
        "scope": scope,
        "label": label,
        "n_all": all_count,
        "n_historical": len(historical),
        "n_not_due": len(not_due),
        "n_age_unknown": len(age_unknown),
        "n_excluded": len(excluded),
        "n_eligible": len(eligible),
        "n_local_checked": local_checked,
        "local_coverage": _coverage_ratio(local_checked, len(historical)),
        "n_ft": ft_ready,
        "n_h2": h2_ready,
        "n_entry": entry,
        "n_entry_h2": entry_h2,
        "n_backtest": backtest,
        "n_entry_before": entry_before,
        "n_entry_h2_before": entry_h2_before,
        "n_backtest_before": backtest_before,
        "n_ft_before": before_ft,
        "n_h2_before": before_h2,
        "processing_complete": int(processing_complete),
        "unprocessed": unprocessed,
        "provider_open": provider_open,
        "provider_blocked": blocked,
        "n_unresolved": unresolved,
        "ft_coverage": _coverage_ratio(ft_ready, len(eligible)),
        "ft_coverage_historical": _coverage_ratio(ft_ready, len(historical)),
        "h2_coverage": _coverage_ratio(h2_ready, len(eligible)),
        "h2_coverage_historical": _coverage_ratio(h2_ready, len(historical)),
        "entry_h2_coverage": _coverage_ratio(entry_h2, entry),
        "backtest_coverage": _coverage_ratio(backtest, entry),
        "ft_target_status": ft_status,
        "entry_h2_target_status": h2_status,
        "missing_ft_to_90": _missing_to_target(ft_ready, len(eligible)),
        "missing_entry_h2_to_90": _missing_to_target(entry_h2, entry),
        "ft_gap_to_90_pct_points": (
            None if not eligible else round((_coverage_ratio(ft_ready, len(eligible)) - 0.90) * 100, 4)
        ),
        "entry_h2_gap_to_90_pct_points": (
            None if not entry else round((_coverage_ratio(entry_h2, entry) - 0.90) * 100, 4)
        ),
    }


def result_recovery_coverage(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    entry_flags: Mapping[str, Mapping[str, bool]] | None = None,
) -> dict[str, Any]:
    """Calculate full-run denominators from persisted event-grain items."""

    rows = [dict(row) for row in _run_item_rows(connection, run_id)]
    global_row = _coverage_for_group(rows, scope="GLOBAL", label=None, entry_flags=entry_flags)
    dimensions: list[dict[str, Any]] = [global_row]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        kickoff = str(row.get("kickoff_time") or "")
        dimensions_for_row = (
            ("DATE", kickoff[:10] if len(kickoff) >= 10 else "UNKNOWN"),
            ("COUNTRY", str(row.get("competition_country") or "UNKNOWN")),
            ("LEAGUE", f"{row.get('competition_country') or 'UNKNOWN'} / {row.get('competition_name') or 'UNKNOWN'}"),
            ("AGE", str(row.get("cohort") or "UNKNOWN")),
        )
        for key in dimensions_for_row:
            groups.setdefault(key, []).append(row)
    for (scope, label), group_rows in sorted(groups.items()):
        dimensions.append(_coverage_for_group(group_rows, scope=scope, label=label, entry_flags=entry_flags))
    return {
        "run_id": str(run_id),
        "global": global_row,
        "rows": dimensions,
        "entry_flags_status": "AVAILABLE" if entry_flags else "UNAVAILABLE",
    }


def _local_inventory_pass(
    runner: ResultFinalizationRunner,
    event_ids: Sequence[str],
    *,
    apply: bool,
    run_id: str | None,
    checked_at: str,
) -> dict[str, Any]:
    rows = _event_rows_for_ids(runner.connection, event_ids)
    tables = _event_tables(runner.connection)
    outcomes: list[dict[str, Any]] = []
    item_updates: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row in rows:
        event = _as_dict(row)
        decision = decide_event(runner.connection, event, tables=tables)
        outcome = _persist_decision(
            runner.database,
            event,
            decision,
            apply=apply,
            checked_at=checked_at,
            run_id=run_id,
        )
        outcome["local_status"] = decision.result_status
        outcomes.append(outcome)
        counts[decision.result_status] += 1
        if apply and run_id:
            flags = _result_flags(runner.connection, event["event_id"])
            complete_locally = bool(decision.result_use_ft and decision.result_use_h2)
            explicitly_unavailable = decision.result_status == "UNAVAILABLE"
            item_updates.append({
                "event_id": event["event_id"],
                "local_status": decision.result_status,
                "provider_status": "NOT_REQUIRED" if complete_locally or explicitly_unavailable else "PENDING",
                "resolution_status": decision.reason,
                "stage": "COMPLETE" if complete_locally or explicitly_unavailable else "PROVIDER_PENDING",
                "after_result_use_ft": flags.get("result_use_ft"),
                "after_result_use_h2": flags.get("result_use_h2"),
                "result_revision": flags.get("result_revision"),
                "evidence_id": outcome.get("evidence_id"),
                "checked_at": checked_at,
                "completed_at": checked_at if complete_locally or explicitly_unavailable else None,
            })
    if apply and run_id and hasattr(runner.database, "update_result_finalization_items_bulk"):
        runner.database.update_result_finalization_items_bulk(run_id, item_updates)
    return {
        "events_selected": len(rows),
        "outcomes": outcomes,
        "counts": dict(sorted(counts.items())),
        "applied": sum(1 for item in outcomes if item.get("applied")),
        "local_results": sum(1 for item in outcomes if item.get("action") in {"INSERTED_LOCAL_RESULT", "NORMALIZED_EXISTING", "ENRICHED_EXISTING"}),
        "status_repairs": sum(1 for item in outcomes if item.get("action") == "STATUS_ONLY"),
        "h2_ready": sum(1 for item in outcomes if item.get("result_use_h2")),
        "unresolved": sum(1 for item in outcomes if item.get("result_status") in {"PENDING", "UNAVAILABLE", "CONFLICT", "PARTIAL"}),
    }


def _apply_provider_items(database: Any, run_id: str, provider: Mapping[str, Any]) -> dict[str, int]:
    connection = database.connection
    status = str(provider.get("status") or "PARTIAL")
    pending = [row for row in _run_item_rows(connection, run_id) if row["stage"] in {"PROVIDER_PENDING", "BLOCKED_PROVIDER"}]
    updates: list[dict[str, Any]] = []
    if status in TECHNICAL_BLOCK_STATUSES:
        for row in pending:
            updates.append({
                "event_id": row["event_id"],
                "provider_status": status,
                "resolution_status": "TECHNICAL_PROVIDER_BLOCK",
                "stage": "BLOCKED_PROVIDER",
                "last_error": status,
            })
    else:
        outcomes = provider.get("outcomes") or provider.get("samples") or []
        seen: set[str] = set()
        for item in outcomes:
            event_id = str(item.get("event_id") or "")
            if not event_id:
                continue
            seen.add(event_id)
            queue_status = str(item.get("queue_status") or item.get("validation_status") or "UNKNOWN")
            technical = queue_status in TECHNICAL_BLOCK_STATUSES
            flags = _result_flags(connection, event_id)
            updates.append({
                "event_id": event_id,
                "provider_status": queue_status,
                "resolution_status": str(item.get("validation_status") or queue_status),
                "stage": "BLOCKED_PROVIDER" if technical else "COMPLETE",
                "attempt_count": int(item.get("attempt_count") or 0),
                "last_error": item.get("error"),
                "provider_match_id": item.get("provider_match_id"),
                "evidence_id": item.get("evidence_id"),
                "after_result_use_ft": flags.get("result_use_ft"),
                "after_result_use_h2": flags.get("result_use_h2"),
                "result_revision": flags.get("result_revision"),
                "checked_at": item.get("checked_at") or _iso(),
                "completed_at": None if technical else (item.get("checked_at") or _iso()),
            })
        # A worker can terminate after its network request but before it
        # returns a per-item outcome. Keep those fixed-list items blocked;
        # they are resumable and never silently counted as complete.
        for row in pending:
            if str(row["event_id"]) not in seen:
                updates.append({
                    "event_id": row["event_id"],
                    "provider_status": "NO_ITEM_OUTCOME",
                    "resolution_status": "TECHNICAL_PROVIDER_INCOMPLETE",
                    "stage": "BLOCKED_PROVIDER",
                    "last_error": "provider returned no item outcome",
                })
    if hasattr(database, "update_result_finalization_items_bulk"):
        database.update_result_finalization_items_bulk(run_id, updates)
    return {
        "updated": len(updates),
        "blocked": sum(1 for item in updates if item.get("stage") == "BLOCKED_PROVIDER"),
        "completed": sum(1 for item in updates if item.get("stage") == "COMPLETE"),
    }


def _inventory_result(
    database: Any,
    settings: Any,
    run_id: str,
    *,
    apply: bool,
    workers: int | None,
    mode: str | None,
    refresh_index: bool,
    entry_flags: Mapping[str, Mapping[str, bool]] | None = None,
) -> dict[str, Any]:
    started = _now()
    connection = database.connection if hasattr(database, "connection") else database
    item_rows = _run_item_rows(connection, run_id)
    local_ids = [
        str(row["event_id"])
        for row in item_rows
        if row["cohort"] == "HISTORICAL" and row["stage"] in {"PENDING_LOCAL", "LOCAL_PROCESSING"}
    ]
    local = _local_inventory_pass(
        ResultFinalizationRunner(settings, database),
        local_ids,
        apply=apply,
        run_id=run_id if apply else None,
        checked_at=_iso(started),
    )
    if apply:
        item_rows = _run_item_rows(connection, run_id)
    provider_ids = [
        str(row["event_id"])
        for row in item_rows
        if row["cohort"] == "HISTORICAL" and row["stage"] == "PROVIDER_PENDING"
    ]
    provider: dict[str, Any]
    if provider_ids:
        provider = ResultBackfillRunner(settings, database, logger=logging.getLogger("tipico.result_finalization")).run(
            apply=apply,
            limit=max(1, len(provider_ids)),
            workers=workers,
            mode=mode,
            refresh_index=refresh_index,
            now=started,
            event_ids=provider_ids,
            run_id=run_id if apply else None,
        )
        if apply:
            provider["item_update"] = _apply_provider_items(database, run_id, provider)
    else:
        provider = {
            "status": "NOT_REQUIRED",
            "events_selected": 0,
            "applied": 0,
            "outcomes": [],
            "provider_days": [],
            "network_allowed": False,
        }
    if apply:
        item_rows = _run_item_rows(connection, run_id)
    after_flags: dict[str, dict[str, bool]] = {}
    research_info: dict[str, Any] = {"status": "NOT_RUN"}
    database_path = getattr(database, "path", None)
    if database_path:
        after_flags, research_info = _research_flags(Path(database_path))
        updates = []
        for row in item_rows:
            info = after_flags.get(str(row["event_id"]), {})
            updates.append({
                "event_id": row["event_id"],
                "after_entry_eligible": int(bool(info.get("entry_eligible"))),
                "after_entry_h2_usable": int(bool(info.get("entry_h2"))),
                "after_backtest_ready": int(bool(info.get("backtest_ready"))),
            })
        if apply and hasattr(database, "update_result_finalization_items_bulk"):
            database.update_result_finalization_items_bulk(run_id, updates)
    coverage = result_recovery_coverage(connection, run_id, entry_flags=after_flags or entry_flags or {})
    global_coverage = coverage["global"]
    item_rows = _run_item_rows(connection, run_id)
    technical_block = any(row["stage"] == "BLOCKED_PROVIDER" for row in item_rows if row["cohort"] == "HISTORICAL" and row["stage"] != "EXCLUDED")
    processing_complete = bool(global_coverage["processing_complete"]) and not technical_block
    status = "BLOCKED_PROVIDER" if technical_block else "COMPLETED"
    target_status = "NOT_EVALUATED" if not processing_complete else (
        "MET" if global_coverage["ft_target_status"] == "MET" and global_coverage["entry_h2_target_status"] in {"MET", "NOT_EVALUATED"}
        else "NOT_MET"
    )
    finished = _now()
    local_summary = {key: value for key, value in local.items() if key != "outcomes"}
    local_summary["outcomes_processed"] = len(local.get("outcomes") or [])
    provider_summary = {key: value for key, value in provider.items() if key != "outcomes"}
    provider_summary["outcomes_processed"] = len(provider.get("outcomes") or [])
    provider_summary["samples"] = list(provider.get("samples") or [])[:10]
    result = {
        "status": status,
        "run_status": status,
        "run_id": str(run_id),
        "rule_version": RULE_VERSION,
        "recovery_version": RECOVERY_VERSION,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "dry_run": not apply,
        "database_path": str(database_path or ""),
        "workers": max(1, int(workers or getattr(settings, "result_backfill_workers", 10))),
        "local": local_summary,
        "provider": provider_summary,
        "research": research_info,
        "coverage": coverage,
        "processing_complete": processing_complete,
        "coverage_target_status": target_status,
        "events_selected": local.get("events_selected", 0),
        "applied": local.get("applied", 0) + int(provider.get("applied") or 0),
        "h2_ready": global_coverage.get("n_h2", 0),
        "provider_blocked": int(global_coverage.get("provider_blocked", 0) or 0),
    }
    if apply and hasattr(database, "update_result_finalization_run"):
        database.update_result_finalization_run(
            run_id,
            status=status,
            run_status=status,
            completed_at=result["finished_at"],
            finished_at=result["finished_at"],
            heartbeat_at=result["finished_at"],
            processing_complete=int(processing_complete),
            coverage_target_status=target_status,
            summary_json=result,
        )
    return result


def start_full_result_recovery(
    database: Any,
    settings: Any,
    *,
    output_dir: Path | str,
    apply: bool = True,
    workers: int | None = 10,
    mode: str | None = "worker",
    refresh_index: bool = True,
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    """Run the fixed-list full recovery workflow, including preflight backup."""

    moment = _parse_time(as_of_utc) or _now()
    as_of = _iso(moment)
    root_output = Path(output_dir).expanduser().resolve()
    if not apply:
        connection = database.connection if hasattr(database, "connection") else database
        event_ids = _historical_item_ids(connection, as_of)
        dry_run_id = f"dry-run-{moment.strftime('%Y%m%dT%H%M%SZ')}"
        local = _local_inventory_pass(
            ResultFinalizationRunner(settings, database), event_ids,
            apply=False, run_id=None, checked_at=as_of,
        )
        provider = ResultBackfillRunner(settings, database).run(
            apply=False,
            limit=max(1, len(event_ids)),
            workers=workers,
            mode=mode,
            refresh_index=refresh_index,
            now=moment,
            event_ids=event_ids,
        ) if event_ids else {"status": "NOT_REQUIRED", "events_selected": 0, "outcomes": []}
        result = {
            "status": provider.get("status", "DRY_RUN_LOCAL_ONLY"),
            "run_status": "DRY_RUN",
            "run_id": dry_run_id,
            "rule_version": RULE_VERSION,
            "recovery_version": RECOVERY_VERSION,
            "dry_run": True,
            "started_at": as_of,
            "finished_at": _iso(),
            "events_selected": len(event_ids),
            "local": local,
            "provider": provider,
            "inventory": {"historical_count": len(event_ids)},
        }
        result["as_of_utc"] = as_of
        return result

    root_output.mkdir(parents=True, exist_ok=True)
    run_started = _iso(moment)
    run_id = acquire_run_lock(database, mode="full_inventory", started_at=run_started)
    run_dir = root_output / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    backup_path = run_dir / "backup" / "tipico-preflight.db"
    try:
        source_id = database.source_database_identity() if hasattr(database, "source_database_identity") else None
        database.update_result_finalization_run(
            run_id,
            source_database_id=source_id,
            source_path=str(getattr(database, "path", "")),
            as_of_utc=as_of,
            code_version=RECOVERY_VERSION,
            rule_version=RULE_VERSION,
            config_fingerprint=_config_fingerprint(settings),
            report_directory=str(run_dir),
            worker_count=max(1, int(workers or 10)),
            heartbeat_at=run_started,
        )
        if hasattr(database, "backup_to"):
            database.backup_to(backup_path)
        inventory = database.seed_result_finalization_inventory(run_id, as_of_utc=as_of)
        database.update_result_finalization_run(run_id, backup_path=str(backup_path))
        before_flags, research_before = _research_flags(Path(database.path))
        database.update_result_finalization_items_bulk(
            run_id,
            [
                {
                    "event_id": event_id,
                    "before_entry_eligible": int(bool(info.get("entry_eligible"))),
                    "before_entry_h2_usable": int(bool(info.get("entry_h2"))),
                    "before_backtest_ready": int(bool(info.get("backtest_ready"))),
                }
                for event_id, info in before_flags.items()
            ],
        )
        database.update_result_finalization_run(run_id, heartbeat_at=_iso())
        result = _inventory_result(
            database, settings, run_id, apply=True, workers=workers, mode=mode,
            refresh_index=refresh_index, entry_flags=before_flags,
        )
        result["as_of_utc"] = as_of
        result["inventory"] = inventory
        result["research_before"] = research_before
        result["backup_path"] = str(backup_path)
        report_paths = write_recovery_reports(run_dir, database.connection, run_id, result=result)
        result["report_paths"] = report_paths
        database.update_result_finalization_run(
            run_id,
            report_directory=str(run_dir),
            summary_json=result,
        )
        release_run_lock(database, run_id, status=result["status"], summary=result)
        return result
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "run_status": "FAILED",
            "run_id": run_id,
            "rule_version": RULE_VERSION,
            "error": f"{type(exc).__name__}: {exc}",
        }
        try:
            database.update_result_finalization_run(
                run_id,
                status="FAILED",
                run_status="FAILED",
                last_error=failure["error"],
                finished_at=_iso(),
                completed_at=_iso(),
                summary_json=failure,
            )
        finally:
            release_run_lock(database, run_id, status="FAILED", summary=failure)
        raise


def _table_exists_connection(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def resume_full_result_recovery(
    database: Any,
    settings: Any,
    run_id: str,
    *,
    workers: int | None = 10,
    mode: str | None = "worker",
    refresh_index: bool = True,
) -> dict[str, Any]:
    """Resume a fixed inventory after a crash/provider block."""

    row = database.result_finalization_run(run_id)
    if row is None:
        raise ValueError(f"Unbekannter Recovery-Lauf: {run_id}")
    status = str(row["run_status"] or row["status"] or "")
    if status == "COMPLETED":
        result = json.loads(row["summary_json"]) if row["summary_json"] else {"status": status, "run_id": run_id}
        return result
    heartbeat = _parse_time(row["heartbeat_at"] or row["started_at"])
    if status == "RUNNING" and heartbeat and (_now() - heartbeat).total_seconds() < RUNNING_STALE_AFTER_SECONDS:
        raise RuntimeError("RESULT_FINALIZATION_ALREADY_RUNNING")
    # Reclaim the same persistent run row.  This avoids creating a second
    # visible "SUCCESS" run merely to protect a resumed inventory.
    database.update_result_finalization_run(run_id, status="PAUSED", run_status="PAUSED", heartbeat_at=_iso())
    lease_id = acquire_run_lock(database, mode="resume", started_at=_iso(), run_id=run_id)
    try:
        database.update_result_finalization_run(run_id, status="RUNNING", run_status="RUNNING", last_error=None, heartbeat_at=_iso())
        # A provider-blocked item is an unfinished fixed-list item, not a
        # terminal decision.  Reopen it explicitly so a later worker retry
        # can revisit it.  ``resume --mode cached`` will block it again with
        # a truthful checkpoint, while worker/manual mode can make progress.
        blocked_items = database.result_finalization_items(run_id, stages=("BLOCKED_PROVIDER",))
        database.update_result_finalization_items_bulk(
            run_id,
            [
                {
                    "event_id": item["event_id"],
                    "provider_status": "PENDING",
                    "resolution_status": "PENDING_PROVIDER_RETRY",
                    "stage": "PROVIDER_PENDING",
                    "last_error": None,
                    "completed_at": None,
                }
                for item in blocked_items
            ],
        )
        result = _inventory_result(
            database, settings, run_id, apply=True, workers=workers, mode=mode,
            refresh_index=refresh_index,
        )
        run_dir = Path(row["report_directory"] or (Path(getattr(database, "path", ".")).parent / "result_finalization" / str(run_id)))
        result["report_paths"] = write_recovery_reports(run_dir, database.connection, run_id, result=result)
        release_run_lock(database, lease_id, status=result["status"], summary=result)
        return result
    except Exception as exc:
        database.update_result_finalization_run(run_id, status="FAILED", run_status="FAILED", last_error=f"{type(exc).__name__}: {exc}", finished_at=_iso(), completed_at=_iso())
        release_run_lock(database, lease_id, status="FAILED", summary={"resumed_run_id": run_id, "error": str(exc)})
        raise


def status_full_result_recovery(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM result_finalization_runs WHERE run_id = ?", (str(run_id),)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unbekannter Recovery-Lauf: {run_id}")
    items = _run_item_rows(connection, run_id)
    entry_flags: dict[str, dict[str, bool]] = {}
    research: dict[str, Any] = {"status": "UNAVAILABLE"}
    source_path = row["source_path"] if "source_path" in row.keys() else None
    if source_path and Path(str(source_path)).exists():
        entry_flags, research = _research_flags(Path(str(source_path)))
    coverage = result_recovery_coverage(connection, run_id, entry_flags=entry_flags)
    return {
        "run": dict(row),
        "coverage": coverage,
        "research": research,
        "items": {
            "total": len(items),
            "stages": dict(Counter(str(item["stage"]) for item in items)),
            "cohorts": dict(Counter(str(item["cohort"]) for item in items)),
        },
    }


def write_recovery_reports(
    output_dir: Path | str,
    connection: sqlite3.Connection,
    run_id: str,
    *,
    result: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Rebuild all V0.6.5.1 reports from the persisted run inventory."""

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_row = connection.execute(
        "SELECT * FROM result_finalization_runs WHERE run_id = ?", (str(run_id),)
    ).fetchone()
    items = _run_item_rows(connection, run_id)
    audit = audit_connection(connection)
    ids = {str(row["event_id"]) for row in items}
    audit_rows = [row for row in audit.get("rows", []) if str(row.get("event_id")) in ids]
    audit_fields = list(audit_rows[0].keys()) if audit_rows else ["event_id", "kickoff_at", "country", "competition"]
    audit_path = output / "RESULT_FINALIZATION_AUDIT.csv"
    write_csv(audit_path, audit_rows, audit_fields)

    change_metadata: dict[str, dict[str, Any]] = {}
    if _table_exists_connection(connection, "result_finalization_changes"):
        for change_row in connection.execute(
            """
            SELECT event_id, action, revision
            FROM result_finalization_changes
            WHERE run_id = ?
            ORDER BY event_id, revision, changed_at
            """,
            (str(run_id),),
        ).fetchall():
            event_id = str(change_row["event_id"])
            metadata = change_metadata.setdefault(event_id, {"actions": [], "revisions": []})
            action = str(change_row["action"] or "")
            if action and action not in metadata["actions"]:
                metadata["actions"].append(action)
            if change_row["revision"] is not None:
                metadata["revisions"].append(int(change_row["revision"]))

    change_fields = [
        "run_id", "event_id", "cohort", "kickoff_at", "country", "competition",
        "stage", "local_status", "provider_status", "resolution_status", "eligibility_reason",
        "attempt_count", "provider_match_id", "evidence_id", "result_revision",
        "before_result_use_ft", "after_result_use_ft", "before_result_use_h2", "after_result_use_h2",
        "before_entry_eligible", "after_entry_eligible",
        "before_entry_h2_usable", "after_entry_h2_usable",
        "before_backtest_ready", "after_backtest_ready",
        "last_error", "checked_at", "completed_at", "change_kind", "change_action",
    ]
    change_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    for row in items:
        item = dict(row)
        metadata = change_metadata.get(str(item.get("event_id")), {})
        actions = metadata.get("actions", [])
        has_persisted_change = bool(actions)
        change = {
            "run_id": run_id,
            "event_id": item.get("event_id"),
            "cohort": item.get("cohort"),
            "kickoff_at": item.get("kickoff_time"),
            "country": item.get("competition_country"),
            "competition": item.get("competition_name"),
            "stage": item.get("stage"),
            "local_status": item.get("local_status"),
            "provider_status": item.get("provider_status"),
            "resolution_status": item.get("resolution_status"),
            "eligibility_reason": item.get("eligibility_reason"),
            "attempt_count": item.get("attempt_count"),
            "provider_match_id": item.get("provider_match_id"),
            "evidence_id": item.get("evidence_id"),
            "result_revision": item.get("result_revision"),
            "before_result_use_ft": item.get("before_result_use_ft"),
            "after_result_use_ft": item.get("after_result_use_ft"),
            "before_result_use_h2": item.get("before_result_use_h2"),
            "after_result_use_h2": item.get("after_result_use_h2"),
            "before_entry_eligible": item.get("before_entry_eligible"),
            "after_entry_eligible": item.get("after_entry_eligible"),
            "before_entry_h2_usable": item.get("before_entry_h2_usable"),
            "after_entry_h2_usable": item.get("after_entry_h2_usable"),
            "before_backtest_ready": item.get("before_backtest_ready"),
            "after_backtest_ready": item.get("after_backtest_ready"),
            "last_error": item.get("last_error"),
            "checked_at": item.get("checked_at"),
            "completed_at": item.get("completed_at"),
            "change_kind": "ACTUAL_CHANGE" if has_persisted_change or (
                item.get("before_result_use_ft") != item.get("after_result_use_ft")
                or item.get("before_result_use_h2") != item.get("after_result_use_h2")
                or item.get("before_entry_eligible") != item.get("after_entry_eligible")
                or item.get("before_entry_h2_usable") != item.get("after_entry_h2_usable")
                or item.get("before_backtest_ready") != item.get("after_backtest_ready")
                or item.get("stage") in {"EXCLUDED", "BLOCKED_PROVIDER"}
            ) else "CHECK_ONLY",
            "change_action": ";".join(str(action) for action in actions) if actions else None,
        }
        change_rows.append(change)
        historical_eligible = item.get("cohort") == "HISTORICAL" and item.get("stage") != "EXCLUDED"
        unresolved = historical_eligible and (
            item.get("after_result_use_ft") != 1
            or item.get("after_result_use_h2") != 1
            or item.get("stage") == "BLOCKED_PROVIDER"
            or str(item.get("resolution_status") or "") in {"CONFLICT", "RESULT_CONFLICT"}
        )
        if unresolved:
            unresolved_rows.append(change)
    changes_path = output / "RESULT_FINALIZATION_CHANGES.csv"
    unresolved_path = output / "RESULT_FINALIZATION_UNRESOLVED.csv"
    write_csv(changes_path, change_rows, change_fields)
    write_csv(unresolved_path, unresolved_rows, change_fields)

    entry_flags: dict[str, dict[str, bool]] = {}
    research_info: dict[str, Any] = {"status": "UNAVAILABLE"}
    source_path = run_row["source_path"] if run_row is not None and "source_path" in run_row.keys() else None
    if source_path and Path(str(source_path)).exists():
        entry_flags, research_info = _research_flags(Path(str(source_path)))
    coverage = result_recovery_coverage(connection, run_id, entry_flags=entry_flags)
    coverage_path = output / "RESULT_RECOVERY_COVERAGE.csv"
    coverage_fields = list(coverage["rows"][0].keys()) if coverage.get("rows") else ["scope", "n_all"]
    write_csv(coverage_path, coverage.get("rows", []), coverage_fields)
    provider_days = [dict(row) for row in connection.execute(
        "SELECT * FROM result_finalization_provider_days WHERE run_id = ? ORDER BY observation_date",
        (str(run_id),),
    ).fetchall()] if _table_exists_connection(connection, "result_finalization_provider_days") else []
    provider_days_path = output / "RESULT_RECOVERY_PROVIDER_DAYS.csv"
    write_csv(
        provider_days_path,
        provider_days,
        list(provider_days[0].keys()) if provider_days else ["run_id", "observation_date", "status"],
    )

    global_coverage = coverage.get("global", {})
    summary = dict(result or {})
    if not result and run_row is not None and run_row["summary_json"]:
        try:
            persisted_summary = json.loads(str(run_row["summary_json"]))
            if isinstance(persisted_summary, Mapping):
                summary = dict(persisted_summary)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    persisted_local = summary.get("local", {}) if isinstance(summary.get("local"), Mapping) else {}
    persisted_provider = summary.get("provider", {}) if isinstance(summary.get("provider"), Mapping) else {}
    total_duration_seconds = summary.get("duration_seconds")
    if run_row is not None:
        started_at = _parse_time(run_row["started_at"])
        finished_at = _parse_time(run_row["finished_at"] or run_row["completed_at"])
        if started_at is not None and finished_at is not None:
            total_duration_seconds = round(max(0.0, (finished_at - started_at).total_seconds()), 3)
    summary.pop("local", None)
    summary.pop("provider", None)
    summary["run_id"] = str(run_id)
    summary["coverage"] = coverage
    summary["research"] = research_info
    summary["provider_days"] = {
        "rows": len(provider_days),
        "statuses": dict(Counter(str(row.get("status") or "UNKNOWN") for row in provider_days)),
    }
    summary["database_quick_check"] = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    summary["run_metadata"] = dict(run_row) if run_row else None
    provider_summary = persisted_provider
    client_metrics = provider_summary.get("client_metrics", {}) if isinstance(provider_summary.get("client_metrics"), Mapping) else {}
    network_requests = int(client_metrics.get("network_served", client_metrics.get("requests", 0)) or 0)
    details_fetched = int(provider_summary.get("details_fetched", 0) or 0)
    summary["total_duration_seconds"] = total_duration_seconds
    summary["runtime"] = {
        "total_duration_seconds": total_duration_seconds,
        "network_requests": network_requests,
        "details_fetched": details_fetched,
    }
    manifest_path = output / "RESULT_RECOVERY_MANIFEST.json"
    manifest = {
        "schema_version": RECOVERY_VERSION,
        "run_id": str(run_id),
        "source_database_id": run_row["source_database_id"] if run_row else None,
        "source_path": run_row["source_path"] if run_row else None,
        "as_of_utc": run_row["as_of_utc"] if run_row else None,
        "code_version": run_row["code_version"] if run_row else RECOVERY_VERSION,
        "rule_version": run_row["rule_version"] if run_row else RULE_VERSION,
        "config_fingerprint": run_row["config_fingerprint"] if run_row else None,
        "backup_path": run_row["backup_path"] if run_row else None,
        "backup_sha256": _file_sha256(Path(run_row["backup_path"])) if run_row and run_row["backup_path"] else None,
        "report_directory": str(output),
        "generated_at_utc": _iso(),
        "frozen_event_count": run_row["frozen_event_count"] if run_row else len(items),
        "historical_count": run_row["historical_count"] if run_row else global_coverage.get("n_historical"),
        "excluded_count": run_row["excluded_count"] if run_row else global_coverage.get("n_excluded"),
        "inventory_fingerprint": run_row["inventory_fingerprint"] if run_row else None,
        "database_quick_check": summary["database_quick_check"],
        "coverage_target_status": run_row["coverage_target_status"] if run_row else summary.get("coverage_target_status"),
        "reports": {},
        "research": research_info,
    }
    _write_json_file(manifest_path, manifest)
    status_path = output / "RESULT_RECOVERY_STATUS.json"
    status_json = {
        "run_id": str(run_id),
        "run_status": run_row["run_status"] if run_row else summary.get("run_status"),
        "processing_complete": bool(run_row["processing_complete"]) if run_row else bool(summary.get("processing_complete")),
        "coverage_target_status": run_row["coverage_target_status"] if run_row else summary.get("coverage_target_status"),
        "coverage": global_coverage,
        "runtime": summary.get("runtime", {}),
        "research": research_info,
        "provider_days": summary.get("provider_days", {}),
        "report_generated_at": _iso(),
    }
    _write_json_file(status_path, status_json)
    validation_path = output / "RESULT_RECOVERY_VALIDATION.md"
    validation_path.write_text(
        "\n".join([
            f"# Result Recovery Validation {run_id}", "",
            f"- SQLite quick_check: **{summary['database_quick_check']}**",
            f"- Gefrorene Events: **{len(items):,}**; eindeutige IDs: **{len(ids):,}**",
            f"- Prüfliste eindeutig: **{'JA' if len(items) == len(ids) else 'NEIN'}**",
            f"- Historisch: **{global_coverage.get('n_historical', 0):,}**; nicht fällig: **{global_coverage.get('n_not_due', 0):,}**; Alter unbekannt: **{global_coverage.get('n_age_unknown', 0):,}**; bereinigt eligible: **{global_coverage.get('n_eligible', 0):,}**",
            f"- Lokale Prüfung: **{global_coverage.get('n_local_checked', 0):,}/{global_coverage.get('n_historical', 0):,}** ({_coverage_ratio(global_coverage.get('n_local_checked', 0), global_coverage.get('n_historical', 0))})",
            f"- FT bereinigt: **{global_coverage.get('n_ft', 0):,}/{global_coverage.get('n_eligible', 0):,}** ({_coverage_ratio(global_coverage.get('n_ft', 0), global_coverage.get('n_eligible', 0))})",
            f"- FT brutto: **{global_coverage.get('n_ft', 0):,}/{global_coverage.get('n_historical', 0):,}** ({global_coverage.get('ft_coverage_historical')})",
            f"- H2-Ergebnis: **{global_coverage.get('n_h2', 0):,}/{global_coverage.get('n_eligible', 0):,}** ({_coverage_ratio(global_coverage.get('n_h2', 0), global_coverage.get('n_eligible', 0))})",
            f"- H2-Entry: **{global_coverage.get('n_entry_h2', 0):,}/{global_coverage.get('n_entry', 0):,}** ({_coverage_ratio(global_coverage.get('n_entry_h2', 0), global_coverage.get('n_entry', 0))})",
            f"- Provider-Tageszeilen: **{len(provider_days):,}**; Statusse: `{json.dumps(dict(Counter(str(row.get('status') or 'UNKNOWN') for row in provider_days)), ensure_ascii=False)}`",
            "- Diese Validierung enthält keine künstliche Freigabe unbekannter oder konflikthafter Ergebnisse.",
            "- Resume-/Idempotenzstatus wird aus den persistierten Item- und Run-Zeilen abgeleitet; ein technisch blockierter Providerlauf bleibt offen.",
        ]) + "\n",
        encoding="utf-8",
    )
    status_md_path = output / "RESULT_FINALIZATION_STATUS.md"
    status_md_path.write_text(
        "\n".join([
            "# V0.6.5.1 Result Finalization Status", "",
            f"Lauf: `{run_id}`; Regel: `{manifest['rule_version']}`; erstellt: `{manifest['generated_at_utc']}`.", "",
            f"- Laufstatus: **{run_row['run_status'] if run_row else summary.get('run_status', 'UNKNOWN')}**; technische Provider-Blockaden: **{global_coverage.get('provider_blocked', 0):,}**",
            f"- Laufzeit: **{total_duration_seconds if total_duration_seconds is not None else '—'} s**; Worker: **{summary.get('workers', run_row['worker_count'] if run_row else '—')}**; Netzwerk-Requests: **{network_requests:,}**; Details: **{details_fetched:,}**",
            "## Bezugsmenge", "",
            f"- N_all: **{global_coverage.get('n_all', 0):,}**",
            f"- N_historical: **{global_coverage.get('n_historical', 0):,}**",
            f"- N_not_due: **{global_coverage.get('n_not_due', 0):,}**",
            f"- N_age_unknown: **{global_coverage.get('n_age_unknown', 0):,}**",
            f"- N_excluded: **{global_coverage.get('n_excluded', 0):,}**",
            f"- N_eligible: **{global_coverage.get('n_eligible', 0):,}**",
            f"- Lokale Prüfung: **{global_coverage.get('n_local_checked', 0):,}/{global_coverage.get('n_historical', 0):,}** ({_coverage_ratio(global_coverage.get('n_local_checked', 0), global_coverage.get('n_historical', 0))})",
            f"- Bearbeitung vollständig: **{'JA' if global_coverage.get('processing_complete') else 'NEIN'}**",
            "",
            "## Abdeckung", "",
            f"- FT bereinigt: **{global_coverage.get('n_ft', 0):,}/{global_coverage.get('n_eligible', 0):,}** ({_coverage_ratio(global_coverage.get('n_ft', 0), global_coverage.get('n_eligible', 0))}); Zielstatus **{global_coverage.get('ft_target_status')}**; fehlend bis 90 %: **{global_coverage.get('missing_ft_to_90')}**",
            f"- FT brutto: **{global_coverage.get('n_ft', 0):,}/{global_coverage.get('n_historical', 0):,}** ({global_coverage.get('ft_coverage_historical')})",
            f"- H2-Ergebnis: **{global_coverage.get('n_h2', 0):,}/{global_coverage.get('n_eligible', 0):,}**",
            f"- H2-Entry: **{global_coverage.get('n_entry_h2', 0):,}/{global_coverage.get('n_entry', 0):,}**; Zielstatus **{global_coverage.get('entry_h2_target_status')}**; fehlend bis 90 %: **{global_coverage.get('missing_entry_h2_to_90')}**",
            f"- Backtest-fähig nach Research-Regeln: **{global_coverage.get('n_backtest', 0):,}**",
            f"- Vorher/Nachher FT: **{global_coverage.get('n_ft_before', 0):,} → {global_coverage.get('n_ft', 0):,}**; H2: **{global_coverage.get('n_h2_before', 0):,} → {global_coverage.get('n_h2', 0):,}**; H2-Entry: **{global_coverage.get('n_entry_h2_before', 0):,} → {global_coverage.get('n_entry_h2', 0):,}**",
            f"- Provider offen: **{global_coverage.get('provider_open', 0):,}**; technisch blockiert: **{global_coverage.get('provider_blocked', 0):,}**",
            f"- Laufbezogene lokale Ergebnisänderungen: **{persisted_local.get('local_results', 0)}**; FotMob-Anwendungen: **{persisted_provider.get('applied', 0)}**",
            f"- Research-Flags: **{research_info.get('status', 'UNAVAILABLE')}**; Beobachtungen: **{research_info.get('observations', 0):,}**",
            "",
            "## Dateien", "",
            "- `RESULT_FINALIZATION_AUDIT.csv`: genau eine Zeile je Event der eingefrorenen Liste.",
            "- `RESULT_FINALIZATION_CHANGES.csv`: alle geprüften Items mit tatsächlicher Änderung oder CHECK_ONLY.",
            "- `RESULT_FINALIZATION_UNRESOLVED.csv`: alle historischen nicht nutzbaren/offenen Items.",
            "- `RESULT_RECOVERY_COVERAGE.csv`: global sowie Datum/Land/Liga/Alterskohorte.",
            "- `RESULT_RECOVERY_PROVIDER_DAYS.csv`: benötigte FotMob-Tage und Fehlerstatus.",
        ]) + "\n",
        encoding="utf-8",
    )
    paths = {
        "status": str(status_md_path),
        "audit": str(audit_path),
        "changes": str(changes_path),
        "unresolved": str(unresolved_path),
        "manifest": str(manifest_path),
        "coverage": str(coverage_path),
        "provider_days": str(provider_days_path),
        "validation": str(validation_path),
        "status_json": str(status_path),
    }
    manifest["reports"] = paths
    _write_json_file(manifest_path, manifest)
    return paths


def _write_json_file(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def audit_connection(connection: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """Produce a read-only, event-grain inventory for reports and QA."""

    moment = now or _now()
    tables = _event_tables(connection)
    rows = connection.execute(
        "SELECT * FROM events WHERE lower(sport) = 'soccer' ORDER BY kickoff_time, event_id"
    ).fetchall()
    audit_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    by_country: Counter[str] = Counter()
    by_competition: Counter[str] = Counter()
    for row in rows:
        event = _as_dict(row)
        decision = decide_event(connection, event, tables=tables)
        current_state = connection.execute(
            "SELECT status, period, score_home, score_away, observed_at FROM current_event_state WHERE event_id = ?",
            (str(row["event_id"]),),
        ).fetchone() if "current_event_state" in tables else None
        result_columns = {
            str(item["name"])
            for item in connection.execute("PRAGMA table_info(match_results)").fetchall()
        } if "match_results" in tables else set()
        result_select = ["result_source" if "result_source" in result_columns else "NULL AS result_source"]
        for field, fallback in (
            ("result_status", "NULL"),
            ("result_use_ft", "NULL"),
            ("result_use_h2", "NULL"),
            ("result_reason", "NULL"),
            ("result_revision", "0"),
        ):
            result_select.append(field if field in result_columns else f"{fallback} AS {field}")
        result = connection.execute(
            f"SELECT {', '.join(result_select)} FROM match_results WHERE event_id = ?",
            (str(row["event_id"]),),
        ).fetchone() if "match_results" in tables else None
        kickoff = _parse_time(row["kickoff_time"])
        age_bucket = "UNKNOWN"
        if kickoff:
            age = (moment - kickoff).total_seconds() / 3600
            age_bucket = "UNDER_24H" if age < 24 else "ONE_TO_SEVEN_DAYS" if age < 24 * 7 else "OVER_7_DAYS"
        status = _token(row["status"])
        counts[f"raw_status:{status or 'UNKNOWN'}"] += 1
        counts[f"result_status:{_row_value(result, 'result_status') or decision.result_status}"] += 1
        counts[f"canonical:{decision.canonical_status}"] += 1
        by_country[str(row["competition_country"] or "UNKNOWN")] += 1
        by_competition[str(row["competition_name"] or "UNKNOWN")] += 1
        audit_rows.append(
            {
                "event_id": row["event_id"],
                "kickoff_at": row["kickoff_time"],
                "age_bucket": age_bucket,
                "country": row["competition_country"],
                "competition": row["competition_name"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "raw_status": row["status"],
                "raw_period": row["period"],
                "current_status": _row_value(current_state, "status"),
                "current_period": _row_value(current_state, "period"),
                "canonical_status": decision.canonical_status,
                "result_status": _row_value(result, "result_status") or decision.result_status,
                "result_source": _row_value(result, "result_source") or decision.ft_source,
                "result_use_ft": int(
                    _row_value(result, "result_use_ft")
                    if _row_value(result, "result_use_ft") is not None
                    else decision.result_use_ft
                ),
                "result_use_h2": int(
                    _row_value(result, "result_use_h2")
                    if _row_value(result, "result_use_h2") is not None
                    else decision.result_use_h2
                ),
                "result_reason": _row_value(result, "result_reason") or decision.reason,
                "existing_result_revision": _row_value(result, "result_revision") or 0,
                "local_evidence_type": decision.evidence.source_record_type if decision.evidence else None,
                "local_evidence_id": decision.evidence.source_record_id if decision.evidence else None,
                "local_evidence_observed_at": decision.evidence.observed_at if decision.evidence else None,
                "ht_home": decision.ht_home,
                "ht_away": decision.ht_away,
                "ft_home": decision.ft_home,
                "ft_away": decision.ft_away,
                "scope_status": decision.scope_status,
                "flags": ";".join(decision.flags),
            }
        )
    return {
        "rule_version": RULE_VERSION,
        "generated_at": _iso(moment),
        "database_tables": sorted(tables),
        "games": len(audit_rows),
        "counts": dict(sorted(counts.items())),
        "by_country": dict(sorted(by_country.items())),
        "by_competition": dict(sorted(by_competition.items(), key=lambda item: (-item[1], item[0]))),
        "rows": audit_rows,
    }


def write_csv(path: Path, rows: list[Mapping[str, Any]], fieldnames: list[str]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_status_reports(output_dir: Path, audit: Mapping[str, Any], result: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Write the stable V0.6.5 report set without exporting database rows."""

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = list(audit.get("rows") or [])
    fields = list(rows[0].keys()) if rows else [
        "event_id", "kickoff_at", "country", "competition", "raw_status",
        "canonical_status", "result_status", "result_use_ft", "result_use_h2",
        "result_reason",
    ]
    audit_path = output_dir / "RESULT_FINALIZATION_AUDIT.csv"
    write_csv(audit_path, rows, fields)
    changes: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    change_fields = [
        "phase", "event_id", "action", "queue_status", "reason",
        "result_status", "validation_status", "result_use_ft",
        "result_use_h2", "source_record_type", "evidence_id",
        "provider_match_id", "flags",
    ]

    def _report_item(item: Mapping[str, Any], phase: str) -> dict[str, Any]:
        return {
            "phase": phase,
            "event_id": item.get("event_id"),
            "action": item.get("action"),
            "queue_status": item.get("queue_status"),
            "reason": item.get("reason") or item.get("error"),
            "result_status": item.get("result_status"),
            "validation_status": item.get("validation_status"),
            "result_use_ft": item.get("result_use_ft"),
            "result_use_h2": item.get("result_use_h2"),
            "source_record_type": item.get("source_record_type"),
            "evidence_id": item.get("evidence_id"),
            "provider_match_id": item.get("provider_match_id"),
            "flags": ";".join(str(flag) for flag in (item.get("flags") or [])),
        }

    local_items = (result or {}).get("local", {}).get("outcomes", [])
    provider_items = (result or {}).get("provider", {}).get("samples", [])
    for item in local_items:
        row = _report_item(item, "TIPICO_LOCAL")
        if item.get("action") or item.get("result_status") in {"CONFLICT", "PENDING", "UNAVAILABLE", "PARTIAL"}:
            changes.append(row)
        if item.get("result_status") in {"CONFLICT", "PENDING", "UNAVAILABLE", "PARTIAL"}:
            unresolved.append(row)
    for item in provider_items:
        row = _report_item(item, "FOTMOB_PROVIDER")
        if item.get("queue_status") != "APPLIED":
            changes.append(row)
        if item.get("queue_status") not in {"APPLIED", "CONFIRMED"}:
            unresolved.append(row)
    changes_path = output_dir / "RESULT_FINALIZATION_CHANGES.csv"
    unresolved_path = output_dir / "RESULT_FINALIZATION_UNRESOLVED.csv"
    write_csv(changes_path, changes, change_fields)
    write_csv(unresolved_path, unresolved, change_fields)

    counts = audit.get("counts") or {}
    md = [
        f"# V0.6.5 Result Finalization Status",
        "",
        f"Regelversion: `{audit.get('rule_version', RULE_VERSION)}`",
        f"Erstellt: `{audit.get('generated_at')}`",
        "",
        "## Bezugsmenge",
        "",
        f"- Fußball-Events im Audit: **{audit.get('games', 0):,}**",
        f"- Rohstatus: `{json.dumps({key: value for key, value in counts.items() if key.startswith('raw_status:')}, ensure_ascii=False)}`",
        f"- Ergebnisstatus: `{json.dumps({key: value for key, value in counts.items() if key.startswith('result_status:')}, ensure_ascii=False)}`",
        f"- Kanonische Spielstatus: `{json.dumps({key: value for key, value in counts.items() if key.startswith('canonical:')}, ensure_ascii=False)}`",
        "",
        "## Lauf",
        "",
        f"- Geprüfte lokale Kandidaten: **{(result or {}).get('events_selected', 0):,}**",
        f"- Lokal angewendet: **{(result or {}).get('local', {}).get('applied', 0):,}**",
        f"- Lokale Ergebnisreparaturen: **{(result or {}).get('local', {}).get('local_results', 0):,}**",
        f"- Reine Statusreparaturen: **{(result or {}).get('local', {}).get('status_repairs', 0):,}**",
        f"- Neue H2-fähige Ergebnisse im lokalen Lauf: **{(result or {}).get('local', {}).get('h2_ready', 0):,}**",
        f"- FotMob-Backfill angewendet: **{(result or {}).get('provider', {}).get('applied', 0):,}**",
        f"- Offene/konflikthafte Laufzeilen: **{len(unresolved):,}**",
        "",
        "## Ausstehend",
        "",
        "Offene Fälle werden nicht als verlorene Wette und nicht als gültiges H2-Label gezählt. Die Detailzeilen stehen in `RESULT_FINALIZATION_UNRESOLVED.csv`.",
        "",
        "## Dateien",
        "",
        "- `RESULT_FINALIZATION_AUDIT.csv`: Event-Grain-Audit",
        "- `RESULT_FINALIZATION_CHANGES.csv`: vorgeschlagene bzw. angewendete lokale Änderungen",
        "- `RESULT_FINALIZATION_UNRESOLVED.csv`: offene, konflikthafte oder nicht verfügbare Ergebnisse",
    ]
    status_path = output_dir / "RESULT_FINALIZATION_STATUS.md"
    status_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return {
        "status": str(status_path),
        "audit": str(audit_path),
        "changes": str(changes_path),
        "unresolved": str(unresolved_path),
    }


def acquire_run_lock(
    database: Any,
    *,
    mode: str,
    started_at: str,
    run_id: str | None = None,
) -> str:
    """Use the DB unique index as a cross-process single-run lease.

    A stale lease is moved to ``PAUSED`` before a new one is acquired.  This
    makes a reboot or killed systemd process resumable without treating the
    old run as completed.
    """

    requested_run_id = str(run_id) if run_id else None
    run_id = requested_run_id or str(uuid.uuid4())
    now = _parse_time(started_at) or _now()
    try:
        with database._lock, database.connection:
            active = database.connection.execute(
                "SELECT run_id, heartbeat_at, started_at FROM result_finalization_runs WHERE lock_key = 'default' AND status = 'RUNNING' LIMIT 1"
            ).fetchone()
            if active is not None:
                heartbeat = _parse_time(active["heartbeat_at"] or active["started_at"])
                if heartbeat is None or (now - heartbeat).total_seconds() >= RUNNING_STALE_AFTER_SECONDS:
                    database.connection.execute(
                        """UPDATE result_finalization_runs
                           SET status = 'PAUSED', run_status = 'PAUSED',
                               last_error = COALESCE(last_error, 'stale lease recovered'),
                               heartbeat_at = ?
                           WHERE run_id = ? AND status = 'RUNNING'""",
                        (_iso(now), str(active["run_id"])),
                    )
                else:
                    raise RuntimeError("RESULT_FINALIZATION_ALREADY_RUNNING")
            if requested_run_id is not None:
                updated = database.connection.execute(
                    """UPDATE result_finalization_runs
                       SET lock_key = 'default', status = 'RUNNING', mode = ?, pid = ?,
                           heartbeat_at = ?, run_status = 'RUNNING', last_error = NULL
                       WHERE run_id = ? AND status IN ('PAUSED', 'FAILED', 'BLOCKED_PROVIDER')""",
                    (mode, os.getpid(), _iso(now), requested_run_id),
                ).rowcount
                if updated != 1:
                    raise RuntimeError(f"RESULT_FINALIZATION_RESUME_NOT_ALLOWED:{requested_run_id}")
            else:
                database.connection.execute(
                    """INSERT INTO result_finalization_runs
                       (run_id, lock_key, started_at, status, mode, pid,
                        heartbeat_at, run_status, coverage_target_status)
                       VALUES (?, 'default', ?, 'RUNNING', ?, ?, ?, 'RUNNING', 'NOT_EVALUATED')""",
                    (run_id, started_at, mode, os.getpid(), _iso(now)),
                )
    except sqlite3.IntegrityError as exc:
        raise RuntimeError("RESULT_FINALIZATION_ALREADY_RUNNING") from exc
    return run_id


def release_run_lock(database: Any, run_id: str, *, status: str, summary: Mapping[str, Any]) -> None:
    with database._lock, database.connection:
        database.connection.execute(
            """UPDATE result_finalization_runs
               SET finished_at = ?, completed_at = ?, heartbeat_at = ?,
                   status = ?, run_status = ?, summary_json = ?
               WHERE run_id = ?""",
            (
                _iso(), _iso(), _iso(), status, status,
                json.dumps(dict(summary), ensure_ascii=False, default=str), run_id,
            ),
        )
