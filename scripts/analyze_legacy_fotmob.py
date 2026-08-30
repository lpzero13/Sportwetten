#!/usr/bin/env python3
"""Inventory and fresh-check the historic FotMob SQLite database.

The input is opened read-only.  A byte-for-byte backup is created before the
audit unless ``--no-backup`` is supplied.  Reports are deterministic apart
from request timestamps and are intended to be checked into the project.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fotmob.client import FotMobClient
from fotmob.history_models import FOTMOB_HISTORICAL_SCHEMA_VERSION
from fotmob.legacy import LEGACY_DB_DEFAULT, LegacyFotMobReader, compare_legacy_row_to_match, season_for_date


def _fmt(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value).replace("|", "/").replace("\n", " ")


def _md_table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    header_list = list(headers)
    lines = ["| " + " | ".join(header_list) + " |", "| " + " | ".join("---" for _ in header_list) + " |"]
    lines.extend("| " + " | ".join(_fmt(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _fresh_candidates(reader: LegacyFotMobReader, count: int) -> list[object]:
    rows = []
    for row in reader.rows(54):
        season = season_for_date(row["date"])
        if season is None or season[0] not in {"2024/2025", "2025/2026"}:
            continue
        converted = reader.to_historical_row(row)
        if all(converted.get(key) is not None for key in (
            "ht_xg_home", "ht_xg_away", "ht_shots_home", "ht_shots_away",
            "ht_shots_on_target_home", "ht_shots_on_target_away",
            "ht_big_chances_home", "ht_big_chances_away",
            "ht_corners_home", "ht_corners_away",
        )):
            rows.append(row)
    return rows[: max(0, int(count))]


def _sample_report(reader: LegacyFotMobReader, rows: list[object]) -> str:
    prepared = []
    for row in rows:
        normalized = reader.to_historical_row(row)
        prepared.append(
            (
                row,
                normalized,
            )
        )

    def pair(normalized: dict, prefix: str, field: str) -> str:
        return f"{normalized.get(f'{prefix}_{field}_home')} / {normalized.get(f'{prefix}_{field}_away')}"

    lines = [
        "# LEGACY_FOTMOB_SAMPLE",
        "",
        "Deterministischer Audit-Sample aus `matches` in der unveränderten Legacy-Datenbank.",
        "Die 60-Minuten-Felder stehen bewusst in einer eigenen Spalte und werden nicht als FirstHalf interpretiert.",
        "",
        _md_table(
            ("ID", "Datum", "Season", "Heim", "Auswärts", "HT", "FT", "HT xG", "HT Schüsse", "HT SOT", "HT BC", "HT Ecken", "HT Cards Y/R", "60' xG", "stored target", "recalculated 2H goals", "recalculated class"),
            (
                (
                    row["match_id"], row["date"], (season_for_date(row["date"]) or ("—",))[0],
                    row["home_team"], row["away_team"],
                    f"{row['ht_score_home']}:{row['ht_score_away']}",
                    f"{row['score_home']}:{row['score_away']}",
                    pair(normalized, "ht", "xg"),
                    pair(normalized, "ht", "shots"),
                    pair(normalized, "ht", "shots_on_target"),
                    pair(normalized, "ht", "big_chances"),
                    f"Y {pair(normalized, 'ht', 'yellow_cards')} / R {pair(normalized, 'ht', 'red_cards')}",
                    f"{row['xg_home_60']} / {row['xg_away_60']}",
                    row["goal_after_60"],
                    normalized["second_half_goals"],
                    normalized["second_half_goal_class"],
                )
                for row, normalized in prepared
            ),
        ),
        "",
        "## Recalculation",
        "",
        "`second_half_goals = (FT home + FT away) - (HT home + HT away)`; the legacy `goal_after_60` field is not used as the target.",
        "",
    ]
    return "\n".join(lines)


def _data_report(
    reader: LegacyFotMobReader,
    inventory: dict,
    *,
    backup: Path | None,
    fresh_results: list[dict],
    client_metrics: dict,
    no_network: bool,
) -> str:
    selected = inventory.get("selected_league") or {}
    season_counts = selected.get("season_counts", {})
    expected = [f"{year}/{str(year + 1)[-2:]}" for year in range(2019, 2026)]
    # Keep this explicit rather than hiding absent seasons behind a generated
    # range: the source really contains gaps.
    available_short = {f"{key.split('/')[0]}/{key.split('/')[-1][-2:]}" for key in season_counts}
    missing = [label for label in expected if label not in available_short]
    leagues = inventory.get("leagues", [])
    lines = [
        "# LEGACY_FOTMOB_DATA_REPORT",
        "",
        "## Scope and safety",
        "",
        f"- Input: `{reader.path}`",
        f"- Read mode: SQLite `mode=ro`; no in-place migration was performed.",
        f"- Backup: `{backup}`" if backup else "- Backup: not requested",
        f"- Historical schema target: `{FOTMOB_HISTORICAL_SCHEMA_VERSION}`",
        "",
        "## League inventory",
        "",
        _md_table(
            (
                "League ID", "Name", "Matches", "Min date", "Max date", "HT score", "FT score",
                "HT xG", "HT shots", "HT SOT", "HT BC", "HT corners", "60' coverage", "FotMob ID",
            ),
            (
                (
                    item["league_id"], ", ".join(item["league_names"]), item["matches"], item["min_date"],
                    item["max_date"], item["ht_score_coverage"], item["ft_score_coverage"],
                    item["ht_core_coverage"].get("xg", 0), item["ht_core_coverage"].get("shots", 0),
                    item["ht_core_coverage"].get("shots_on_target", 0), item["ht_core_coverage"].get("big_chances", 0),
                    item["ht_core_coverage"].get("corners", 0), json.dumps(item["m60_coverage"], ensure_ascii=False),
                    item["fotmob_match_id_coverage"],
                )
                for item in leagues
            ),
        ),
        "",
        "## Bundesliga / League 54",
        "",
        _md_table(
            ("Metric", "Value"),
            (
                ("Matches", selected.get("matches")),
                ("Date range", f"{selected.get('min_date')} – {selected.get('max_date')}"),
                ("Complete HT core", selected.get("complete_ht_core")),
                ("HT core coverage", json.dumps(selected.get("ht_core_coverage", {}), ensure_ascii=False)),
                ("Full core coverage", json.dumps(selected.get("full_core_coverage", {}), ensure_ascii=False)),
                ("60-minute coverage", json.dumps(selected.get("m60_coverage", {}), ensure_ascii=False)),
                ("Season counts", json.dumps(season_counts, ensure_ascii=False)),
                ("Expected seasons absent in source", ", ".join(missing) or "none"),
            ),
        ),
        "",
        "## Fresh public-page check",
        "",
        f"- Endpoint: `GET https://www.fotmob.com/match/{{match_id}}`",
        f"- Mode: {'no-network' if no_network else 'real public requests'}",
        f"- Client metrics: `{json.dumps(client_metrics, ensure_ascii=False, default=str)}`",
        "",
        _md_table(
            ("ID", "Status", "MATCH", "MISMATCH", "Legacy missing", "Current missing", "Reason"),
            (
                (
                    result.get("legacy_match_id"), result.get("status"), result.get("match"),
                    result.get("mismatch"), result.get("legacy_missing"), result.get("current_missing"),
                    result.get("error") or "field-level comparison",
                )
                for result in fresh_results
            ),
        ),
        "",
        "Field classifications are restricted to `MATCH`, `MISMATCH`, `LEGACY_MISSING` and `CURRENT_MISSING`; details are stored in the JSON emitted by the script.",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory and fresh-check the legacy FotMob SQLite database")
    parser.add_argument("--input", type=Path, default=LEGACY_DB_DEFAULT)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "outputs")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--league-id", default="54")
    parser.add_argument("--fresh-check", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if not args.no_backup:
        backup = (args.root.resolve() / "work" / "v053-legacy-backup" / args.input.name)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists() or backup.stat().st_size != args.input.stat().st_size:
            shutil.copy2(args.input, backup)

    with LegacyFotMobReader(args.input) as reader:
        inventory = reader.inventory(args.league_id)
        sample = reader.audit_sample(args.league_id, 20)
        (output_dir / "LEGACY_FOTMOB_SAMPLE.md").write_text(
            _sample_report(reader, sample), encoding="utf-8"
        )
        fresh_results: list[dict] = []
        client_metrics: dict = {}
        candidates = _fresh_candidates(reader, args.fresh_check)
        if args.no_network:
            fresh_results = [
                {"legacy_match_id": str(row["match_id"]), "status": "SKIPPED", "error": "--no-network"}
                for row in candidates
            ]
        else:
            client = FotMobClient(
                match_details_path="/match/{match_id}",
                min_request_interval_seconds=max(0.0, float(args.interval)),
                max_retries=1,
            )
            for row in candidates:
                fetched = client.fetch_match_details(str(row["match_id"]))
                if not fetched.success or fetched.match is None:
                    fresh_results.append(
                        {"legacy_match_id": str(row["match_id"]), "status": "ERROR", "error": fetched.error}
                    )
                else:
                    fresh_results.append(compare_legacy_row_to_match(row, fetched.match))
            client_metrics = client.metrics.as_dict()
        (output_dir / "LEGACY_FOTMOB_DATA_REPORT.md").write_text(
            _data_report(
                reader,
                inventory,
                backup=backup,
                fresh_results=fresh_results,
                client_metrics=client_metrics,
                no_network=args.no_network,
            ),
            encoding="utf-8",
        )
        result = {
            "status": "PASS",
            "input": str(reader.path),
            "backup": str(backup) if backup else None,
            "total_rows": inventory["total_rows"],
            "league_id": str(args.league_id),
            "league_rows": (inventory.get("selected_league") or {}).get("matches", 0),
            "sample_rows": len(sample),
            "fresh_check": fresh_results,
            "reports": {
                "data": str(output_dir / "LEGACY_FOTMOB_DATA_REPORT.md"),
                "sample": str(output_dir / "LEGACY_FOTMOB_SAMPLE.md"),
            },
        }
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
