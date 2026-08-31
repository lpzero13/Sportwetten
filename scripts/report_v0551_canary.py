#!/usr/bin/env python3
"""Build the V0.5.5.1 five-day all-leagues validation report.

The report intentionally reads the small SQLite catalog first and then only
the canonical Parquet files belonging to the selected daily-feed fixtures.
It never scans or rewrites the historical archive outside the canary scope.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pyarrow.parquet as parquet
except ImportError:  # pragma: no cover - dependency is declared by the app
    parquet = None

from config import Settings
from fotmob.canonical import FotMobCanonicalArchive
from fotmob.history_discovery import is_finished_index_record
from fotmob.history_storage import FotMobHistoryStore
from storage.database import Database


DEFAULT_FROM = "2026-08-26"
DEFAULT_TO = "2026-08-30"

HT_METRICS: dict[str, tuple[str, ...]] = {
    "HT score": ("ht_score_home", "ht_score_away"),
    "HT xG": ("ht_xg_home", "ht_xg_away"),
    "HT shots": ("ht_shots_home", "ht_shots_away"),
    "HT shots on target": ("ht_shots_on_target_home", "ht_shots_on_target_away"),
    "HT big chances": ("ht_big_chances_home", "ht_big_chances_away"),
    "HT corners": ("ht_corners_home", "ht_corners_away"),
    "HT possession": ("ht_possession_home", "ht_possession_away"),
    "HT yellow cards": ("ht_yellow_cards_home", "ht_yellow_cards_away"),
    "HT red cards": ("ht_red_cards_home", "ht_red_cards_away"),
    "HT fouls": ("ht_fouls_home", "ht_fouls_away"),
    "HT offsides": ("ht_offsides_home", "ht_offsides_away"),
    "HT goalkeeper saves": ("ht_goalkeeper_saves_home", "ht_goalkeeper_saves_away"),
    "HT passes": ("ht_passes_home", "ht_passes_away"),
    "HT accurate passes": ("ht_accurate_passes_home", "ht_accurate_passes_away"),
    "HT shots inside box": ("ht_shots_inside_box_home", "ht_shots_inside_box_away"),
    "HT shots outside box": ("ht_shots_outside_box_home", "ht_shots_outside_box_away"),
    "HT touches in box": ("ht_touches_in_box_home", "ht_touches_in_box_away"),
    "HT expected threat": ("ht_expected_threat_home", "ht_expected_threat_away"),
}

SHOT_FIELDS = (
    "minute", "added_time", "xg", "xgot", "outcome", "shot_type", "situation",
    "body_part", "x", "y", "team_id", "is_home", "player_id", "player_name", "period",
)
EVENT_FIELDS = (
    "event_type", "period", "minute", "added_time", "team_id", "is_home", "player_id",
    "player_name", "score_home_after", "score_away_after",
)


def _dict_row(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping) or hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return dict(row)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fmt_time(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value else "n/a"


def _pct(available: int, eligible: int) -> str:
    return f"{available / eligible * 100:.1f}%" if eligible else "n/a"


def _coverage_label(available: int, eligible: int) -> str:
    if not available:
        return "NONE"
    ratio = available / eligible if eligible else 0.0
    if ratio >= 0.80:
        return "HIGH"
    if ratio >= 0.50:
        return "MEDIUM"
    return "LOW"


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    header_values = list(headers)
    output = [
        "| " + " | ".join(_md_cell(item) for item in header_values) + " |",
        "| " + " | ".join("---" for _ in header_values) + " |",
    ]
    output.extend(
        "| " + " | ".join(_md_cell(item) for item in row) + " |"
        for row in rows
    )
    return output


def _read_parquet(path: Path | str | None) -> list[dict[str, Any]]:
    if parquet is None or not path:
        return []
    candidate = Path(str(path))
    if not candidate.exists():
        return []
    try:
        return [dict(row) for row in parquet.read_table(candidate).to_pylist()]
    except (OSError, ValueError, TypeError):
        return []


def _sibling_dataset(core_path: Path, dataset: str) -> Path | None:
    parts = list(core_path.parts)
    try:
        index = parts.index("match_core")
    except ValueError:
        return None
    parts[index] = dataset
    return Path(*parts)


def _load_match_rows(connection: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 700):
        chunk = ids[start:start + 700]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"SELECT * FROM fotmob_match_index WHERE fotmob_match_id IN ({placeholders})",
            chunk,
        ).fetchall()
        result.update({str(row["fotmob_match_id"]): _dict_row(row) for row in rows})
    return result


def _extra_available(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, Mapping):
        return any(_extra_available(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_extra_available(item) for item in value)
    return True


def _extra_metrics(core_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    samples: dict[str, Any] = {}
    for row in core_rows:
        raw = row.get("ht_extra_stats_json")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        if not isinstance(parsed, Mapping):
            continue
        for name, value in parsed.items():
            key = str(name)
            if _extra_available(value):
                counts[key] += 1
                samples.setdefault(key, value)
    eligible = len(core_rows)
    return [
        {
            "provider_metric_name": name,
            "matches_available": count,
            "eligible_matches": eligible,
            "coverage": _pct(count, eligible),
            "label": _coverage_label(count, eligible),
            "sample": samples.get(name),
        }
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]


def _field_coverage(rows: list[dict[str, Any]], fields: Iterable[str]) -> list[tuple[str, int, int, str]]:
    total = len(rows)
    return [
        (field, sum(row.get(field) is not None for row in rows), total, _pct(sum(row.get(field) is not None for row in rows), total))
        for field in fields
    ]


def _event_categories(events: list[dict[str, Any]]) -> list[tuple[str, int]]:
    category_counts = Counter()
    for row in events:
        event_type = str(row.get("event_type") or "UNKNOWN").upper()
        if event_type in {"GOAL", "PENALTY_GOAL"}:
            category_counts["Goals"] += 1
        if event_type == "OWN_GOAL":
            category_counts["Own goals"] += 1
        if "PENALTY" in event_type:
            category_counts["Penalties"] += 1
        if event_type in {"YELLOW_CARD", "SECOND_YELLOW"}:
            category_counts["Yellow cards"] += 1
        if event_type == "RED_CARD":
            category_counts["Red cards"] += 1
        if event_type == "SUBSTITUTION":
            category_counts["Substitutions"] += 1
        if event_type == "VAR":
            category_counts["VAR"] += 1
    return sorted(category_counts.items())


def _run_report(
    root: Path,
    start_date: str,
    end_date: str,
    output: Path,
    access_metrics: Mapping[str, Any] | None = None,
    execution_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings = Settings.from_env(root)
    database = Database(settings.database_path)
    # The constructor is intentionally used so a pre-V0.5.5.1 database gets
    # the additive feed-counter migration before it is queried.
    store = FotMobHistoryStore(database, settings.fotmob_archive_path)
    try:
        index_rows = [
            _dict_row(row)
            for row in store.daily_index(
                start_date=start_date,
                end_date=end_date,
                limit=1_000_000,
                order_by="observation_date",
                ascending=True,
            )
        ]
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        days = [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]
        day_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in index_rows:
            day_rows[str(row["observation_date"])].append(row)

        ids = sorted({str(row["fotmob_match_id"]) for row in index_rows})
        match_rows = _load_match_rows(database.connection, ids)
        archive = FotMobCanonicalArchive(settings.fotmob_archive_path)
        core_rows: list[dict[str, Any]] = []
        core_by_id: dict[str, dict[str, Any]] = {}
        archive_errors: list[str] = []
        shot_rows: list[dict[str, Any]] = []
        event_rows: list[dict[str, Any]] = []
        archive_paths: dict[str, str] = {}
        for match_id in ids:
            row = match_rows.get(match_id, {})
            path_value = next(
                (
                    item.get("canonical_archive_path")
                    for item in index_rows
                    if str(item["fotmob_match_id"]) == match_id and item.get("canonical_archive_path")
                ),
                None,
            )
            if not path_value:
                continue
            path = Path(str(path_value))
            if not path.exists():
                archive_errors.append(f"{match_id}: Datei fehlt: {path}")
                continue
            core = archive.read_match_core(path)
            if not core:
                archive_errors.append(f"{match_id}: Match-Core nicht lesbar: {path}")
                continue
            core["_daily_country_name"] = row.get("country_name")
            core["_daily_league_name"] = row.get("league_name")
            core["_daily_observation_date"] = row.get("observation_date")
            core_rows.append(core)
            core_by_id[match_id] = core
            archive_paths[match_id] = str(path)
            for dataset, target in (("shots", shot_rows), ("events", event_rows)):
                sibling = _sibling_dataset(path, dataset)
                if sibling:
                    target.extend(_read_parquet(sibling))

        unique_shots: dict[tuple[str, str], dict[str, Any]] = {}
        for row in shot_rows:
            unique_shots[(str(row.get("fotmob_match_id")), str(row.get("shot_id")))] = row
        shot_rows = list(unique_shots.values())
        unique_events: dict[tuple[str, str], dict[str, Any]] = {}
        for row in event_rows:
            unique_events[(str(row.get("fotmob_match_id")), str(row.get("event_id")))] = row
        event_rows = list(unique_events.values())

        runs = {
            str(row["observation_date"]): _dict_row(row)
            for row in store.daily_load_runs(start_date=start_date, end_date=end_date, limit=100)
            if str(row["league_id"]) == "ALL"
        }
        unique_statuses = Counter(
            str(match_rows.get(match_id, {}).get("detail_status") or "NOT_FETCHED")
            for match_id in ids
        )
        skipped_ids = {
            match_id for match_id in ids
            if match_rows.get(match_id, {}).get("detail_status") == "SKIPPED_NO_HALFTIME"
        }
        finished_ids = {
            match_id for match_id in ids
            if is_finished_index_record(match_rows.get(match_id, {}))
        }
        fetched_ids = {
            match_id for match_id in ids
            if match_rows.get(match_id, {}).get("detail_status") in {"FETCHED", "PARTIAL"}
        }
        attempted_ids = {
            match_id for match_id in ids
            if match_rows.get(match_id, {}).get("last_checked_at")
            or int(match_rows.get(match_id, {}).get("attempt_count") or 0) > 0
        }
        failed_ids = {
            match_id for match_id in ids
            if match_rows.get(match_id, {}).get("detail_status") == "FAILED"
        }
        archive_rule_violations = [
            f"{match_id}: SKIPPED_NO_HALFTIME besitzt Canonical-Datei"
            for match_id in skipped_ids
            if match_id in archive_paths
        ]
        archive_rule_violations.extend(
            f"{match_id}: FETCHED/PARTIAL ohne Canonical-Datei"
            for match_id in fetched_ids
            if match_id not in archive_paths
        )
        no_ht_quality_violations = [
            f"{match_id}: Status SKIPPED_NO_HALFTIME, data_quality={match_rows.get(match_id, {}).get('data_quality')}"
            for match_id in skipped_ids
            if match_rows.get(match_id, {}).get("data_quality") != "NO_HALFTIME"
        ]

        first_half_coverage = []
        for label, fields in HT_METRICS.items():
            available = sum(all(core.get(field) is not None for field in fields) for core in core_rows)
            first_half_coverage.append((label, available, len(core_rows), _pct(available, len(core_rows)), _coverage_label(available, len(core_rows))))

        league_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for core in core_rows:
            key = (
                str(core.get("_daily_country_name") or core.get("country_name") or "UNKNOWN"),
                str(core.get("_daily_league_name") or core.get("league_name") or "UNKNOWN"),
            )
            league_groups[key].append(core)
        league_coverage = []
        for (country, league), rows in sorted(league_groups.items()):
            if len(rows) < 10:
                continue
            score_available = sum(all(row.get(field) is not None for field in HT_METRICS["HT score"]) for row in rows)
            xg_available = sum(all(row.get(field) is not None for field in HT_METRICS["HT xG"]) for row in rows)
            shots_available = sum(all(row.get(field) is not None for field in HT_METRICS["HT shots"]) for row in rows)
            league_coverage.append((country, league, len(rows), _pct(score_available, len(rows)), _pct(xg_available, len(rows)), _pct(shots_available, len(rows))))

        extra = _extra_metrics(core_rows)
        shot_coverage = _field_coverage(shot_rows, SHOT_FIELDS)
        event_coverage = _field_coverage(event_rows, EVENT_FIELDS)
        event_types = Counter(str(row.get("event_type") or "UNKNOWN") for row in event_rows)
        event_categories = _event_categories(event_rows)

        timestamps: list[datetime] = []
        for match_id in attempted_ids:
            for key in ("last_attempt_at", "last_checked_at"):
                parsed = _parse_time(match_rows.get(match_id, {}).get(key))
                if parsed:
                    timestamps.append(parsed)
        started = min(timestamps) if timestamps else None
        finished = max(timestamps) if timestamps else None
        elapsed_seconds = (finished - started).total_seconds() if started and finished else None

        feed_groups = sum(int(row.get("feed_group_count") or 0) for row in runs.values())
        feed_entries = sum(int(row.get("feed_entry_count") or 0) for row in runs.values())
        feed_unique = sum(int(row.get("feed_unique_count") or row.get("fixture_count") or 0) for row in runs.values())
        next_day = sum(int(row.get("next_day_count") or 0) for row in runs.values())
        duplicates = sum(int(row.get("duplicates_removed_count") or 0) for row in runs.values())
        distinct_leagues = sorted({str(row.get("league_name") or row.get("league_id") or "UNKNOWN") for row in index_rows})
        distinct_countries = sorted({str(row.get("country_name") or row.get("country_code") or "UNKNOWN") for row in index_rows})
        distinct_seasons = sorted({str(row.get("season_label") or "UNKNOWN") for row in index_rows})
        missing_run_days = [day for day in days if day not in runs]
        incomplete_run_days = [day for day in days if day in runs and str(runs[day].get("status")) not in {"COMPLETE", "PASS"}]
        index_mismatch_days = [
            day for day in days
            if day in runs
            and int(runs[day].get("feed_unique_count") or runs[day].get("fixture_count") or 0) != len(day_rows.get(day, []))
        ]

        structural_errors = list(missing_run_days) + list(incomplete_run_days)
        if archive_errors:
            structural_errors.append(f"{len(archive_errors)} archive read errors")
        structural_errors.extend(index_mismatch_days)
        structural_errors.extend(archive_rule_violations)
        structural_errors.extend(no_ht_quality_violations)
        pending_ids = set(ids) - attempted_ids
        five_day_gate = "PASS" if not structural_errors and not failed_ids and not pending_ids else "PARTIAL"
        ready_gate = "PASS" if five_day_gate == "PASS" and core_rows and first_half_coverage[0][1] else "NOT_READY"

        recommendations: list[str] = []
        if missing_run_days:
            recommendations.append(f"Fehlende Daily-Run-Zeilen für: {', '.join(missing_run_days)}.")
        if incomplete_run_days:
            recommendations.append(f"Unvollständige Daily-Runs prüfen: {', '.join(incomplete_run_days)}.")
        if pending_ids:
            recommendations.append(f"{len(pending_ids)} Fixtures haben keinen terminalen Detailstatus.")
        if failed_ids:
            recommendations.append(f"{len(failed_ids)} Detailrequests sind FAILED; Fehlertexte und Retry-Limit prüfen.")
        if archive_rule_violations:
            recommendations.append("Canonical-Archivregel für FirstHalf-Skips/FETCHED-Details verletzt; Archivindex prüfen.")
        low_metrics = [label for label, _, _, _, label_name in first_half_coverage if label_name == "LOW"]
        if low_metrics:
            recommendations.append("Niedrige HT-Abdeckung dokumentieren oder für V0.6 als nullable Features behandeln: " + ", ".join(low_metrics[:5]) + ".")
        unavailable_metrics = [label for label, _, _, _, label_name in first_half_coverage if label_name == "NONE"]
        if unavailable_metrics:
            recommendations.append("HT-Metriken ohne Providerwerte nicht imputieren; nullable halten: " + ", ".join(unavailable_metrics[:5]) + ".")
        missing_shot_fields = [field for field, available, _, _ in shot_coverage if available == 0]
        if missing_shot_fields:
            recommendations.append("Shotmap-Felder ohne Werte als nullable behandeln: " + ", ".join(missing_shot_fields[:6]) + ".")
        missing_event_fields = [field for field, available, _, _ in event_coverage if available == 0]
        if missing_event_fields:
            recommendations.append("Event-Felder ohne Werte nicht imputieren; Providerlücke dokumentieren: " + ", ".join(missing_event_fields[:6]) + ".")
        if not access_metrics:
            recommendations.append("Für den nächsten Lauf FotMobClient-access.metrics als kompakten JSON-Sidecar archivieren, damit 429-/Retry-/Parse-Zähler exakt statt nur statusbasiert vorliegen.")
        if not recommendations:
            recommendations.append("Keine strukturellen Blocker aus dem Fünf-Tage-Canary abgeleitet; V0.6-Dataset kann auf dieser Archivbasis geplant werden.")
        recommendations = recommendations[:10]
        access = dict(access_metrics or {})
        execution = dict(execution_summary or {})
        access_available = bool(access)
        detail_successes = (
            int(execution.get("fetched", 0) or 0)
            + int(execution.get("partial", 0) or 0)
            + int(execution.get("skipped_no_halftime", 0) or 0)
        )
        index_successes = len(runs) + 1 if not missing_run_days else 0

        report: list[str] = [
            "# V0.5.5.1 – Five-Day All-Leagues Real Data Canary Report",
            "",
            f"- Zeitraum: **{start_date} bis {end_date}**, inklusiv",
            "- Zeitzone der Daily-Requests: `Europe/Berlin`",
            "- Scope: **ALL_LEAGUES**, kein `--league`-Filter",
            f"- Detail-Worker: **{getattr(settings, 'fotmob_history_workers', 'n/a')}**; globaler Request-Limiter: **{getattr(settings, 'fotmob_history_requests_per_second', 'n/a')} req/s**",
            f"- Persistierter Detail-Zeitbereich: `{_fmt_time(started)}` bis `{_fmt_time(finished)}`; elapsed: `{elapsed_seconds:.1f}s`" if elapsed_seconds is not None else "- Persistierter Detail-Zeitbereich: nicht verfügbar",
            "",
            "## Gate",
            "",
            f"- `FIVE_DAY_CANARY`: **{five_day_gate}**",
            f"- `READY_FOR_V06_DATASET`: **{ready_gate}**",
            "- ML, Backtesting und Strategielogik wurden in diesem Milestone nicht ausgeführt.",
            "",
            "## Gesamtübersicht",
            "",
        ]
        report.extend(_table(
            ["Kennzahl", "Wert"],
            [
                ("Tagesfeed-Gruppen", feed_groups),
                ("Roh-Feed-Einträge", feed_entries),
                ("Feed-Unique-Matches (pro Tag summiert)", feed_unique),
                ("SQLite-Daily-Indexzeilen", len(index_rows)),
                ("Unique Match IDs im Bereich", len(ids)),
                ("Distinct Länder", len(distinct_countries)),
                ("Distinct Ligen", len(distinct_leagues)),
                ("Saisonlabels", ", ".join(distinct_seasons)),
                ("Next-Day-Einträge", next_day),
                ("Entfernte Feed-Duplikate", duplicates),
                ("Finished-Unique-Matches", len(finished_ids)),
                ("Detailjobs selected/requested", execution.get("requested", len(ids))),
                ("Detailrequests attempted, unique", len(attempted_ids)),
                ("Detail-Run neu FETCHED", execution.get("fetched", unique_statuses["FETCHED"])),
                ("Detailstatus FETCHED", unique_statuses["FETCHED"]),
                ("Detailstatus PARTIAL", unique_statuses["PARTIAL"]),
                ("Detail-Run Queue-Skips", execution.get("skipped", "n/a")),
                ("SKIPPED_NO_HALFTIME", len(skipped_ids)),
                ("FAILED", len(failed_ids)),
                ("Canonical Match-Core-Dateien", len(core_rows)),
                ("Archive-Lesefehler", len(archive_errors)),
            ],
        ))
        report.extend(["", f"Die Feed-Unique-Zahl ist je Tag dedupliziert und wird hier summiert. Überlappungen derselben Provider-ID zwischen benachbarten Tagesfeeds (z. B. Next-Day-Einträge) werden im Bereichs-Unique auf {len(ids)} IDs reduziert."])
        report.extend(["", "## Tagesdetails", ""])
        report.extend(_table(
            ["Datum", "Run", "Gruppen", "Roh", "Unique", "Index", "Next-Day", "Duplikate", "Detail", "Skip HZ"],
            [
                (
                    day,
                    runs.get(day, {}).get("status", "MISSING"),
                    runs.get(day, {}).get("feed_group_count", 0),
                    runs.get(day, {}).get("feed_entry_count", 0),
                    runs.get(day, {}).get("feed_unique_count", runs.get(day, {}).get("fixture_count", 0)),
                    len(day_rows.get(day, [])),
                    runs.get(day, {}).get("next_day_count", 0),
                    runs.get(day, {}).get("duplicates_removed_count", 0),
                    sum(str(row.get("detail_status") or "NOT_FETCHED") in {"FETCHED", "PARTIAL"} for row in day_rows.get(day, [])),
                    sum(str(row.get("detail_status") or "") == "SKIPPED_NO_HALFTIME" for row in day_rows.get(day, [])),
                )
                for day in days
            ],
        ))
        report.extend(["", "## Detail- und Rate-Limit-Nachweis", ""])
        report.append("Die folgenden Werte werden aus den terminalen SQLite-Detailstatus abgeleitet. Der Client hält HTTP-/Retry-Metriken nur im Prozess; wenn der CLI-Abschlussoutput nicht separat archiviert wurde, sind 200/Fehler hier eine Status-basierte Untergrenze. Für exakte Client-Zähler kann der Abschlussoutput als JSON mit --access-metrics übergeben werden.")
        report.extend([""])
        report.extend(_table(
            ["Metrik", "Wert", "Definition"],
            [
                ("Client requests gesamt", access.get("requests", "n/a") if access_available else "n/a", "FotMobClient-Zähler, inklusive Katalog/Tagesfeeds"),
                ("Daily-Index/Katalog HTTP-200 (mind.)", access.get("index_successes", index_successes) if access_available else index_successes, "1 allLeagues-Katalog + fünf erfolgreiche Daily-Feeds"),
                ("Detail-Run erfolgreiche Responses", access.get("successes", "n/a") if access_available else detail_successes, "CLI-Detailresult; Queue-Skips sind keine HTTP-Requests"),
                ("HTTP-200 mindestens gesamt", access.get("successes", "n/a") if access_available else index_successes + detail_successes, "Index/Katalog plus erfolgreiche Detail-Responses; ohne Retry-Zähler"),
                ("HTTP-Fehler", access.get("http_failures", "n/a") if access_available else execution.get("errors", len(failed_ids)), "Client-Zähler; ohne access.metrics terminale Scope-Untergrenze"),
                ("HTTP-429", access.get("rate_limit_responses", "n/a") if access_available else "n/a", "FotMobClient-Zähler"),
                ("Retries", access.get("retries", "n/a") if access_available else "n/a", "FotMobClient-Zähler"),
                ("Parse failures", access.get("parse_failures", "n/a") if access_available else "n/a", "FotMobClient-Zähler"),
                ("Nicht terminal", len(pending_ids), "NOT_FETCHED oder IN_PROGRESS nach Abschluss"),
            ],
        ))
        report.extend(["", "## FirstHalf-Abdeckung – Match-Core", ""])
        report.append("Ein Match zählt als verfügbar, wenn Home- und Away-Wert des jeweiligen Paar-Metrics vorhanden sind. `None` bleibt fehlend und wird nicht als 0 interpretiert.")
        report.extend([""])
        report.extend(_table(
            ["Metric", "verfügbar", "eligible", "Coverage", "Label"],
            first_half_coverage,
        ))
        report.extend(["", "### FirstHalf-Abdeckung je Liga (nur >=10 eligible Matches)", ""])
        report.extend(_table(
            ["Land", "Liga", "eligible", "HT score", "HT xG", "HT shots"],
            league_coverage or [("–", "Keine Liga mit >=10 eligible Matches im Canary", 0, "n/a", "n/a", "n/a")],
        ))
        report.extend(["", "### `ht_extra_stats_json` – provider_metric_name", ""])
        report.append("Die Extra-Metriken bleiben im Canonical-Schema als JSON erhalten; die Tabelle zeigt ihre beobachtete FirstHalf-Abdeckung.")
        report.extend([""])
        report.extend(_table(
            ["provider_metric_name", "verfügbar", "eligible", "Coverage", "Label", "Beispiel"],
            [
                (item["provider_metric_name"], item["matches_available"], item["eligible_matches"], item["coverage"], item["label"], json.dumps(item["sample"], ensure_ascii=False, default=str))
                for item in extra
            ] or [("–", 0, len(core_rows), "0.0%" if core_rows else "n/a", "NONE", "")],
        ))
        report.extend(["", "## Shotmap-Feldabdeckung", "", f"Shot rows im Canary-Scope: **{len(shot_rows)}**", ""])
        report.extend(_table(["Feld", "vorhanden", "gesamt", "Coverage"], shot_coverage or [("–", 0, 0, "n/a")]))
        report.extend(["", "## Events", "", f"Event rows im Canary-Scope: **{len(event_rows)}**", ""])
        report.extend(_table(["Feld", "vorhanden", "gesamt", "Coverage"], event_coverage or [("–", 0, 0, "n/a")]))
        report.extend(["", "### Event-Typen", ""])
        report.extend(_table(["event_type", "count"], sorted(event_types.items()) or [("–", 0)]))
        report.extend(["", "### Abgeleitete Event-Kategorien", ""])
        report.extend(_table(["Kategorie", "count"], event_categories or [("–", 0)]))
        report.extend(["", "## Derived-feature readiness", ""])
        report.extend(_table(
            ["Bereich", "Status", "Begründung"],
            [
                ("HT target / second-half outcome", "READY" if first_half_coverage[0][1] else "NOT_READY", "HT score plus FT score are present in canonical core where available."),
                ("HT feature matrix", "READY" if core_rows and any(item[1] for item in first_half_coverage[1:]) else "PARTIAL", "Nullable fixed metrics and provider extras are archived; coverage labels above apply."),
                ("Shot-derived features", "READY" if shot_rows else "NOT_READY", "Canonical shot rows are available only for matches with provider shotmap data."),
                ("Event-derived features", "READY" if event_rows else "NOT_READY", "Canonical event rows are available only for matches with provider timeline data."),
                ("ML/backtest/strategy", "NOT_IN_SCOPE", "Explicitly excluded from V0.5.5.1."),
            ],
        ))
        report.extend(["", "## Archive- und Qualitätschecks", ""])
        report.extend(_table(
            ["Check", "Ergebnis"],
            [
                ("Daily runs vorhanden", "PASS" if not missing_run_days else "FAIL: " + ", ".join(missing_run_days)),
                ("Feed unique == Daily-Index je Tag", "PASS" if not index_mismatch_days else "FAIL: " + ", ".join(index_mismatch_days)),
                ("Keine fehlende Canonical-Datei für FETCHED/PARTIAL", "PASS" if not archive_rule_violations else "FAIL"),
                ("SKIPPED_NO_HALFTIME => NO_HALFTIME", "PASS" if not no_ht_quality_violations else "FAIL"),
                ("Alle Detailjobs terminal", "PASS" if not pending_ids else "FAIL"),
                ("Archive-Lesbarkeit", "PASS" if not archive_errors else "FAIL"),
            ],
        ))
        if archive_errors:
            report.extend(["", "Archive-Lesefehler:", ""])
            report.extend(f"- {item}" for item in archive_errors[:20])
        report.extend(["", "## Empfehlungen", ""])
        report.extend(f"{index}. {item}" for index, item in enumerate(recommendations, start=1))
        report.extend(["", "## Reproduzierbarkeit", "", "```text", f"python scripts/fotmob_history.py dates --from-date {start_date} --to-date {end_date} --workers 10 --root .", f"python scripts/fotmob_history.py dates --from-date {start_date} --to-date {end_date} --workers 10 --index-only --root .", f"python scripts/report_v0551_canary.py --from-date {start_date} --to-date {end_date} --root . --execution-summary outputs/V0551_DETAIL_RUN_SUMMARY.json", "```", ""])

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(report), encoding="utf-8")
        return {
            "output": str(output),
            "five_day_canary": five_day_gate,
            "ready_for_v06_dataset": ready_gate,
            "days": days,
            "daily_index_rows": len(index_rows),
            "unique_matches": len(ids),
            "core_rows": len(core_rows),
            "skipped_no_halftime": len(skipped_ids),
            "failed": len(failed_ids),
            "pending": len(pending_ids),
            "shot_rows": len(shot_rows),
            "event_rows": len(event_rows),
            "access_metrics": access,
            "execution_summary": execution,
        }
    finally:
        database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--from-date", default=DEFAULT_FROM)
    parser.add_argument("--to-date", default=DEFAULT_TO)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--access-metrics",
        type=Path,
        help="optional gespeicherter FotMobClient metrics_snapshot()-JSON-Block",
    )
    parser.add_argument(
        "--execution-summary",
        type=Path,
        help="optional gespeicherte kompakte CLI-Detailzusammenfassung",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    output = (args.output or root / "outputs" / "V0551_FIVE_DAY_CANARY_REPORT.md").resolve()
    access_metrics: dict[str, Any] = {}
    if args.access_metrics:
        access_metrics = json.loads(args.access_metrics.read_text(encoding="utf-8"))
        if not isinstance(access_metrics, dict):
            raise ValueError("--access-metrics muss ein JSON-Objekt enthalten")
    execution_summary: dict[str, Any] = {}
    if args.execution_summary:
        execution_summary = json.loads(args.execution_summary.read_text(encoding="utf-8"))
        if not isinstance(execution_summary, dict):
            raise ValueError("--execution-summary muss ein JSON-Objekt enthalten")
    result = _run_report(root, args.from_date, args.to_date, output, access_metrics, execution_summary)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["five_day_canary"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
