"""Compact UI for the Tipico-only research and backtest lab."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import streamlit as st

from config import Settings
from tipico_research.runner import latest_run, run_study


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        import pyarrow.parquet as parquet
        return parquet.read_table(path).to_pylist()
    except (ImportError, OSError, ValueError):
        return []


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


def _latest_manifest(run_path: Path) -> dict[str, Any]:
    return _read_json(run_path / "RUN_MANIFEST.json")


def _matches_bucket(row: dict[str, Any], bucket: dict[str, str]) -> bool:
    fields = {
        "P1": "p1_market",
        "Q0": "q_zero",
        "Q2_PLUS": "q_two_plus",
        "HT_TOTAL": "ht_goals",
    }
    field = fields.get(str(bucket.get("dimension") or ""))
    if field == "ht_goals" and row.get("ht_goals") is None:
        if row.get("ht_score_home") is None or row.get("ht_score_away") is None:
            return False
        value = float(row["ht_score_home"]) + float(row["ht_score_away"])
    else:
        try:
            value = float(row.get(field)) if field else None
        except (TypeError, ValueError):
            value = None
    if value is None:
        return False
    try:
        lower = float(bucket.get("lower_inclusive"))
    except (TypeError, ValueError):
        lower = None
    try:
        upper = float(bucket.get("upper_exclusive"))
    except (TypeError, ValueError):
        upper = None
    return (lower is None or value >= lower) and (upper is None or value < upper)


def render_tipico_backtest(settings: Settings) -> None:
    """Render research outputs without running anything on a normal rerun."""

    st.title("Tipico Backtest")
    st.caption("Empirische Musteranalyse ausschließlich aus gespeicherten Tipico-Daten · kein ML · kein FotMob")
    source = settings.tipico_backtest_source_path
    output = settings.tipico_backtest_output_path
    source_label = str(source)
    source_exists = source.is_file()
    source_columns = st.columns([3, 1.2])
    source_columns[0].caption(f"Quelle: {source_label}")
    if source_exists:
        source_columns[1].success("Quelle vorhanden")
    else:
        source_columns[1].error("Quelle fehlt")

    if st.button("Tipico-Studie jetzt ausführen", type="primary", key="tipico-backtest-run"):
        if not source_exists:
            st.error("Keine Tipico-SQLite-Quelle gefunden. TIPICO_BACKTEST_SOURCE_DB setzen oder data/tipico.db bereitstellen.")
        else:
            with st.spinner("Audit, Dataset und 18 Varianten werden berechnet …"):
                try:
                    result = run_study(source, output)
                    st.session_state["tipico_backtest_run_path"] = result["run_path"]
                    st.success(f"Studie abgeschlossen: {result['run_id']}")
                except Exception as exc:
                    st.error(f"Studie fehlgeschlagen: {exc}")

    selected_run = st.session_state.get("tipico_backtest_run_path")
    run_path = Path(selected_run) if selected_run else latest_run(output)
    if run_path is None or not run_path.is_dir():
        st.info("Noch kein Tipico-Backtest-Lauf vorhanden. Die Studie wird ausschließlich über den Button gestartet.")
        return
    manifest = _latest_manifest(run_path)
    dataset = manifest.get("dataset", {})
    st.caption(f"Letzter Lauf: {manifest.get('run_id', run_path.name)} · erstellt {manifest.get('created_at_utc', '—')}")
    columns = st.columns(5)
    columns[0].metric("HT-STABLE", dataset.get("observations", 0))
    columns[1].metric("Ergebnis valide", dataset.get("resolved", 0))
    columns[2].metric("Entry-eligible", dataset.get("entry_eligible", 0))
    columns[3].metric("Varianten", len(manifest.get("variants", [])))
    columns[4].metric("Quelle", str(manifest.get("source_sha256", ""))[:8])

    overview, patterns, variants, evidence = st.tabs(["Überblick", "P1 & Muster", "Varianten", "Spielbeleg"])
    with overview:
        st.subheader("Datenbasis")
        st.write({
            "Entry-Policy": manifest.get("entry_policy", "—"),
            "Einsatz je Spiel": _money(manifest.get("stake_eur")),
            "Evidenzstufen": dataset.get("evidence_levels", {}),
            "Unterschiedliche Events": dataset.get("different_events", 0),
        })
        coverage = _read_csv(run_path / "TIPICO_COVERAGE.csv")
        if coverage:
            st.dataframe(coverage, hide_index=True, width="stretch")
        audit_path = run_path / "TIPICO_DATA_AUDIT.md"
        if audit_path.is_file():
            with st.expander("Audit anzeigen", expanded=False):
                st.markdown(audit_path.read_text(encoding="utf-8"))
    with patterns:
        st.subheader("P1 gegenüber tatsächlichem HZ2-Ergebnis")
        p1 = _read_csv(run_path / "TIPICO_P1_CALIBRATION.csv")
        if p1:
            st.dataframe(p1, hide_index=True, width="stretch")
        pattern = _read_csv(run_path / "TIPICO_PATTERN_BUCKETS.csv")
        if pattern:
            st.subheader("Feste Musterbereiche")
            st.dataframe(pattern, hide_index=True, width="stretch")
            dimensions = sorted({str(row.get("dimension") or "") for row in pattern if row.get("dimension")})
            chosen_dimension = st.selectbox("Drill-down Dimension", dimensions, key="tipico-backtest-pattern-dimension")
            dimension_rows = [row for row in pattern if row.get("dimension") == chosen_dimension]
            chosen_bucket = st.selectbox(
                "Bereich",
                dimension_rows,
                format_func=lambda row: str(row.get("bucket") or "—"),
                key="tipico-backtest-pattern-bucket",
            )
            dataset_rows = _read_parquet(run_path / "TIPICO_BACKTEST_DATASET.parquet")
            matching = [row for row in dataset_rows if _matches_bucket(row, chosen_bucket)]
            if matching:
                display = [{
                    "Event": row.get("event_id"),
                    "Spiel": f"{row.get('home_team') or '—'} – {row.get('away_team') or '—'}",
                    "Land": row.get("competition_country") or "—",
                    "Liga": row.get("competition_name") or "—",
                    "P1": _percent(row.get("p1_market")),
                    "HZ": f"{row.get('ht_score_home', '—')}:{row.get('ht_score_away', '—')}",
                    "Entry": "Ja" if row.get("entry_eligible") else "Nein",
                    "Ergebnis": row.get("outcome_class") or "UNRESOLVED",
                    "Grund": row.get("primary_reject_reason") or "—",
                    "Evidenz": row.get("evidence_level") or "—",
                } for row in matching]
                st.caption(f"{len(matching)} Beobachtungen im Bereich; ungelöste Ergebnisse bleiben sichtbar und werden nicht als Verlust gezählt.")
                st.dataframe(display[:1000], hide_index=True, width="stretch")
            else:
                st.info("Keine Beobachtung in diesem festen Bereich.")
    with variants:
        st.subheader("Variantenvergleich")
        result_rows = _read_csv(run_path / "TIPICO_VARIANT_RESULTS.csv")
        if result_rows:
            st.dataframe(result_rows, hide_index=True, width="stretch")
        reference_rows = _read_csv(run_path / "TIPICO_VARIANT_REFERENCE_RESULTS.csv")
        if reference_rows:
            with st.expander("Referenzvergleich auf identischer Teilmenge", expanded=False):
                st.dataframe(reference_rows, hide_index=True, width="stretch")
        breakdown_rows = _read_csv(run_path / "TIPICO_VARIANT_BREAKDOWN.csv")
        if breakdown_rows:
            with st.expander("Drill-down nach Datum, Land und Wettbewerb", expanded=False):
                st.dataframe(breakdown_rows, hide_index=True, width="stretch")
        st.caption("Ranglisten bleiben explorativ. Unter 100 unterschiedlichen Spielen wird kein automatischer Paper-Kandidat erzeugt.")
    with evidence:
        st.subheader("Backtest-Spielbelege")
        trade_rows = _read_parquet(run_path / "TIPICO_BACKTEST_TRADES.parquet")
        if not trade_rows:
            st.info("Für den aktuellen Lauf liegen keine auflösbaren Backtest-Trades vor.")
        else:
            variant_options = sorted({str(row.get("variant_id")) for row in trade_rows})
            selected_variant = st.selectbox("Variante", ["Alle"] + variant_options, key="tipico-backtest-variant")
            country_options = sorted({str(row.get("competition_country") or "Unbekannt") for row in trade_rows})
            selected_country = st.selectbox("Land", ["Alle"] + country_options, key="tipico-backtest-country")
            competition_options = sorted({str(row.get("competition_name") or "Unbekannt") for row in trade_rows})
            selected_competition = st.selectbox("Wettbewerb", ["Alle"] + competition_options, key="tipico-backtest-competition")
            shown = [
                row for row in trade_rows
                if (selected_variant == "Alle" or str(row.get("variant_id")) == selected_variant)
                and (selected_country == "Alle" or str(row.get("competition_country") or "Unbekannt") == selected_country)
                and (selected_competition == "Alle" or str(row.get("competition_name") or "Unbekannt") == selected_competition)
            ]
            st.caption(f"{len(shown)} Belege in der aktuellen Auswahl")
            st.dataframe(shown[:500], hide_index=True, width="stretch")
    status = run_path / "TIPICO_BACKTEST_STATUS.md"
    candidates = run_path / "PAPER_CANDIDATES.json"
    download_columns = st.columns(2)
    if status.is_file():
        download_columns[0].download_button("Status herunterladen", status.read_bytes(), file_name="TIPICO_BACKTEST_STATUS.md", key="tipico-backtest-status-download")
    if candidates.is_file():
        download_columns[1].download_button("Paper-Kandidaten herunterladen", candidates.read_bytes(), file_name="PAPER_CANDIDATES.json", key="tipico-backtest-candidates-download")
