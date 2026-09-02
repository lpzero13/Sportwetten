"""Volatile FotMob live panel for the currently selected Tipico match."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from fotmob.live import LIVE_STAT_KEYS, STAT_LABELS, FotMobLiveData, FotMobLiveResult, FotMobLiveService


def _age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def _age_label(value: str | None) -> str:
    age = _age_seconds(value)
    if age is None:
        return "—"
    return f"{age:.1f} s" if age < 10 else f"{age:.0f} s"


def _display(value: Any, key: str) -> str:
    if value is None:
        return "—"
    if key == "possession":
        return f"{float(value):g}%"
    if key in {"xg", "xgot"}:
        return f"{float(value):.2f}"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}"
    return str(int(value)) if isinstance(value, (int, float)) else str(value)


def _score(home: int | None, away: int | None) -> str:
    if home is None and away is None:
        return "—"
    return f"{'—' if home is None else home}:{'—' if away is None else away}"


def _pair(stats: dict[str, tuple[Any, Any]] | None, key: str) -> tuple[Any, Any]:
    if not isinstance(stats, dict):
        return None, None
    value = stats.get(key)
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None, None
    return value[0], value[1]


def _stats_table(stats: dict[str, tuple[Any, Any]] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in LIVE_STAT_KEYS:
        home, away = _pair(stats, key)
        rows.append(
            {
                "Statistik": STAT_LABELS[key],
                "Heim": _display(home, key),
                "Auswärts": _display(away, key),
            }
        )
    return rows


def _has_period_data(data: FotMobLiveData) -> bool:
    return any(
        pair[0] is not None or pair[1] is not None
        for stats in data.periods.values()
        if stats is not None
        for pair in stats.values()
    )


def _render_summary(data: FotMobLiveData, result: FotMobLiveResult) -> None:
    columns = st.columns(4)
    columns[0].metric("Status", data.match_status or "—")
    minute = "—" if data.minute is None else f"{data.minute}'"
    if data.added_time:
        minute += f"+{data.added_time}"
    columns[1].metric("Minute", minute)
    columns[2].metric("Stand", _score(data.home_score, data.away_score))
    columns[3].metric("Datenalter", _age_label(data.fetched_at))
    st.caption(
        f"{data.home_team} – {data.away_team} · "
        f"{data.competition_name or 'Liga unbekannt'} · "
        f"{data.competition_country or 'Land unbekannt'}"
    )
    if result.status == "FINISHED":
        st.caption("Auto Refresh: OFF · Spiel beendet")


def _render_available_data(data: FotMobLiveData, result: FotMobLiveResult, event_id: str) -> None:
    _render_summary(data, result)
    period_key = "ALL"
    if _has_period_data(data):
        period_key = st.selectbox(
            "FotMob-Periode",
            options=("ALL", "FIRST_HALF", "SECOND_HALF"),
            format_func={
                "ALL": "Gesamt",
                "FIRST_HALF": "1. Halbzeit",
                "SECOND_HALF": "2. Halbzeit",
            }.get,
            key=f"fotmob-live-period-{event_id}-{data.provider_match_id}",
        )
    period_stats = data.periods.get(period_key) or {}
    st.dataframe(_stats_table(period_stats), hide_index=True, width="stretch")

    st.subheader("Letzte 15 Minuten")
    if not data.shotmap_available:
        st.info("Für dieses Spiel sind keine ausreichenden Shotmap-Daten verfügbar.")
    else:
        st.dataframe(
            [
                {
                    "Metrik": "xG",
                    "Heim": _display(_pair(data.last_15, "xg")[0], "xg"),
                    "Auswärts": _display(_pair(data.last_15, "xg")[1], "xg"),
                },
                {
                    "Metrik": "Schüsse",
                    "Heim": _display(_pair(data.last_15, "shots")[0], "shots"),
                    "Auswärts": _display(_pair(data.last_15, "shots")[1], "shots"),
                },
                {
                    "Metrik": "Schüsse aufs Tor",
                    "Heim": _display(_pair(data.last_15, "shots_on_target")[0], "shots_on_target"),
                    "Auswärts": _display(_pair(data.last_15, "shots_on_target")[1], "shots_on_target"),
                },
            ],
            hide_index=True,
            width="stretch",
        )


def render_fotmob_live_panel(service: FotMobLiveService, event: Any | None) -> None:
    """Render one selected-match panel without touching persistent storage."""

    st.subheader("FotMob Live")
    event_id = getattr(event, "event_id", None) if event is not None else None
    if event_id is None:
        st.info("FotMob Live: Kein Live-Spiel ausgewählt.")
        return
    event_id = str(event_id)
    enabled_key = f"fotmob-live-enabled-{event_id}"
    auto_key = f"fotmob-live-auto-{event_id}"
    stop_key = f"fotmob-live-stop-{event_id}"
    if enabled_key not in st.session_state:
        st.session_state[enabled_key] = True
    if auto_key not in st.session_state:
        st.session_state[auto_key] = True

    cached = service.cached_for_event(event)
    if cached is not None and cached.status in {"NO_DATA", "FINISHED"}:
        st.session_state[auto_key] = False
    if st.session_state.pop(stop_key, False):
        st.session_state[auto_key] = False

    provider_id = service.provider_match_id_for_event(event)
    manual_live_result: FotMobLiveResult | None = None
    if provider_id is None and service.manual_use_allowed:
        st.caption(
            "Für dieses Spiel wurde noch keine FotMob-Zuordnung gefunden. "
            "Du kannst die Match-ID aus der FotMob-URL einmalig für dieses "
            "Spiel prüfen; die Zuordnung bleibt nur in dieser Sitzung im RAM."
        )
        manual_id = st.text_input(
            "FotMob Match-ID (optional)",
            key=f"fotmob-live-manual-id-{event_id}",
            placeholder="z. B. 6003655 aus #6003655",
            help=(
                "Die numerische ID steht am Ende der FotMob-Match-URL, "
                "zum Beispiel #6003655. Du kannst auch die komplette URL einfügen."
            ),
        )
        if st.button(
            "FotMob-Match prüfen und anzeigen",
            key=f"fotmob-live-bind-{event_id}",
            type="secondary",
            width="stretch",
        ):
            with st.spinner("FotMob-Match wird geprüft …"):
                binding = service.bind_manual_match_id(event, manual_id)
            if binding.success:
                provider_id = binding.provider_match_id
                manual_live_result = binding.live_result
                st.success(
                    f"FotMob-Match {binding.provider_match_id} zugeordnet · "
                    f"Matching {binding.match_status or 'bestätigt'}"
                )
            else:
                st.warning(binding.error or "FotMob-Match konnte nicht zugeordnet werden.")
                if binding.match_result is not None:
                    st.caption(
                        f"Matching: {binding.match_result.status} · "
                        f"Confidence {binding.match_result.confidence:.2f} · "
                        f"{'; '.join(binding.match_result.reasons) or 'keine passende Begründung'}"
                    )
    terminal = cached is not None and cached.status in {"NO_DATA", "FINISHED"}
    control_columns = st.columns([1.35, 1.35, 1.35, 3.0])
    live_enabled = control_columns[0].checkbox(
        "FotMob Live",
        key=enabled_key,
        help="Aktuelle FotMob-Werte nur für das ausgewählte Spiel anzeigen.",
    )
    auto_refresh = control_columns[1].checkbox(
        "Auto Refresh",
        key=auto_key,
        disabled=terminal,
        help=f"Ausgewähltes Spiel etwa alle {service.refresh_seconds} Sekunden aktualisieren.",
    )
    refresh_now = control_columns[2].button(
        "Refresh now",
        key=f"fotmob-live-refresh-{event_id}",
        disabled=not live_enabled or provider_id is None or terminal,
        width="stretch",
    )
    control_columns[3].caption(
        f"Intervall: {service.refresh_seconds} s · "
        f"FotMob-ID: {provider_id or 'keine bestätigte Zuordnung'}"
    )

    if live_enabled and auto_refresh and provider_id is not None and not terminal:
        st_autorefresh(
            interval=service.refresh_seconds * 1000,
            key=f"fotmob-live-autorefresh-{event_id}",
        )

    if not live_enabled:
        st.info("FotMob Live ist für dieses Spiel deaktiviert.")
        return
    if provider_id is None:
        st.info(
            "FotMob Live: Keine bestätigte Zuordnung vorhanden. "
            "Ohne Match-ID wird kein Provider-Request ausgeführt."
        )
        return

    result = manual_live_result or service.fetch_for_event(
        event,
        force=refresh_now,
        allow_network=auto_refresh or refresh_now,
    )
    if result.status == "DISABLED":
        st.info("FotMob Live ist durch die aktuelle Provider-Konfiguration deaktiviert.")
        return
    if result.status == "NO_DATA":
        st.session_state[stop_key] = True
        st.info(
            "Keine ausführlichen FotMob-Live-Daten verfügbar.\n\n"
            "Dieses Spiel wird nicht weiter automatisch aktualisiert."
        )
        if result.data is not None:
            st.caption("FotMob-Match gefunden · Basisdaten vorhanden · Detailstatistiken nicht verfügbar")
        return
    if result.status == "PENDING":
        st.info(
            "Live-Daten werden geladen …\n\n"
            "Noch keine ausreichenden Matchstatistiken verfügbar."
        )
        if result.data is not None:
            _render_summary(result.data, result)
        return
    if result.status == "ERROR":
        st.warning(
            "FotMob-Aktualisierung fehlgeschlagen. "
            + (f"Nächster Versuch in etwa {result.retry_delay_seconds} s." if result.retry_delay_seconds else "")
        )
        if result.error:
            st.caption(result.error)
        if result.data is None:
            st.info("Noch keine gültigen FotMob-Live-Daten in dieser Sitzung vorhanden.")
            return
    if result.data is None:
        st.info("Für dieses Spiel liegen noch keine FotMob-Live-Daten vor.")
        return

    if result.status == "FINISHED":
        st.caption("Das Spiel ist beendet. Die letzte FotMob-Live-Anzeige bleibt sichtbar.")
    elif result.availability_status == "DETAILED_DATA_AVAILABLE":
        st.caption(
            f"Ausführliche Daten verfügbar · aktualisiert vor {_age_label(result.data.fetched_at)}"
        )
    _render_available_data(result.data, result, event_id)
    with st.expander("FotMob Live technische Details", expanded=False):
        st.json(service.debug_for_event(event))


__all__ = ["render_fotmob_live_panel"]
