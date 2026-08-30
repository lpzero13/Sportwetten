"""FotMob-only UI surfaces; no Tipico odds or strategy decisions live here."""

from __future__ import annotations

import json
from typing import Any

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

    if not service.enabled:
        st.info("FotMob ist deaktiviert. Für die optionale Nutzung FOTMOB_ENABLED=true setzen.")
        return
    if not service.manual_use_allowed:
        st.warning("FotMob-Einzelspielnutzung ist durch die aktuelle Provider-Policy deaktiviert.")
        return
    st.info(
        "V0.5.1: FotMob ist nur für ein ausdrücklich ausgewähltes Einzelspiel aktiv. "
        "Der periodische Worker bleibt bei dieser Provider-Entscheidung deaktiviert."
    )

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
    st.subheader("FotMob Access")
    st.write(
        {
            "FotMob access": metrics.get("access", {}),
            "Letzter erfolgreicher Request": metrics.get("access", {}).get("last_success_at", "—"),
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
