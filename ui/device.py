"""Automatic device detection and responsive Streamlit helpers."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st


MOBILE_MARKERS = (
    "android", "iphone", "ipad", "ipod", "mobile", "windows phone",
    "blackberry", "opera mini", "iemobile",
)


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    is_mobile: bool
    source: str
    user_agent: str

    @property
    def label(self) -> str:
        return "Mobil · kompakte Ansicht" if self.is_mobile else "Desktop · breite Ansicht"


def _query_override() -> str | None:
    try:
        value = st.query_params.get("view")
    except Exception:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    resolved = str(value or "").strip().casefold()
    if resolved in {"mobile", "phone", "handy"}:
        return "mobile"
    if resolved in {"desktop", "wide"}:
        return "desktop"
    return None


def detect_device() -> DeviceInfo:
    """Use an explicit URL override first, otherwise the browser User-Agent."""

    override = _query_override()
    if override:
        return DeviceInfo(override == "mobile", "URL-Override", "")
    user_agent = ""
    try:
        headers = st.context.headers
        user_agent = str(headers.get("User-Agent") or headers.get("user-agent") or "")
    except Exception:
        # Streamlit versions without st.context still receive the responsive
        # CSS below; only the informational device label becomes unknown.
        pass
    lowered = user_agent.casefold()
    if user_agent:
        return DeviceInfo(
            any(marker in lowered for marker in MOBILE_MARKERS),
            "Browser User-Agent",
            user_agent,
        )
    return DeviceInfo(False, "Responsive CSS fallback", user_agent)


def apply_responsive_style() -> None:
    """Keep the same app usable on narrow Proxmox/LXC browser screens."""

    st.markdown(
        """
        <style>
        @media (max-width: 700px) {
            .block-container { padding: 0.8rem 0.55rem 2rem 0.55rem; }
            [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: 0.45rem; }
            [data-testid="stMetric"] { min-width: 8.4rem; }
            [data-testid="stDataFrame"] { max-width: 100%; overflow-x: auto; }
            [data-testid="stExpander"] { width: 100%; }
            h1 { font-size: 1.55rem; }
            h2 { font-size: 1.25rem; }
            h3 { font-size: 1.05rem; }
            button { min-height: 2.35rem; }
        }
        .paper-card {
            border: 1px solid rgba(128, 128, 128, .28);
            border-radius: .55rem;
            padding: .65rem .75rem;
            margin: .35rem 0;
        }
        .paper-card small { color: #777; }
        </style>
        """,
        unsafe_allow_html=True,
    )
