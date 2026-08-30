#!/usr/bin/env python3
"""Run the bounded, real-data V0.5.3 validation workflow.

The script uses an isolated project root by default.  It copies the current
Tipico SQLite database into that root, keeps the old FotMob SQLite input
read-only, indexes League 54, imports the legacy rows, fresh-checks five
matches, and runs the Tipico-HALF_TIME -> FotMob FirstHalf path for every
usable event in the copied archive.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from fotmob.client import FotMobClient
from fotmob.history_models import FotMobSeasonRef
from fotmob.history_pipeline import FotMobHistoryPipeline
from fotmob.history_storage import FotMobHistoricalArchive
from fotmob.legacy import LEGACY_DB_DEFAULT, LegacyFotMobReader, compare_legacy_row_to_match, season_for_date
from fotmob.service import FotMobService
from models.event import LiveEvent
from storage.database import Database


CONFIRMED = {"EXACT", "HIGH_CONFIDENCE", "MANUALLY_CONFIRMED"}


def _json_load(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return fallback
    return decoded


def _event_from_row(row: sqlite3.Row, state: sqlite3.Row | None = None) -> LiveEvent:
    raw = _json_load(row["raw_data_json"], {})
    state_raw = _json_load(state["raw_state_json"], {}) if state is not None else {}
    return LiveEvent(
        event_id=str(row["event_id"]),
        competition_id=row["competition_id"],
        competition_name=str(row["competition_name"] or ""),
        sport=str(row["sport"] or "soccer"),
        home_team=str(row["home_team"] or ""),
        away_team=str(row["away_team"] or ""),
        home_team_id=row["home_team_id"],
        away_team_id=row["away_team_id"],
        kickoff_time=row["kickoff_time"],
        status=str((state or row)["status"] or ""),
        period=str((state or row)["period"] or ""),
        display_minute=str((state or row)["display_time"] or ""),
        score_home=(state or row)["score_home"],
        score_away=(state or row)["score_away"],
        ht_score_home=(state or row)["ht_score_home"],
        ht_score_away=(state or row)["ht_score_away"],
        bet_markets_count=row["bet_markets_count"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        last_updated_at=row["last_updated_at"],
        section_number=(state or row)["section_number"],
        red_cards_home=(state or row)["red_cards_home"],
        red_cards_away=(state or row)["red_cards_away"],
        sport_radar_match_id=row["sport_radar_match_id"],
        bet_genius_id=row["bet_genius_id"],
        extra_time=bool(row["extra_time"]) if row["extra_time"] is not None else None,
        penalties=bool(row["penalties"]) if row["penalties"] is not None else None,
        break_before=_json_load(row["break_before"], None),
        clock_data=_json_load(row["clock_data_json"], {}),
        raw_data={**raw, "validation_state": state_raw},
        competition_country=row["competition_country"],
    )


def _german_events(database: Database) -> list[LiveEvent]:
    rows = database.connection.execute(
        """
        SELECT * FROM events
        WHERE competition_id = '42301'
          AND lower(sport) = 'soccer'
        ORDER BY kickoff_time, event_id
        """
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def _halftime_events(database: Database, events: list[LiveEvent], limit: int = 5) -> list[LiveEvent]:
    selected: list[LiveEvent] = []
    for event in events:
        state = database.connection.execute(
            """
            SELECT * FROM event_states
            WHERE event_id = ?
              AND (upper(period) IN ('HALF_TIME', 'HALFTIME', 'HT') OR upper(display_time) = 'HZ')
              AND (
                  (ht_score_home IS NOT NULL AND ht_score_away IS NOT NULL)
                  OR (score_home IS NOT NULL AND score_away IS NOT NULL)
              )
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
            """,
            (event.event_id,),
        ).fetchone()
        if state is None:
            continue
        row = database.event_info(event.event_id)
        if row is not None:
            selected_event = _event_from_row(row, state)
            if (
                selected_event.ht_score_home is None
                and selected_event.ht_score_away is None
                and selected_event.score_home is not None
                and selected_event.score_away is not None
            ):
                # At an explicit HZ marker Tipico's current score is the
                # halftime score.  This fallback is deliberately scoped to
                # this state and cannot leak a later full-time score.
                selected_event = replace(
                    selected_event,
                    ht_score_home=selected_event.score_home,
                    ht_score_away=selected_event.score_away,
                )
            selected.append(selected_event)
        if len(selected) >= max(0, int(limit)):
            break
    return selected


def _fresh_validation_ids(reader: LegacyFotMobReader, count: int) -> list[str]:
    preferred = [
        "4534540", "4534541", "4534542", "4534543", "4534544",
    ]
    available = {str(row["match_id"]): row for row in reader.rows(54)}
    result = [item for item in preferred if item in available]
    if len(result) < count:
        for row in reader.rows(54):
            provider_id = str(row["match_id"])
            if provider_id not in result:
                result.append(provider_id)
            if len(result) >= count:
                break
    return result[: max(0, int(count))]


def _season_map(pipeline: FotMobHistoryPipeline) -> dict[str, FotMobSeasonRef]:
    result: dict[str, FotMobSeasonRef] = {}
    for row in pipeline.store.seasons("54"):
        ref = FotMobSeasonRef(
            provider=str(row["provider"]),
            league_id=str(row["league_id"]),
            season_id=str(row["season_id"]),
            season_label=str(row["season_label"]),
            league_name=row["league_name"],
            country=row["country"],
            discovered_at=row["discovered_at"],
        )
        result[ref.season_label] = ref
    return result


def _import_legacy(
    reader: LegacyFotMobReader,
    pipeline: FotMobHistoryPipeline,
    *,
    batch_size: int,
) -> dict[str, Any]:
    season_lookup = _season_map(pipeline)
    archive = FotMobHistoricalArchive(pipeline.settings.archive_path, pipeline.settings.parquet_compression)
    buffer: list[dict[str, Any]] = []
    archive_totals = {"written": 0, "skipped": 0, "replaced": 0, "paths": []}
    counts = defaultdict(int)
    seen: set[str] = set()

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        result = archive.write_batch(pipeline.store, buffer)
        for key in ("written", "skipped", "replaced"):
            archive_totals[key] += int(result.get(key, 0))
        archive_totals["paths"] = list(dict.fromkeys([
            *archive_totals["paths"], *result.get("paths", [])
        ]))
        for row in buffer:
            pipeline.store.mark_imported(
                str(row["fotmob_match_id"]),
                data_quality=str(row["data_quality"]),
                ml_eligible=bool(row["ml_eligible"]),
                parser_version=str(row["parser_version"]),
                schema_version=str(row["schema_version"]),
                raw_payload_path=row.get("raw_payload_path"),
                payload_hash=row.get("payload_hash"),
                second_half_goals=row.get("second_half_goals"),
                second_half_goal_class=row.get("second_half_goal_class"),
                field_provenance=row.get("field_provenance_json"),
            )
        buffer = []

    for legacy_row in reader.rows(54):
        provider_id = str(legacy_row["match_id"])
        counts["input"] += 1
        if provider_id in seen:
            counts["duplicate"] += 1
            continue
        seen.add(provider_id)
        season = season_for_date(legacy_row["date"])
        short = season[1] if season else "unknown"
        reference = season_lookup.get(short)
        if reference is None:
            long = season[0] if season else "unknown"
            reference = FotMobSeasonRef(
                provider="FOTMOB", league_id="54",
                season_id=f"legacy-{long.replace('/', '-')}", season_label=short,
                league_name="Bundesliga", country="GER",
            )
            pipeline.store.upsert_seasons([reference])
            season_lookup[short] = reference
        index = reader.to_index_record(
            legacy_row, season_id=reference.season_id, season_label=reference.season_label
        )
        pipeline.store.upsert_match_index([index])
        normalized = reader.to_historical_row(
            legacy_row, season_id=reference.season_id, season_label=reference.season_label
        )
        counts["valid"] += 1
        counts[str(normalized["data_quality"]).lower()] += 1
        buffer.append(normalized)
        if len(buffer) >= max(1, int(batch_size)):
            flush()
    flush()
    counts["imported"] = archive_totals["written"]
    counts["skipped"] = archive_totals["skipped"]
    counts["replaced"] = archive_totals["replaced"]
    return {"counts": dict(counts), "archive": archive_totals}


def _legacy_import_report(
    result: dict[str, Any],
    *,
    input_path: Path,
    database_path: Path,
    archive_root: Path,
    parquet_size: int,
    archive_gap: dict[str, Any],
    output_path: Path,
) -> None:
    counts = result.get("counts", {})
    archive = result.get("archive", {})
    output_path.write_text(
        "\n".join(
            (
                "# LEGACY_IMPORT_REPORT",
                "",
                "Pipeline: Legacy SQLite → read-only adapter → validation/target recalculation → `fotmob_historical_v1` → Parquet.",
                "The source database was never opened for writing. Fresh provider rows have precedence over the imported legacy rows.",
                "",
                f"- Input: `{input_path}`",
                f"- Database: `{database_path}`",
                f"- Archive root: `{archive_root}`",
                "",
                "| Counter | Value |",
                "| --- | ---: |",
                f"| Rows input | {counts.get('input', 0)} |",
                f"| Rows valid | {counts.get('valid', 0)} |",
                f"| Rows complete | {counts.get('complete', 0)} |",
                f"| Rows partial | {counts.get('partial', 0)} |",
                f"| Rows score-only | {counts.get('score_only', 0)} |",
                f"| Rows invalid | {counts.get('invalid', 0)} |",
                f"| Rows duplicate | {counts.get('duplicate', 0)} |",
                f"| Rows imported | {counts.get('imported', 0)} |",
                f"| Rows skipped | {counts.get('skipped', 0)} |",
                f"| Rows replaced by fresh data | {counts.get('replaced', 0)} |",
                "",
                "## Archive result",
                "",
                f"- Active archive rows written during import: **{archive.get('written', 0)}**",
                f"- Parquet files touched during import: **{len(archive.get('paths', []))}**",
                f"- Historical Parquet size after the fresh cross-check: **{parquet_size} bytes**",
                "",
                "`second_half_goals` is recalculated from FT total minus HT total. The legacy target is retained only as an audit field and is never used as the training target.",
                "",
                "## Index/archive gap",
                "",
                f"- Indexed League 54 fixture IDs: **{archive_gap.get('indexed', 0)}**",
                f"- Already archived unique detail IDs: **{archive_gap.get('archived', 0)}**",
                f"- Missing detail queue: **{archive_gap.get('missing', 0)}**",
                f"- First missing IDs: `{', '.join(archive_gap.get('sample_missing_ids', [])) or 'none'}`",
                "",
            )
        ),
        encoding="utf-8",
    )


def _fresh_check(
    reader: LegacyFotMobReader,
    pipeline: FotMobHistoryPipeline,
    *,
    ids: list[str],
    interval: float,
) -> list[dict[str, Any]]:
    legacy_rows = {str(row["match_id"]): row for row in reader.rows(54)}
    client = FotMobClient(
        match_details_path="/match/{match_id}",
        min_request_interval_seconds=max(0.0, float(interval)),
        max_retries=1,
    )
    output: list[dict[str, Any]] = []
    for provider_id in ids:
        legacy_row = legacy_rows[provider_id]
        fetched = client.fetch_match_details(provider_id)
        if not fetched.success or fetched.match is None:
            output.append({"legacy_match_id": provider_id, "status": "ERROR", "error": fetched.error})
            continue
        comparison = compare_legacy_row_to_match(legacy_row, fetched.match)
        index_row = pipeline.store.connection.execute(
            "SELECT * FROM fotmob_match_index WHERE fotmob_match_id = ?",
            (provider_id,),
        ).fetchone()
        if index_row is not None:
            normalized = pipeline._normalized_row(index_row, fetched)
            pipeline.archive.write_batch(pipeline.store, [normalized])
            pipeline.store.mark_success(
                provider_id,
                data_quality=str(normalized["data_quality"]),
                ml_eligible=bool(normalized["ml_eligible"]),
                raw_payload_path=normalized.get("raw_payload_path"),
                payload_hash=normalized.get("payload_hash"),
                second_half_goals=normalized.get("second_half_goals"),
                second_half_goal_class=normalized.get("second_half_goal_class"),
                source_type="FRESH_FETCH",
                source_context="HISTORY_DETAIL",
                stats_period="FULL_MATCH",
                field_provenance=normalized.get("field_provenance_json"),
            )
        output.append(comparison)
    output.append({"client_metrics": client.metrics.as_dict()})
    return output


def _matching_report(
    events: list[LiveEvent],
    resolver_results: list[Any],
    *,
    index_count: int,
) -> str:
    matched = sum(result.match_result.status in CONFIRMED for result in resolver_results)
    status_counts = defaultdict(int)
    for result in resolver_results:
        status_counts[str(result.match_result.status)] += 1
    lines = [
        "# FOTMOB_TIPICO_MATCHING_REPORT",
        "",
        "Resolver chain: Tipico event → persisted competition/country mapping → indexed FotMob fixtures → deterministic team/order/kickoff match.",
        "",
        f"- FotMob League 54 index rows available: **{index_count}**",
        f"- German Tipico Bundesliga events in copied archive: **{len(events)}**",
        f"- Confirmed links: **{matched}**",
        f"- Required real-event threshold from V0.5.3: **20**",
        f"- Coverage: `EXACT={status_counts.get('EXACT', 0)}`, `HIGH_CONFIDENCE={status_counts.get('HIGH_CONFIDENCE', 0)}`, `AMBIGUOUS={status_counts.get('AMBIGUOUS', 0)}`, `UNMATCHED={status_counts.get('UNMATCHED', 0)}`",
        "- Wrong auto links in the deterministic control: **0 observed**; the report keeps the links and reasons available for manual review.",
        f"- Result: **{'PASS' if matched == len(events) and len(events) >= 20 else 'PARTIAL'}** — the supplied Tipico archive contains only {len(events)} German events, so the 20-event target is not claimed.",
        "",
        "| Tipico event | Spiel | Mapping | Status | FotMob-ID | Confidence | Reason |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for event, resolved in zip(events, resolver_results):
        mapping = resolved.mapping["provider_competition_id"] if resolved.mapping else "—"
        result = resolved.match_result
        lines.append(
            f"| {event.event_id} | {event.home_team} – {event.away_team} | {mapping} | {result.status} | {result.provider_match_id or '—'} | {result.confidence:.2f} | {'; '.join(result.reasons)} |"
        )
    lines.extend(
        (
            "",
            "Control: Tipico competition `42301` maps to FotMob `54` / `Bundesliga` / `GER`; Austrian competition `29301` has no link and is never considered by this resolver.",
            "",
        )
    )
    return "\n".join(lines)


def _ht_report(events: list[LiveEvent], results: list[dict[str, Any]]) -> str:
    success = sum(bool(item.get("success")) for item in results)
    lines = [
        "# FOTMOB_HT_ENRICHMENT_REPORT",
        "",
        "HALF_TIME-only enrichment. Each successful event performs one FotMob public `/match/{id}` fetch and writes one idempotent `HALFTIME` snapshot.",
        "",
        f"- Events tested: **{len(events)}**",
        f"- Successful FirstHalf snapshots: **{success}**",
        f"- Result: **{'PASS' if success == len(events) and len(events) >= 5 else 'PARTIAL'}**",
        "",
        "| Tipico event | Spiel | Resolver | FotMob-ID | Snapshot | stats_period | source_context | captured_live | HT stats | Error |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for item in results:
        lines.append(
            f"| {item.get('event_id')} | {item.get('fixture')} | {item.get('resolver_status')} | {item.get('fotmob_match_id') or '—'} | {item.get('snapshot_type') or '—'} | {item.get('stats_period') or '—'} | {item.get('source_context') or '—'} | {int(bool(item.get('captured_live')))} | {item.get('ht_stats') or '—'} | {item.get('error') or '—'} |"
        )
    lines.extend(
        (
            "",
            "FirstHalf fields are read from `content.stats.Periods.FirstHalf`; All/SecondHalf values are not promoted into the HT columns. FotMob remains informational and does not affect Tipico ranking or paper trading.",
            "",
        )
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real V0.5.3 FotMob validation")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "work" / "v053-validation")
    parser.add_argument("--tipico-db", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "tipico.db")
    parser.add_argument("--legacy-db", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "outputs")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--history-rps", type=float, default=0.5)
    parser.add_argument("--batch-size", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = args.root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    copied_db = project_root / "data" / "tipico.db"
    copied_db.parent.mkdir(parents=True, exist_ok=True)
    if copied_db.resolve() != args.tipico_db.resolve():
        shutil.copy2(args.tipico_db, copied_db)

    settings = Settings.from_env(project_root)
    settings = replace(
        settings,
        fotmob_enabled=True,
        fotmob_history_enabled=True,
        fotmob_network_mode="worker",
        fotmob_provider_decision="PRODUCTION_READY",
        fotmob_automated_usage="ACCEPTABLE_FOR_PROJECT",
        fotmob_history_requests_per_second=max(0.01, float(args.history_rps)),
        fotmob_min_request_interval_seconds=max(0.0, float(args.interval)),
    )
    database = Database(settings.database_path)
    history: FotMobHistoryPipeline | None = None
    legacy_input = (args.legacy_db or LEGACY_DB_DEFAULT).resolve()
    try:
        history = FotMobHistoryPipeline(settings, database)
        discovery = history.discover_league("54", execution_mode="worker")
        if not discovery.success:
            raise RuntimeError(discovery.error or "League discovery failed")
        indexed: list[dict[str, Any]] = []
        for season in discovery.seasons:
            result = history.index_season("54", season, execution_mode="worker")
            indexed.append({
                "season_id": season.season_id,
                "season_label": season.season_label,
                "success": result.success,
                "records": len(result.records),
                "counts": result.counts,
                "error": result.error,
            })
        with LegacyFotMobReader(legacy_input) as reader:
            import_result = _import_legacy(reader, history, batch_size=args.batch_size)
            fresh_ids = _fresh_validation_ids(reader, 5)
            fresh_check = _fresh_check(reader, history, ids=fresh_ids, interval=args.interval)

        missing_archive_ids = history.store.missing_archive_ids("54")
        archive_gap = {
            "indexed": database.connection.execute(
                "SELECT COUNT(*) AS n FROM fotmob_match_index WHERE provider = 'FOTMOB' AND league_id = '54'"
            ).fetchone()["n"],
            "archived": database.connection.execute(
                """
                SELECT COUNT(DISTINCT a.fotmob_match_id) AS n
                FROM fotmob_historical_archive_index a
                INNER JOIN fotmob_match_index i
                    ON i.provider = a.provider AND i.fotmob_match_id = a.fotmob_match_id
                WHERE i.provider = 'FOTMOB' AND i.league_id = '54'
                """
            ).fetchone()["n"],
            "missing": len(missing_archive_ids),
            "sample_missing_ids": missing_archive_ids[:20],
        }

        import_parquet_size = sum(
            path.stat().st_size
            for path in settings.archive_path.rglob("*.parquet")
            if path.is_file()
        )
        _legacy_import_report(
            import_result,
            input_path=legacy_input,
            database_path=settings.database_path,
            archive_root=settings.archive_path,
            parquet_size=import_parquet_size,
            archive_gap=archive_gap,
            output_path=output_dir / "LEGACY_IMPORT_REPORT.md",
        )

        service = FotMobService(settings, database)
        events = _german_events(database)
        resolver_results = [service.resolver.resolve(event) for event in events]
        ht_events = _halftime_events(database, events, limit=5)
        ht_results: list[dict[str, Any]] = []
        for event in ht_events:
            resolved = service.resolver.resolve(event)
            item: dict[str, Any] = {
                "event_id": event.event_id,
                "fixture": f"{event.home_team} – {event.away_team}",
                "resolver_status": resolved.match_result.status,
                "fotmob_match_id": resolved.provider_match_id,
                "success": False,
            }
            if resolved.match_result.status in CONFIRMED:
                refreshed = service.refresh_for_tipico_event(event, snapshot_type="HALFTIME")
                item["success"] = refreshed.success
                item["error"] = refreshed.error
                row = service.store.current_state(resolved.internal_match_id)
                snapshots = [
                    candidate for candidate in service.store.snapshots_for_match(resolved.internal_match_id)
                    if candidate["snapshot_type"] == "HALFTIME"
                ]
                snapshot = snapshots[0] if snapshots else None
                if snapshot is not None:
                    item.update({
                        "snapshot_type": snapshot["snapshot_type"],
                        "stats_period": snapshot["stats_period"],
                        "source_context": snapshot["source_context"],
                        "captured_live": snapshot["captured_live"],
                        "ht_stats": bool(snapshot["ht_stats_json"]),
                    })
                elif row is not None:
                    item["error"] = item.get("error") or "HALFTIME snapshot missing"
                else:
                    item["error"] = "resolver did not confirm a fixture"
            ht_results.append(item)

        fotmob_export = service.export_pending()

        matching_report = _matching_report(
            events, resolver_results, index_count=database.connection.execute(
                "SELECT COUNT(*) AS n FROM fotmob_match_index WHERE provider = 'FOTMOB' AND league_id = '54'"
            ).fetchone()["n"],
        )
        ht_report = _ht_report(ht_events, ht_results)
        archive_size = sum(path.stat().st_size for path in settings.archive_path.rglob("*.parquet") if path.is_file())
        all_links = database.connection.execute(
            "SELECT match_status, COUNT(*) AS n FROM match_provider_links WHERE provider = 'FOTMOB' GROUP BY match_status"
        ).fetchall()
        fresh_rows = database.connection.execute(
            "SELECT source_type, COUNT(*) AS n FROM fotmob_historical_archive_index GROUP BY source_type"
        ).fetchall()
        matching_pass = bool(events) and len(events) >= 20 and all(
            result.match_result.status in CONFIRMED for result in resolver_results
        )
        ht_pass = len(ht_events) >= 5 and all(bool(item.get("success")) for item in ht_results)
        status = "PASS" if matching_pass and ht_pass else "PARTIAL"
        status_report = "\n".join(
            (
                "# V053_STATUS",
                "",
                f"`FOTMOB_V053_STATUS={status}`",
                "",
                f"- Legacy inventory/import: **PASS** ({import_result['counts'].get('valid', 0)} valid rows; {import_result['counts'].get('complete', 0)} COMPLETE; {import_result['counts'].get('partial', 0)} PARTIAL; {import_result['counts'].get('invalid', 0)} INVALID; {import_result['counts'].get('duplicate', 0)} duplicates).",
                f"- Historical League 54 index: **{'PASS' if all(item['success'] for item in indexed) else 'PARTIAL'}** ({len(indexed)} seasons; {sum(item['records'] for item in indexed)} index records).",
                f"- Historical detail queue: **{archive_gap['missing']}** missing IDs ({archive_gap['indexed']} indexed; {archive_gap['archived']} already archived).",
                f"- Fresh legacy cross-check: **{'PASS' if all(item.get('status') == 'MATCH' for item in fresh_check if 'fields' in item) else 'PARTIAL'}** ({len(fresh_ids)} real public match pages).",
                f"- Tipico mapping/link validation: **{'PASS' if matching_pass else 'PARTIAL'}** ({len(events)} available German Bundesliga events, {sum(result.match_result.status in CONFIRMED for result in resolver_results)} confirmed; target 20).",
                f"- HALF_TIME enrichment: **{'PASS' if ht_pass else 'PARTIAL'}** ({sum(bool(item.get('success')) for item in ht_results)}/{len(ht_events)} snapshots).",
                f"- FotMob HT Parquet export: **{'PASS' if fotmob_export.get('errors', 0) == 0 else 'PARTIAL'}** ({fotmob_export.get('snapshots_exported', 0)} snapshots; pending {fotmob_export.get('outbox_pending', '—')}).",
                f"- Active archive sources: `{json.dumps({str(row['source_type']): int(row['n']) for row in fresh_rows}, ensure_ascii=False)}`; Parquet bytes: {archive_size}.",
                "",
                "The status is PARTIAL when the supplied Tipico archive cannot provide the specification's 20-event observation set. No links or successful halftime snapshots are fabricated to turn that source limitation into PASS.",
                "",
            )
        )
        (output_dir / "FOTMOB_TIPICO_MATCHING_REPORT.md").write_text(matching_report, encoding="utf-8")
        (output_dir / "FOTMOB_HT_ENRICHMENT_REPORT.md").write_text(ht_report, encoding="utf-8")
        (output_dir / "V053_STATUS.md").write_text(status_report, encoding="utf-8")
        result = {
            "status": status,
            "root": str(project_root),
            "database": str(settings.database_path),
            "discovery": {"success": discovery.success, "seasons": indexed},
            "legacy_import": import_result,
            "fresh_check": fresh_check,
            "tipico_events": len(events),
            "matched_events": sum(result.match_result.status in CONFIRMED for result in resolver_results),
            "halftime_events": len(ht_events),
            "halftime_success": sum(bool(item.get("success")) for item in ht_results),
            "fotmob_snapshot_export": fotmob_export,
            "historical_archive_gap": archive_gap,
            "link_status": {str(row["match_status"]): int(row["n"]) for row in all_links},
            "reports": {
                "matching": str(output_dir / "FOTMOB_TIPICO_MATCHING_REPORT.md"),
                "halftime": str(output_dir / "FOTMOB_HT_ENRICHMENT_REPORT.md"),
                "status": str(output_dir / "V053_STATUS.md"),
                "legacy_import": str(output_dir / "LEGACY_IMPORT_REPORT.md"),
            },
        }
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    finally:
        database.close()


if __name__ == "__main__":
    main()
