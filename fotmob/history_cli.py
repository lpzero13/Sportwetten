"""Command-line entry points for the opt-in FotMob historical foundation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from config import Settings
from storage.database import Database

from .history_models import FotMobSeasonRef
from .history_pipeline import FotMobHistoryPipeline
from .max_throughput import write_max_status_report, write_max_throughput_report
from .performance import write_performance_report, write_status_report


def _add_root(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS lets ``--root`` work both before and after a subcommand.
    parser.add_argument(
        "--root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Projektwurzel (Standard: Repository-Ordner)",
    )


def _add_league(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--league", "--league-id", dest="league_id", required=True)


def _add_season_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--season",
        dest="season_selector",
        required=False,
        help="explizite FotMob season_id oder sichtbares Label, z. B. 2025/26",
    )
    parser.add_argument(
        "--season-id",
        dest="season_id_override",
        help="explizite season_id; überschreibt --season für Index/Fetchtasks",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fotmob_history",
        description=(
            "Opt-in FotMob Historical Discovery. Ohne --payload werden externe "
            "Requests nur im ausdrücklich aktivierten manuellen CLI-Modus ausgeführt."
        ),
    )
    _add_root(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    seasons = subparsers.add_parser("seasons", help="Liga-Metadaten und Season-IDs entdecken")
    _add_root(seasons)
    _add_league(seasons)
    seasons.add_argument("--payload", type=Path, help="lokale JSON-Payload für Offline-Discovery")

    index = subparsers.add_parser("index", help="Fixtures/Results einer Season indexieren")
    _add_root(index)
    _add_league(index)
    _add_season_selector(index)
    index.add_argument("--season-label", help="Label zusammen mit --season-id bei Offline-Payload")
    index.add_argument("--payload", type=Path, help="lokale JSON-Payload für Offline-Indexierung")

    status = subparsers.add_parser("status", help="Queue- und Archivstatus einer Season anzeigen")
    _add_root(status)
    _add_league(status)
    _add_season_selector(status)

    sample = subparsers.add_parser("sample", help="deterministische Finished-Sample speichern")
    _add_root(sample)
    _add_league(sample)
    _add_season_selector(sample)
    sample.add_argument("--matches", type=int, default=5)

    fetch = subparsers.add_parser("fetch", help="Detail-Queue resumierbar abarbeiten")
    _add_root(fetch)
    _add_league(fetch)
    _add_season_selector(fetch)
    fetch.add_argument("--workers", type=int)
    fetch.add_argument("--retry-failed", action="store_true")
    fetch.add_argument("--sample-only", action="store_true")
    fetch.add_argument("--limit", type=int)
    fetch.add_argument("--batch-size", type=int)

    dates = subparsers.add_parser(
        "dates",
        help="FotMob-Tagesdaten für einen inklusiven Von-/Bis-Bereich laden",
    )
    _add_root(dates)
    dates.add_argument("--from-date", required=True, help="Startdatum YYYY-MM-DD")
    dates.add_argument("--to-date", required=True, help="Enddatum YYYY-MM-DD")
    dates.add_argument(
        "--league",
        "--league-id",
        dest="league_id",
        default=None,
        help="Legacy: einzelne FotMob-Liga; ohne diese Option werden alle Tagesfeed-Ligen geladen",
    )
    dates.add_argument("--workers", type=int)
    dates.add_argument(
        "--index-only",
        action="store_true",
        help="Nur Datum/Land/Liga/Match-Index laden, keine Matchdetails abrufen",
    )

    performance = subparsers.add_parser(
        "performance",
        aliases=["probe"],
        help="V0.5.6: drei abgeschlossene Tage messen und RPS/Worker-Profil speichern",
    )
    _add_root(performance)
    performance.add_argument("--from-date", required=True, help="Startdatum YYYY-MM-DD")
    performance.add_argument("--to-date", required=True, help="Enddatum YYYY-MM-DD")
    performance.add_argument(
        "--requests-per-level",
        type=int,
        help="Detailanfragen je RPS-/Worker-Stufe (Standard aus FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL)",
    )

    max_performance = subparsers.add_parser(
        "max-throughput",
        aliases=["performance-max", "probe-max"],
        help="V0.5.6.1: maximal stabile RPS und den technischen Bottleneck messen",
    )
    _add_root(max_performance)
    max_performance.add_argument("--from-date", required=True, help="Startdatum YYYY-MM-DD")
    max_performance.add_argument("--to-date", required=True, help="Enddatum YYYY-MM-DD")
    max_performance.add_argument(
        "--requests-per-level",
        type=int,
        default=100,
        help="Detailanfragen je RPS-/Worker-Stufe (Standard: 100)",
    )
    max_performance.add_argument(
        "--critical-requests",
        type=int,
        default=250,
        help="Detailanfragen für die zusätzliche höchste stabile Stufe (Standard: 250)",
    )
    max_performance.add_argument(
        "--max-target-rps",
        type=float,
        default=100.0,
        help="Temporäres oberes Benchmark-Limit; Standard ist 100 RPS",
    )
    max_performance.add_argument(
        "--include-worker-50",
        action="store_true",
        help="Optional zusätzlich 50 Worker messen, wenn mehr Parallelität begründet ist",
    )

    return parser


def _root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "root", Path(__file__).resolve().parents[1])).resolve()


def _load_payload(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Payload konnte nicht gelesen werden: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Payload ist kein gültiges JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Payload muss ein JSON-Objekt sein")
    return value


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping) or hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return dict(row)


def _season_dict(season: FotMobSeasonRef) -> dict[str, Any]:
    return {
        "provider": season.provider,
        "league_id": season.league_id,
        "season_id": season.season_id,
        "season_label": season.season_label,
        "league_name": season.league_name,
        "country": season.country,
        "discovered_at": season.discovered_at,
    }


def _resolve_season(pipeline: FotMobHistoryPipeline, args: argparse.Namespace) -> FotMobSeasonRef | None:
    explicit_id = getattr(args, "season_id_override", None)
    selector = explicit_id or getattr(args, "season_selector", None)
    if not selector:
        return None
    return pipeline.resolve_season(args.league_id, str(selector))


def _explicit_season(args: argparse.Namespace) -> FotMobSeasonRef | None:
    season_id = getattr(args, "season_id_override", None)
    if not season_id:
        return None
    label = getattr(args, "season_label", None)
    if not label:
        return None
    return FotMobSeasonRef(
        provider="FOTMOB",
        league_id=str(args.league_id),
        season_id=str(season_id),
        season_label=str(label),
    )


def _result_status(success: bool, error: str | None = None) -> str:
    if success:
        return "PASS"
    if error and "gesperrt" in error.casefold():
        return "BLOCKED_BY_POLICY"
    return "ERROR"


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _root(args)
    settings = Settings.from_env(root)
    database = Database(settings.database_path)
    pipeline = FotMobHistoryPipeline(settings, database)
    try:
        if args.command in {"performance", "probe"}:
            from scripts.audit_project_capabilities import generate_report

            audit_path = generate_report(root)
            result = pipeline.run_performance_probe(
                args.from_date,
                args.to_date,
                requests_per_level=args.requests_per_level,
                execution_mode="manual",
            )
            report_path = write_performance_report(
                result,
                root / "outputs" / "V056_FOTMOB_PERFORMANCE_REPORT.md",
            )
            status_path = write_status_report(
                result,
                root / "outputs" / "V056_STATUS.md",
            )
            result = {
                **result,
                "audit_path": str(audit_path),
                "report_path": str(report_path),
                "status_path": str(status_path),
            }
            return result
        if args.command in {"max-throughput", "performance-max", "probe-max"}:
            from scripts.audit_project_capabilities import generate_report

            audit_path = generate_report(root)
            result = pipeline.run_max_throughput_probe(
                args.from_date,
                args.to_date,
                requests_per_level=args.requests_per_level,
                critical_requests=args.critical_requests,
                max_target_rps=args.max_target_rps,
                include_worker_50=args.include_worker_50,
                execution_mode="manual",
            )
            report_path = write_max_throughput_report(
                result,
                root / "outputs" / "V0561_MAX_THROUGHPUT_REPORT.md",
            )
            status_path = write_max_status_report(
                result,
                root / "outputs" / "V0561_STATUS.md",
            )
            return {
                **result,
                "audit_path": str(audit_path),
                "report_path": str(report_path),
                "status_path": str(status_path),
            }
        payload = _load_payload(getattr(args, "payload", None))
        if args.command == "seasons":
            result = pipeline.discover_league(
                args.league_id,
                payload=payload,
                execution_mode="manual",
            )
            return {
                "status": _result_status(result.success, result.error),
                "league_id": result.league_id,
                "league_name": result.league_name,
                "country": result.country,
                "seasons": [_season_dict(item) for item in result.seasons],
                "error": result.error,
                "policy": {
                    "history_enabled": settings.fotmob_history_enabled,
                    "network_mode": settings.fotmob_network_mode,
                    "provider_decision": settings.fotmob_provider_decision,
                    "automated_usage": settings.fotmob_automated_usage,
                },
            }

        if args.command == "dates":
            return pipeline.load_date_range(
                args.from_date,
                args.to_date,
                league_id=args.league_id,
                fetch_details=not args.index_only,
                workers=args.workers or settings.fotmob_history_workers,
                execution_mode="manual",
            )

        season = _resolve_season(pipeline, args)
        if season is None and not getattr(args, "season_selector", None) and not getattr(args, "season_id_override", None):
            return {
                "status": "ERROR",
                "error": "Bitte --season oder --season-id angeben.",
            }
        if season is None and args.command == "index":
            # The index job may resolve a selector through the separate league
            # discovery endpoint.  For offline fixtures this is still fully
            # local; an explicit ID + label is the final fallback and no ID is
            # invented.
            discovery = pipeline.discover_league(
                args.league_id,
                payload=payload,
                execution_mode="manual",
            )
            season = _resolve_season(pipeline, args)
            if season is None:
                season = _explicit_season(args)
            if season is None and not discovery.success:
                return {"status": _result_status(False, discovery.error), "error": discovery.error}
        if season is None:
            return {
                "status": "ERROR",
                "error": "Season nicht im Katalog gefunden; zuerst seasons ausführen oder eine explizite season_id verwenden.",
            }

        if args.command == "index":
            result = pipeline.index_season(
                args.league_id,
                season,
                payload=payload,
                execution_mode="manual",
            )
            return {
                "status": _result_status(result.success, result.error),
                "league_id": result.league_id,
                "season_id": result.season_id,
                "season_label": result.season_label,
                "counts": result.counts,
                "matches": [_record_dict(item) for item in result.records],
                "error": result.error,
            }
        if args.command == "status":
            return {"status": "PASS", **pipeline.status(args.league_id, season.season_id)}
        if args.command == "sample":
            target_count = max(0, args.matches)
            rows = pipeline.sample_season(args.league_id, season.season_id, target_count)
            return {
                "status": "PASS" if len(rows) >= target_count else "PARTIAL",
                "league_id": args.league_id,
                "season_id": season.season_id,
                "season_label": season.season_label,
                "target_count": target_count,
                "sample_count": len(rows),
                "sample": [_row_dict(row) for row in rows],
            }
        if args.command == "fetch":
            return pipeline.fetch_details(
                args.league_id,
                season.season_id,
                workers=args.workers or settings.fotmob_history_workers,
                retry_failed=args.retry_failed,
                only_sample=args.sample_only,
                limit=args.limit,
                batch_size=args.batch_size or settings.fotmob_history_batch_size,
                execution_mode="manual",
            )
        raise ValueError(f"Unbekannter Befehl: {args.command}")
    finally:
        database.close()


def _record_dict(record: Any) -> dict[str, Any]:
    return {
        "provider": record.provider,
        "fotmob_match_id": record.provider_match_id,
        "league_id": record.league_id,
        "season_id": record.season_id,
        "season_label": record.season_label,
        "kickoff_at": record.kickoff_at,
        "home_team_id": record.home_team_id,
        "home_team_name": record.home_team_name,
        "away_team_id": record.away_team_id,
        "away_team_name": record.away_team_name,
        "round": record.round_name,
        "match_status": record.match_status,
        "league_name": record.league_name,
        "country": record.country,
        "country_code": record.country_code,
        "country_name": record.country_name,
        "is_next_day": record.is_next_day,
        "source_context": record.source_context,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        result = run(args)
    except (ValueError, OSError) as exc:
        result = {"status": "ERROR", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") not in {"ERROR"} else 1


if __name__ == "__main__":
    sys.exit(main())
