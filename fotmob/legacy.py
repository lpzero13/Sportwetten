"""Read-only adapter for the historic ``sniper_football.db`` FotMob store.

The old collector stored flattened statistics in ``matches.data_json`` and
kept a separate set of 60-minute feature columns.  V0.5.3 deliberately keeps
that source immutable and translates it into the current FotMob history
contract with explicit provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .history_models import FotMobMatchIndexRecord, historical_row_from_match
from .matching import normalize_name, normalize_team_name
from .models import FotMobMatch, FotMobStats


LEGACY_DB_DEFAULT = Path(
    r"C:\Programmieren\Fussball\Daten Sammler\AntiGrav\backend\data\sniper_football.db"
)
LEGACY_HT_STATS = {
    "xg": "expected_goals_(xg)",
    "shots": "total_shots",
    "shots_on_target": "shots_on_target",
    "big_chances": "big_chances",
    "corners": "corners",
}
LEGACY_STATS_SUFFIXES = {
    "xg": "expected_goals_(xg)",
    "shots": "total_shots",
    "shots_on_target": "shots_on_target",
    "big_chances": "big_chances",
    "corners": "corners",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value).replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _repair_legacy_text(value: Any) -> str | None:
    """Repair the known latin-1 replacement used by the old SQLite export."""

    text = _text(value)
    if text is None:
        return None
    replacements = {
        "M�nchengladbach": "Mönchengladbach",
        "M�nchen": "München",
        "K�ln": "Köln",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _mapping(row: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def _decode_data_json(row: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    raw = row["data_json"] if "data_json" in row.keys() else None
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        decoded = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _date_value(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for parser in (datetime.fromisoformat, date.fromisoformat):
        try:
            parsed = parser(text.replace("Z", "+00:00"))
            return parsed.date() if isinstance(parsed, datetime) else parsed
        except (TypeError, ValueError):
            continue
    return None


def season_for_date(value: Any) -> tuple[str, str] | None:
    """Return ``(YYYY/YYYY+1, YYYY/YY)`` for a Bundesliga calendar date."""

    parsed = _date_value(value)
    if parsed is None:
        return None
    start = parsed.year if parsed.month >= 8 else parsed.year - 1
    end = start + 1
    return f"{start}/{end}", f"{start}/{str(end)[-2:]}"


def _kickoff(value: Any) -> str | None:
    parsed = _date_value(value)
    return f"{parsed.isoformat()}T00:00:00+00:00" if parsed else None


def _team_equal(left: Any, right: Any) -> bool:
    left_norm = normalize_team_name(_repair_legacy_text(left))
    right_norm = normalize_team_name(right)
    if left_norm == right_norm:
        return True
    aliases = {
        "fc augsburg": "augsburg",
        "sc freiburg": "freiburg",
        "borussia monchengladbach": "borussia monchengladbach",
        "borussia m gladbach": "borussia monchengladbach",
        "hamburger sv": "hamburger sv",
        "1 fc koln": "fc koln",
    }
    return aliases.get(left_norm, left_norm) == aliases.get(right_norm, right_norm)


def _legacy_stat(data: Mapping[str, Any], period: str, stat: str, side: str) -> float | None:
    label = LEGACY_STATS_SUFFIXES[stat]
    key = f"stats_{period}_top_stats_{label}_{side}"
    return _number(data.get(key))


def _stats_from_legacy(data: Mapping[str, Any], period: str) -> FotMobStats:
    values: dict[str, Any] = {}
    for stat in LEGACY_STATS_SUFFIXES:
        values[f"{stat}_home"] = _legacy_stat(data, period, stat, "home")
        values[f"{stat}_away"] = _legacy_stat(data, period, stat, "away")
    possession_home = _number(data.get(f"stats_{period}_top_stats_ball_possession_home"))
    possession_away = _number(data.get(f"stats_{period}_top_stats_ball_possession_away"))
    values["possession_home"] = possession_home
    values["possession_away"] = possession_away
    for stat in ("yellow_cards", "red_cards"):
        values[f"{stat}_home"] = _number(data.get(f"stats_{period}_top_stats_{stat}_{'home'}"))
        values[f"{stat}_away"] = _number(data.get(f"stats_{period}_top_stats_{stat}_{'away'}"))
    # The old collector uses discipline-prefixed keys for cards.
    for stat in ("yellow_cards", "red_cards"):
        for side in ("home", "away"):
            if values[f"{stat}_{side}"] is None:
                values[f"{stat}_{side}"] = _number(
                    data.get(f"stats_{period}_discipline_{stat}_{side}")
                )
    known = {
        f"stats_{period}_top_stats_{label}_{side}"
        for label in LEGACY_STATS_SUFFIXES.values()
        for side in ("home", "away")
    }
    known.update(
        f"stats_{period}_top_stats_ball_possession_{side}" for side in ("home", "away")
    )
    extras = {key: value for key, value in data.items() if key.startswith(f"stats_{period}_") and key not in known}
    values["extra_stats"] = extras
    return FotMobStats(**values)


def _coverage(values: Iterable[Any]) -> int:
    return sum(
        bool(value) if isinstance(value, bool) else value is not None
        for value in values
    )


def _period_coverage(rows: Iterable[Mapping[str, Any] | sqlite3.Row], period: str) -> dict[str, int]:
    data_rows = [
        _decode_data_json(row) if not isinstance(row, Mapping) or "data_json" in row.keys() else dict(row)
        for row in rows
    ]
    return {
        stat: _coverage(
            _legacy_stat(data, period, stat, "home") is not None
            and _legacy_stat(data, period, stat, "away") is not None
            for data in data_rows
        )
        for stat in LEGACY_STATS_SUFFIXES
    }


def _m60_coverage(rows: Iterable[Mapping[str, Any] | sqlite3.Row]) -> dict[str, int]:
    row_list = list(rows)
    return {
        field: _coverage(row[column] is not None for row in row_list)
        for field, column in {
            "score": "score_home_60",
            "xg": "xg_home_60",
            "shots": "shots_total_home_60",
            "shots_on_target": "shots_on_target_home_60",
            "corners": "corners_home_ht",
        }.items()
    }


class LegacyFotMobReader:
    """Read the legacy SQLite database through a read-only SQLite URI."""

    def __init__(self, path: Path | str = LEGACY_DB_DEFAULT) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Legacy FotMob database not found: {self.path}")
        self.connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        table = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='matches'"
        ).fetchone()
        if table is None:
            self.close()
            raise ValueError(f"Legacy database has no matches table: {self.path}")

    def close(self) -> None:
        if getattr(self, "connection", None) is not None:
            self.connection.close()

    def __enter__(self) -> "LegacyFotMobReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def rows(self, league_id: int | str | None = None) -> list[sqlite3.Row]:
        if league_id is None:
            query = "SELECT * FROM matches ORDER BY date, match_id"
            params: tuple[Any, ...] = ()
        else:
            query = "SELECT * FROM matches WHERE league_id = ? ORDER BY date, match_id"
            params = (int(league_id),)
        return list(self.connection.execute(query, params).fetchall())

    def inventory(self, league_id: int | str | None = None) -> dict[str, Any]:
        rows = self.rows()
        by_league: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            by_league[str(row["league_id"])].append(row)
        league_summaries: list[dict[str, Any]] = []
        for current_id, current_rows in sorted(by_league.items(), key=lambda item: int(item[0])):
            dates = [parsed for parsed in (_date_value(row["date"]) for row in current_rows) if parsed]
            names = sorted({str(row["league_name"] or "") for row in current_rows})
            league_summaries.append(
                {
                    "league_id": current_id,
                    "league_names": names,
                    "matches": len(current_rows),
                    "min_date": min(dates).isoformat() if dates else None,
                    "max_date": max(dates).isoformat() if dates else None,
                    "ht_score_coverage": _coverage(
                        row["ht_score_home"] is not None and row["ht_score_away"] is not None
                        for row in current_rows
                    ),
                    "ft_score_coverage": _coverage(
                        row["score_home"] is not None and row["score_away"] is not None
                        for row in current_rows
                    ),
                    "ht_core_coverage": _period_coverage(current_rows, "1st"),
                    "full_core_coverage": _period_coverage(current_rows, "all"),
                    "m60_coverage": _m60_coverage(current_rows),
                    "fotmob_match_id_coverage": _coverage(
                        row["match_id"] is not None for row in current_rows
                    ),
                }
            )

        selected: dict[str, Any] | None = None
        if league_id is not None:
            target_rows = by_league.get(str(league_id), [])
            ht_fields = _period_coverage(target_rows, "1st")
            full_fields = _period_coverage(target_rows, "all")
            m60_fields = _m60_coverage(target_rows)
            seasons: dict[str, int] = defaultdict(int)
            for row in target_rows:
                season = season_for_date(row["date"])
                if season:
                    seasons[season[0]] += 1
            selected = {
                "league_id": str(league_id),
                "matches": len(target_rows),
                "min_date": min((_date_value(row["date"]) for row in target_rows if _date_value(row["date"])), default=None),
                "max_date": max((_date_value(row["date"]) for row in target_rows if _date_value(row["date"])), default=None),
                "ht_core_coverage": ht_fields,
                "full_core_coverage": full_fields,
                "m60_coverage": m60_fields,
                "season_counts": dict(sorted(seasons.items())),
                "complete_ht_core": sum(
                    all(
                        _legacy_stat(_decode_data_json(row), "1st", stat, "home") is not None
                        and _legacy_stat(_decode_data_json(row), "1st", stat, "away") is not None
                        for stat in LEGACY_STATS_SUFFIXES
                    )
                    for row in target_rows
                ),
            }
            if selected["min_date"]:
                selected["min_date"] = selected["min_date"].isoformat()
            if selected["max_date"]:
                selected["max_date"] = selected["max_date"].isoformat()
        return {
            "database": str(self.path),
            "total_rows": len(rows),
            "leagues": league_summaries,
            "selected_league": selected,
        }

    def to_index_record(
        self,
        row: Mapping[str, Any] | sqlite3.Row,
        *,
        season_id: str | None = None,
        season_label: str | None = None,
    ) -> FotMobMatchIndexRecord:
        season = season_for_date(row["date"])
        long_label = season[0] if season else (season_label or "unknown")
        short_label = season[1] if season else (season_label or "unknown")
        data = _decode_data_json(row)
        provenance = {
            "source_database": str(self.path),
            "match_id_column": "matches.match_id",
            "stats": "matches.data_json:stats_1st_top_stats_* + stats_all_top_stats_*",
            "m60": "matches.score_*_60 / *_60 and *_ht columns",
        }
        return FotMobMatchIndexRecord(
            provider_match_id=str(row["match_id"]),
            league_id=str(row["league_id"]),
            season_id=season_id or f"legacy-{long_label.replace('/', '-')}",
            season_label=short_label,
            kickoff_at=_kickoff(row["date"]),
            home_team_id=str(row["home_team_id"]) if row["home_team_id"] is not None else None,
            home_team_name=_repair_legacy_text(row["home_team"]) or "",
            away_team_id=str(row["away_team_id"]) if row["away_team_id"] is not None else None,
            away_team_name=_repair_legacy_text(row["away_team"]) or "",
            round_name=None,
            match_status="finished",
            league_name="Bundesliga",
            country="GER",
            first_seen_at=_text(row["last_updated"]) or datetime.now(timezone.utc).isoformat(),
            provider="FOTMOB",
            source_type="LEGACY_IMPORT",
            source_context="LEGACY_SQLITE",
            stats_period="FIRST_HALF_AND_FULL_MATCH",
            captured_live=False,
            field_provenance=provenance | {"data_keys": len(data)},
        )

    def to_historical_row(
        self,
        row: Mapping[str, Any] | sqlite3.Row,
        *,
        season_id: str | None = None,
        season_label: str | None = None,
        raw_payload_path: str | None = None,
    ) -> dict[str, Any]:
        index = self.to_index_record(row, season_id=season_id, season_label=season_label)
        data = _decode_data_json(row)
        ht_stats = _stats_from_legacy(data, "1st")
        full_stats = _stats_from_legacy(data, "all")
        match = FotMobMatch(
            provider_match_id=index.provider_match_id,
            kickoff_at=index.kickoff_at,
            competition_id=index.league_id,
            competition_name="Bundesliga",
            competition_country="GER",
            home_team=index.home_team_name,
            away_team=index.away_team_name,
            home_team_id=index.home_team_id,
            away_team_id=index.away_team_id,
            status="finished",
            score_home=_integer(row["score_home"]),
            score_away=_integer(row["score_away"]),
            ht_score_home=_integer(row["ht_score_home"]),
            ht_score_away=_integer(row["ht_score_away"]),
            stats=full_stats,
            ht_stats=ht_stats,
            ht_stats_available=ht_stats.has_any_value(),
            extra_data={"legacy_match_id": row["match_id"]},
        )
        core_provenance = {
            "ht_home": "matches.ht_score_home",
            "ht_away": "matches.ht_score_away",
            "ft_home": "matches.score_home",
            "ft_away": "matches.score_away",
            "ht_xg_home": "matches.data_json.stats_1st_top_stats_expected_goals_(xg)_home",
            "ht_xg_away": "matches.data_json.stats_1st_top_stats_expected_goals_(xg)_away",
            "ht_shots_home": "matches.data_json.stats_1st_top_stats_total_shots_home",
            "ht_shots_away": "matches.data_json.stats_1st_top_stats_total_shots_away",
            "ht_shots_on_target_home": "matches.data_json.stats_1st_top_stats_shots_on_target_home",
            "ht_shots_on_target_away": "matches.data_json.stats_1st_top_stats_shots_on_target_away",
            "ht_big_chances_home": "matches.data_json.stats_1st_top_stats_big_chances_home",
            "ht_big_chances_away": "matches.data_json.stats_1st_top_stats_big_chances_away",
            "ht_corners_home": "matches.data_json.stats_1st_top_stats_corners_home",
            "ht_corners_away": "matches.data_json.stats_1st_top_stats_corners_away",
            "m60": "legacy 60-minute feature columns; not FirstHalf fields",
        }
        result = historical_row_from_match(
            index,
            match,
            fetched_at=_text(row["last_updated"]) or datetime.now(timezone.utc).isoformat(),
            raw_payload_path=raw_payload_path,
            source_type="LEGACY_IMPORT",
            source_context="LEGACY_SQLITE",
            stats_period="FIRST_HALF_AND_FULL_MATCH",
            captured_live=False,
            field_provenance=core_provenance,
        )
        # Preserve the old 60-minute feature vector separately.  In
        # particular, goal_after_60 is deliberately not used for the target.
        m60_map = {
            "m60_score_home": "score_home_60",
            "m60_score_away": "score_away_60",
            "m60_xg_home": "xg_home_60",
            "m60_xg_away": "xg_away_60",
            "m60_shots_home": "shots_total_home_60",
            "m60_shots_away": "shots_total_away_60",
            "m60_shots_on_target_home": "shots_on_target_home_60",
            "m60_shots_on_target_away": "shots_on_target_away_60",
            "m60_corners_home": "corners_home_ht",
            "m60_corners_away": "corners_away_ht",
            "m60_yellow_cards_home": "yellow_cards_home_60",
            "m60_yellow_cards_away": "yellow_cards_away_60",
            "m60_red_cards_home": "red_card_home_60",
            "m60_red_cards_away": "red_card_away_60",
        }
        for destination, source in m60_map.items():
            result[destination] = row[source]
        result["payload_hash"] = hashlib.sha256(
            json.dumps(_mapping(row), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        result["source_type"] = "LEGACY_IMPORT"
        result["source_context"] = "LEGACY_SQLITE"
        result["stats_period"] = "FIRST_HALF_AND_FULL_MATCH"
        result["field_provenance_json"] = core_provenance
        return result

    def audit_sample(self, league_id: int | str = 54, count: int = 20) -> list[sqlite3.Row]:
        rows = self.rows(league_id)
        if not rows:
            return []
        groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            season = season_for_date(row["date"])
            groups[season[0] if season else "unknown"].append(row)
        ordered_groups = [groups[key] for key in sorted(groups)]
        selected: list[sqlite3.Row] = []
        # Spread the fixed audit budget across all seasons present, then fill
        # remaining slots deterministically by chronological order.
        for group in ordered_groups:
            index = min(len(group) - 1, int((len(group) - 1) / 2))
            selected.append(group[index])
        if len(selected) > count:
            selected = selected[:count]
        selected_ids = {row["match_id"] for row in selected}
        remaining = [row for row in rows if row["match_id"] not in selected_ids]
        while len(selected) < count and remaining:
            slot = len(selected)
            index = min(len(remaining) - 1, int(slot * len(remaining) / max(1, count - len(selected))))
            selected.append(remaining.pop(index))
        return sorted(selected[:count], key=lambda row: (str(row["date"]), int(row["match_id"])))


def compare_legacy_row_to_match(
    row: Mapping[str, Any] | sqlite3.Row,
    match: FotMobMatch,
) -> dict[str, Any]:
    """Compare a legacy row and a fresh public match field by field."""

    data = _decode_data_json(row)

    def field(name: str, legacy: Any, current: Any, *, tolerance: float = 0.0, team: bool = False) -> dict[str, Any]:
        if legacy is None:
            classification = "LEGACY_MISSING"
        elif current is None:
            classification = "CURRENT_MISSING"
        elif team:
            classification = "MATCH" if _team_equal(legacy, current) else "MISMATCH"
        elif tolerance and isinstance(legacy, (int, float)) and isinstance(current, (int, float)):
            classification = "MATCH" if abs(float(legacy) - float(current)) <= tolerance else "MISMATCH"
        else:
            classification = "MATCH" if legacy == current else "MISMATCH"
        return {"classification": classification, "legacy": legacy, "current": current}

    fields = {
        "home_team": field("home_team", _repair_legacy_text(row["home_team"]), match.home_team, team=True),
        "away_team": field("away_team", _repair_legacy_text(row["away_team"]), match.away_team, team=True),
        "ht_score_home": field("ht_score_home", _integer(row["ht_score_home"]), match.ht_score_home),
        "ht_score_away": field("ht_score_away", _integer(row["ht_score_away"]), match.ht_score_away),
        "ft_score_home": field("ft_score_home", _integer(row["score_home"]), match.score_home),
        "ft_score_away": field("ft_score_away", _integer(row["score_away"]), match.score_away),
    }
    for stat in LEGACY_STATS_SUFFIXES:
        fields[f"ht_{stat}_home"] = field(
            f"ht_{stat}_home",
            _legacy_stat(data, "1st", stat, "home"),
            getattr(match.ht_stats, f"{stat}_home", None) if match.ht_stats else None,
            tolerance=0.03 if stat == "xg" else 0.0,
        )
        fields[f"ht_{stat}_away"] = field(
            f"ht_{stat}_away",
            _legacy_stat(data, "1st", stat, "away"),
            getattr(match.ht_stats, f"{stat}_away", None) if match.ht_stats else None,
            tolerance=0.03 if stat == "xg" else 0.0,
        )
    classifications = [str(item["classification"]) for item in fields.values()]
    return {
        "legacy_match_id": str(row["match_id"]),
        "fields": fields,
        "match": sum(item == "MATCH" for item in classifications),
        "mismatch": sum(item == "MISMATCH" for item in classifications),
        "legacy_missing": sum(item == "LEGACY_MISSING" for item in classifications),
        "current_missing": sum(item == "CURRENT_MISSING" for item in classifications),
        "status": "MATCH" if all(item == "MATCH" for item in classifications) else "PARTIAL",
    }


__all__ = [
    "LEGACY_DB_DEFAULT",
    "LEGACY_HT_STATS",
    "LegacyFotMobReader",
    "compare_legacy_row_to_match",
    "season_for_date",
]
