"""FotMob-only UI surfaces; no Tipico odds or strategy decisions live here."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import streamlit as st

from fotmob.service import FotMobRefreshResult, FotMobService


def _display(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", ",")
    return str(value)


def _score_label(home: Any, away: Any) -> str:
    if home is None and away is None:
        return "—"
    return f"{_display(home)}:{_display(away)}"


def _stats_rows(state: Any, column: str = "stats_json") -> list[dict[str, str]]:
    if state is None:
        return []
    try:
        stats = json.loads(str(state[column]))
    except (KeyError, TypeError, ValueError):
        stats = {}
    labels = (
        ("xG", "xg_home", "xg_away"),
        ("Schüsse", "shots_home", "shots_away"),
        ("Schüsse aufs Tor", "shots_on_target_home", "shots_on_target_away"),
        ("Großchancen", "big_chances_home", "big_chances_away"),
        ("Ecken", "corners_home", "corners_away"),
        ("Ballbesitz (%)", "possession_home", "possession_away"),
        ("Gelbe Karten", "yellow_cards_home", "yellow_cards_away"),
        ("Rote Karten", "red_cards_home", "red_cards_away"),
    )
    return [
        {
            "Statistik": label,
            "Heim": _display(stats.get(home_key)),
            "Auswärts": _display(stats.get(away_key)),
        }
        for label, home_key, away_key in labels
        if stats.get(home_key) is not None or stats.get(away_key) is not None
    ]


def _render_result(result: FotMobRefreshResult) -> None:
    if result.success:
        st.success(
            "FotMob aktualisiert"
            + (f" · Snapshot {result.snapshot_id}" if result.snapshot_id else "")
        )
        return
    if result.match_result is not None:
        st.warning(
            f"Matching: {result.match_result.status} · "
            f"Confidence {result.match_result.confidence:.2f} · "
            f"{'; '.join(result.match_result.reasons)}"
        )
        if result.match is not None:
            st.session_state["fotmob_last_candidate"] = result.match
    elif result.error:
        st.error(result.error)


def render_fotmob_tab(service: FotMobService, event: Any) -> None:
    """Render only provider status and football statistics, never betting value."""

    st.subheader("FotMob Enrichment")
    st.caption(
        "Optionale zweite Datenquelle. FotMob-Werte beeinflussen keine Quoten, "
        "kein Ranking und keinen Paper-Trade. "
        f"Entscheidung: {service.provider_decision} · Automatisierung: {service.automated_usage}."
    )
    internal_match_id = service.ensure_tipico_event(event)
    link = service.store.link_for_internal(internal_match_id)
    current = service.store.current_state(internal_match_id)
    quality = service.store.quality(internal_match_id)

    status_columns = st.columns(4)
    status_columns[0].metric("Feature", "AN" if service.enabled else "AUS")
    status_columns[1].metric("Matching", link["match_status"] if link else "—")
    status_columns[2].metric(
        "Confidence",
        f"{float(link['match_confidence']):.2f}" if link else "—",
    )
    status_columns[3].metric("FotMob-ID", link["provider_match_id"] if link else "—")

    can_load = service.enabled and service.manual_use_allowed
    if not service.enabled:
        st.info(
            "FotMob-Netzwerkzugriff ist deaktiviert. Bereits gespeicherte Daten "
            "werden weiterhin nur lesend angezeigt."
        )
    elif not service.manual_use_allowed:
        st.warning("FotMob-Einzelspielnutzung ist durch die aktuelle Provider-Policy deaktiviert.")
    else:
        st.info(
            "FotMob ist nur für ein ausdrücklich ausgewähltes Einzelspiel aktiv. "
            "Der periodische Worker bleibt bei dieser Provider-Entscheidung deaktiviert."
        )

    if can_load:
        provider_id = st.text_input(
            "FotMob Match-ID",
            value=str(link["provider_match_id"]) if link else "",
            key=f"fotmob-provider-id-{event.event_id}",
            help="Die numerische ID aus der FotMob-Match-URL, z. B. aus dem Fragment #5881143.",
        )
        if st.button("FotMob laden und deterministisch prüfen", key=f"fotmob-load-{event.event_id}"):
            if not provider_id.strip():
                st.warning("Bitte zuerst eine FotMob Match-ID angeben.")
            else:
                with st.spinner("FotMob-Match wird gelesen …"):
                    result = service.discover_and_match(event, provider_id.strip())
                if result.success:
                    st.success("FotMob-Match erfolgreich gelesen und verknüpft.")
                else:
                    _render_result(result)

        candidate = st.session_state.get("fotmob_last_candidate")
        if candidate is not None and (
            link is None or link["match_status"] not in {"MANUALLY_CONFIRMED", "EXACT", "HIGH_CONFIDENCE"}
        ):
            st.warning(
                f"Kandidat: {candidate.home_team} – {candidate.away_team} · "
                f"{candidate.competition_name or 'Liga unbekannt'} · "
                f"FotMob-ID {candidate.provider_match_id}"
            )
            confirm_col, reject_col = st.columns(2)
            if confirm_col.button("Kandidat manuell bestätigen", key=f"fotmob-confirm-{event.event_id}"):
                service.confirm_manual(event, candidate)
                st.session_state.pop("fotmob_last_candidate", None)
                st.success("FotMob-Match manuell bestätigt.")
                st.rerun()
            if reject_col.button("Kandidat ablehnen", key=f"fotmob-reject-{event.event_id}"):
                service.reject_match(event, candidate.provider_match_id)
                st.session_state.pop("fotmob_last_candidate", None)
                st.info("Kandidat als abgelehnt gespeichert.")
                st.rerun()

    # A successful load updates Current State in the same interaction.  Read
    # the row again so the tab does not show one stale render cycle.
    link = service.store.link_for_internal(internal_match_id)
    current = service.store.current_state(internal_match_id)
    quality = service.store.quality(internal_match_id)

    if current is None:
        st.info("Noch kein FotMob-Current-State für dieses Event.")
        return
    state_columns = st.columns(5)
    state_columns[0].metric("Status", current["status"] or "—")
    state_columns[1].metric("Minute", current["minute"] if current["minute"] is not None else "—")
    state_columns[2].metric("Stand", _score_label(current["score_home"], current["score_away"]))
    state_columns[3].metric("HZ", _score_label(current["ht_score_home"], current["ht_score_away"]))
    state_columns[4].metric("Letztes Update", current["updated_at"] or "—")
    rows = _stats_rows(current, "stats_json")
    if rows:
        st.caption("FotMob Match / All")
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Für diesen Current-State sind noch keine normierten FotMob-Statistiken vorhanden.")

    first_half_rows = _stats_rows(current, "ht_stats_json")
    st.subheader("FotMob FirstHalf")
    st.caption(
        "Ausschließlich `content.stats.Periods.FirstHalf`; FotMob All/SecondHalf wird nicht in diese HZ-Werte übernommen."
    )
    if first_half_rows:
        st.dataframe(first_half_rows, hide_index=True, width="stretch")
    else:
        st.info("Noch keine FirstHalf-Statistiken gespeichert.")
    st.caption(
        f"Provider: {current['provider'] or 'FOTMOB'} · FotMob-ID: {current['provider_match_id']} · "
        f"Tipico-ID: {current['tipico_event_id'] or event.event_id} · "
        f"stats_period: {current['stats_period'] or '—'} · "
        f"source_context: {current['source_context'] or '—'} · "
        f"captured_live: {'ja' if current['captured_live'] else 'nein'} · "
        f"captured_at: {current['observed_at'] or '—'}"
    )
    if quality is not None:
        st.caption(
            f"Result consistency: {quality['result_consistency'] or '—'} · "
            f"HT consistency: {quality['ht_consistency'] or '—'} · "
            f"HT-Stats verfügbar: {'ja' if quality['fotmob_ht_stats_available'] else 'nein'}"
        )


def render_fotmob_debug(service: FotMobService) -> None:
    """Read-only data/access debugger for V0.5."""

    st.subheader("FotMob Access & Matching")
    metrics = service.metrics()
    columns = st.columns(5)
    columns[0].metric("Feature", "AN" if service.enabled else "AUS")
    columns[1].metric("Matches", metrics["matches"])
    columns[2].metric("Links", metrics["links"])
    columns[3].metric("Current", metrics["current_state"])
    columns[4].metric("Snapshots", metrics["snapshots"])
    st.caption(
        f"Provider-Entscheidung: {metrics['provider_decision']} · "
        f"Automatisierung: {metrics['automated_usage']} · "
        f"Einzelspiel: {'AN' if metrics['manual_use_allowed'] else 'AUS'} · "
        f"Worker: {'AN' if metrics['automated_worker_allowed'] else 'AUS'}"
    )
    _render_historical_date_loader(service)
    st.subheader("FotMob Access")
    access = metrics.get("access", {}) or {}
    rate_control = access.get("rate_control", {}) or {}
    performance_configuration = metrics.get("performance_configuration", {}) or {}
    access_columns = st.columns(6)
    access_columns[0].metric("Current RPS", _display(access.get("current_rps", rate_control.get("current_rps"))))
    access_columns[1].metric("Effective RPS", _display(access.get("effective_rps")))
    access_columns[2].metric(
        "Workers",
        f"{performance_configuration.get('initial_workers', '—')} / {performance_configuration.get('max_workers', '—')}",
    )
    access_columns[3].metric("Requests", access.get("requests", 0))
    access_columns[4].metric("Success Rate", f"{float(access.get('success_rate', 0.0)) * 100:.1f}%")
    access_columns[5].metric("429 / Retries", f"{access.get('429', access.get('rate_limit_responses', 0))} / {access.get('retries', 0)}")
    latency_columns = st.columns(5)
    latency_columns[0].metric("Median ms", _display(access.get("median_response_ms")))
    latency_columns[1].metric("P95 ms", _display(access.get("p95_response_ms")))
    latency_columns[2].metric("Mode", rate_control.get("mode", performance_configuration.get("rate_mode", "—")))
    latency_columns[3].metric("Stable max RPS", _display(metrics.get("known_stable_max_rps")))
    latency_columns[4].metric("Max RPS config", _display(performance_configuration.get("max_rps")))
    st.caption(
        "Runtime-Grenzen: "
        f"Mode={rate_control.get('mode', performance_configuration.get('rate_mode', '—'))} · "
        f"Initial/Max RPS={performance_configuration.get('initial_rps', '—')}/"
        f"{performance_configuration.get('max_rps', '—')} · "
        f"Worker Initial/Max={performance_configuration.get('initial_workers', '—')}/"
        f"{performance_configuration.get('max_workers', '—')} · "
        f"Netzwerk={metrics.get('network_mode', '—')} · "
        f"Letzter erfolgreicher Request={access.get('last_success_at', '—')}"
    )
    st.caption("Verfügbare Performance-Modi: ADAPTIVE · FIXED · CONSERVATIVE")
    profiles = metrics.get("performance_profiles", []) or []
    if profiles:
        st.caption("Persistierte V0.5.6-Performance-Profile (zuletzt gemessene Stufen):")
        st.dataframe(
            [
                {
                    "Phase": row.get("phase"),
                    "RPS": row.get("rps"),
                    "Worker": row.get("workers"),
                    "Requests": row.get("requests"),
                    "Success": row.get("success_rate"),
                    "429": row.get("http_429"),
                    "Retries": row.get("retries"),
                    "Median ms": row.get("median_latency_ms"),
                    "P95 ms": row.get("p95_latency_ms"),
                    "Status": row.get("status"),
                }
                for row in profiles
            ],
            hide_index=True,
            width="stretch",
        )
    st.write(
        {
            "FotMob access": access,
            "Letzter erfolgreicher Request": access.get("last_success_at", "—"),
        }
    )
    st.subheader("Provider Matching")
    st.write(
        {
            "HT-Stats": metrics["ht_stats"],
            "xG verfügbar": metrics["xg_available"],
            "Big Chances verfügbar": metrics["big_chances_available"],
            "Matching geprüft": metrics["matches_considered"],
            "Auto-Link-Rate": f"{metrics['automatic_match_rate'] * 100:.1f}%",
            "Matching-Status": metrics["matching_status"],
        }
    )
    st.subheader("FotMob Storage / Data Quality")
    st.write(
        {
            "Outbox pending": metrics["outbox_pending"],
            "Archive": str(service.archive.snapshot_root),
            "Letzter Servicefehler": service.last_error or "—",
        }
    )
    rows = service.store.debug_rows()
    if rows:
        st.dataframe(
            [
                {
                    "Tipico Event": row["tipico_event_id"] or "—",
                    "Spiel": f"{row['home_team']} – {row['away_team']}",
                    "Liga/Land": f"{row['competition_name'] or '—'} / {row['competition_country'] or '—'}",
                    "FotMob-ID": row["provider_match_id"] or "—",
                    "Match": row["match_status"] or "—",
                    "Confidence": row["match_confidence"],
                    "Current": row["observed_at"] or "—",
                    "Result": row["result_consistency"] or "—",
                    "HT": row["ht_consistency"] or "—",
                }
                for row in rows
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Noch keine FotMob-Matches verknüpft.")


def _munich_datetime(value: Any) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        return parsed.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return str(value)


def _historical_ht_rows(core: dict[str, Any]) -> list[dict[str, str]]:
    fields = (
        ("xG", "ht_xg_home", "ht_xg_away"),
        ("Schüsse", "ht_shots_home", "ht_shots_away"),
        ("Schüsse aufs Tor", "ht_shots_on_target_home", "ht_shots_on_target_away"),
        ("Großchancen", "ht_big_chances_home", "ht_big_chances_away"),
        ("Ecken", "ht_corners_home", "ht_corners_away"),
        ("Ballbesitz (%)", "ht_possession_home", "ht_possession_away"),
        ("Gelbe Karten", "ht_yellow_cards_home", "ht_yellow_cards_away"),
        ("Rote Karten", "ht_red_cards_home", "ht_red_cards_away"),
        ("Schüsse im Strafraum", "ht_shots_inside_box_home", "ht_shots_inside_box_away"),
        ("Schüsse außerhalb", "ht_shots_outside_box_home", "ht_shots_outside_box_away"),
        ("Ballkontakte im Strafraum", "ht_touches_in_box_home", "ht_touches_in_box_away"),
        ("Pässe", "ht_passes_home", "ht_passes_away"),
        ("Genaue Pässe", "ht_accurate_passes_home", "ht_accurate_passes_away"),
        ("Torwartparaden", "ht_goalkeeper_saves_home", "ht_goalkeeper_saves_away"),
        ("Expected Threat", "ht_expected_threat_home", "ht_expected_threat_away"),
        ("Fouls", "ht_fouls_home", "ht_fouls_away"),
        ("Abseits", "ht_offsides_home", "ht_offsides_away"),
    )
    rows: list[dict[str, str]] = []
    for label, home_key, away_key in fields:
        home = core.get(home_key)
        away = core.get(away_key)
        if home is not None or away is not None:
            rows.append({"Metrik": label, "Heim": _display(home), "Auswärts": _display(away)})
    try:
        extra_stats = json.loads(str(core.get("ht_extra_stats_json") or "{}"))
    except (TypeError, ValueError):
        extra_stats = {}
    if isinstance(extra_stats, dict):
        for label, value in sorted(extra_stats.items(), key=lambda item: str(item[0])):
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                home, away = value[0], value[1]
            else:
                home, away = value, None
            rows.append({"Metrik": str(label), "Heim": _display(home), "Auswärts": _display(away)})
    return rows


def _historical_table_row(row: Any, core: dict[str, Any]) -> dict[str, str]:
    return {
        "Datum": str(row["observation_date"]),
        "Anstoß München": _munich_datetime(row["kickoff_at_utc"]),
        "Folgetag-Eintrag": "ja" if row["is_next_day"] else "nein",
        "Land": row["country_name"] or row["country_code"] or "—",
        "Liga": row["league_name"] or row["league_id"],
        "Saison": row["season_label"] or row["season_id"] or "—",
        "Spiel": f"{row['home_team_name']} – {row['away_team_name']}",
        "Halbzeit": _score_label(core.get("ht_score_home"), core.get("ht_score_away")),
        "HZ xG": _score_label(core.get("ht_xg_home"), core.get("ht_xg_away")),
        "HZ Schüsse": _score_label(core.get("ht_shots_home"), core.get("ht_shots_away")),
        "HZ SOT": _score_label(
            core.get("ht_shots_on_target_home"), core.get("ht_shots_on_target_away")
        ),
        "HZ Ecken": _score_label(core.get("ht_corners_home"), core.get("ht_corners_away")),
        "Endstand": _score_label(core.get("ft_score_home"), core.get("ft_score_away")),
        "FotMob-ID": str(row["fotmob_match_id"]),
    }


def _render_historical_date_loader(service: FotMobService) -> None:
    """Load and inspect every FotMob-listed game for a selected date range."""

    st.subheader("FotMob-Historie laden")
    st.caption(
        "Alle Länder und Ligen aus dem FotMob-Tagesfeed. Jede dort gelistete "
        "Partie wird unabhängig von der Anstoßzeit geprüft; Partien ohne "
        "FirstHalf-Daten werden nicht ins Detailarchiv übernommen. "
        "Index/Katalog liegen in SQLite, Detailmetriken im kanonischen Parquet-Archiv."
    )
    munich_today = datetime.now(ZoneInfo("Europe/Berlin")).date()
    default_from = munich_today - timedelta(days=7)
    with st.form("fotmob-date-range-form", clear_on_submit=False):
        columns = st.columns(2)
        start_date = columns[0].date_input(
            "Von",
            value=default_from,
            key="fotmob-history-from-date",
        )
        end_date = columns[1].date_input(
            "Bis",
            value=munich_today,
            key="fotmob-history-to-date",
        )
        submitted = st.form_submit_button(
            "FotMob-Daten laden",
            type="primary",
            width="stretch",
        )
    if submitted:
        try:
            with st.spinner("FotMob-Tagesdaten für alle Länder und Ligen werden geladen …"):
                result = service.history_pipeline.load_date_range(
                    start_date,
                    end_date,
                    fetch_details=True,
                    workers=service.settings.fotmob_history_workers,
                    execution_mode="manual",
                )
            st.session_state["fotmob_last_date_load"] = result
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            st.session_state["fotmob_last_date_load"] = {
                "status": "ERROR",
                "from_date": str(start_date),
                "to_date": str(end_date),
                "error": str(exc),
            }

    last_result = st.session_state.get("fotmob_last_date_load")
    if isinstance(last_result, dict):
        status = str(last_result.get("status", ""))
        if status == "PASS":
            scope = "alle Länder/Ligen" if last_result.get("scope") == "ALL_LEAGUES" else "die ausgewählte Liga"
            st.success(
                f"FotMob abgeschlossen: {last_result.get('unique_fixtures', last_result.get('fixtures', 0))} "
                f"einzigartige Spiele für {last_result.get('from_date', '—')} bis "
                f"{last_result.get('to_date', '—')} ({scope})."
            )
        elif status == "BLOCKED_BY_POLICY":
            st.warning(str(last_result.get("error") or "FotMob-Historie ist durch die Konfiguration blockiert."))
        else:
            errors = last_result.get("errors", [])
            st.warning(
                f"FotMob-Lauf {status or 'FEHLER'}: "
                f"{last_result.get('error') or '; '.join(str(item) for item in errors) or 'siehe Details'}"
            )
        warnings = last_result.get("warnings", [])
        if warnings:
            st.caption("Hinweis: " + "; ".join(str(item) for item in warnings))
        details = last_result.get("details") or {}
        detail_columns = st.columns(5)
        detail_columns[0].metric("Index-Einträge", last_result.get("daily_index_rows", 0))
        detail_columns[1].metric("Spiele", last_result.get("unique_fixtures", last_result.get("fixtures", 0)))
        detail_columns[2].metric("HZ-Daten geladen", details.get("fetched", 0))
        detail_columns[3].metric("Ohne HZ übersprungen", details.get("skipped_no_halftime", 0))
        detail_columns[4].metric("Period-Stats", details.get("period_stats_rows", 0))

    loaded_from = str(last_result.get("from_date")) if isinstance(last_result, dict) and last_result.get("from_date") else default_from.isoformat()
    loaded_to = str(last_result.get("to_date")) if isinstance(last_result, dict) and last_result.get("to_date") else munich_today.isoformat()
    rows = service.history_pipeline.store.daily_index(
        start_date=loaded_from,
        end_date=loaded_to,
        limit=20000,
        order_by="observation_date",
        ascending=False,
    )
    if not rows:
        st.info("Noch kein FotMob-Tagesindex für diesen Zeitraum geladen.")
        return

    country_options = sorted({str(row["country_name"] or row["country_code"]) for row in rows if row["country_name"] or row["country_code"]})
    league_options = sorted({str(row["league_name"] or row["league_id"]) for row in rows if row["league_name"] or row["league_id"]})
    season_options = sorted({str(row["season_label"] or row["season_id"]) for row in rows if row["season_label"] or row["season_id"]}, reverse=True)
    filter_columns = st.columns(3)
    country_filter = filter_columns[0].selectbox("Land", ["Alle"] + country_options, key="fotmob-history-country-filter")
    league_filter = filter_columns[1].selectbox("Liga", ["Alle"] + league_options, key="fotmob-history-league-filter")
    season_filter = filter_columns[2].selectbox("Saison", ["Alle"] + season_options, key="fotmob-history-season-filter")

    filtered_rows = [
        row
        for row in rows
        if (country_filter == "Alle" or str(row["country_name"] or row["country_code"]) == country_filter)
        and (league_filter == "Alle" or str(row["league_name"] or row["league_id"]) == league_filter)
        and (season_filter == "Alle" or str(row["season_label"] or row["season_id"]) == season_filter)
    ]
    skipped_count = sum(row["detail_status"] == "SKIPPED_NO_HALFTIME" for row in filtered_rows)
    detail_rows: list[tuple[Any, dict[str, Any]]] = []
    for row in filtered_rows:
        if row["detail_status"] not in {"FETCHED", "PARTIAL"} or not row["canonical_archive_path"]:
            continue
        core = service.history_pipeline.canonical_archive.read_match_core(row["canonical_archive_path"])
        if core is not None and core.get("ht_score_home") is not None and core.get("ht_score_away") is not None:
            detail_rows.append((row, core))

    summary_columns = st.columns(4)
    summary_columns[0].metric("Index gefiltert", len(filtered_rows))
    summary_columns[1].metric("Mit Halbzeitdaten", len(detail_rows))
    summary_columns[2].metric("Ohne Halbzeitdaten", skipped_count)
    summary_columns[3].metric("Länder / Ligen", f"{len({row['country_code'] for row in filtered_rows})} / {len({row['league_id'] for row in filtered_rows})}")

    if detail_rows:
        st.caption("Gespeicherte Spiele mit FirstHalf-Daten. Die Tabelle ist nach jeder Spalte sortierbar.")
        st.dataframe(
            [_historical_table_row(row, core) for row, core in detail_rows],
            hide_index=True,
            width="stretch",
        )
        detail_by_id = {str(row["fotmob_match_id"]): (row, core) for row, core in detail_rows}
        selected_id = st.selectbox(
            "Spiel für Halbzeitdetails",
            list(detail_by_id),
            format_func=lambda match_id: (
                f"{detail_by_id[match_id][0]['home_team_name']} – "
                f"{detail_by_id[match_id][0]['away_team_name']} · {match_id}"
            ),
            key="fotmob-history-selected-match",
        )
        selected_row, selected_core = detail_by_id[selected_id]
        st.subheader(
            f"Halbzeitdaten · {selected_row['home_team_name']} – {selected_row['away_team_name']}"
        )
        st.dataframe(_historical_ht_rows(selected_core), hide_index=True, width="stretch")
        st.caption(
            f"Land: {selected_row['country_name'] or selected_row['country_code'] or '—'} · "
            f"Liga: {selected_row['league_name'] or selected_row['league_id']} · "
            f"Saison: {selected_row['season_label'] or selected_row['season_id'] or '—'} · "
            f"FotMob-ID: {selected_id} · Archiv: {selected_row['canonical_archive_path']}"
        )
    elif skipped_count:
        st.info("Für die aktuelle Auswahl existieren nur Spiele ohne FotMob-FirstHalf-Daten; sie wurden bewusst übersprungen.")
    else:
        st.info("Für die aktuelle Auswahl sind noch keine vollständigen Halbzeitdetails archiviert.")
