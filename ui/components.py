"""Small escaped HTML components; no provider text is trusted as markup."""
from html import escape
import streamlit as st


def text(value) -> str:
    return escape(str(value if value is not None else "—"), quote=True)


def scroll_to_top() -> None:
    """Only on a navigation action, never on a polling or input rerun."""
    markup = '''<script>
    const main = window.parent.document.querySelector('[data-testid="stMain"]');
    if (main) main.scrollTo({top: 0, behavior: 'instant'});
    </script>'''
    if hasattr(st, "iframe"):
        # st.iframe (unlike legacy components.html) rejects height=0.
        # This helper runs before the detail tabs: an invalid size aborts
        # ALL first-click entrypoints, including quotes and FotMob.
        st.iframe(markup, height=1, tab_index=-1)
    else:
        import streamlit.components.v1 as components
        components.html(markup, height=1, tab_index=-1)


def table(rows: list[dict]) -> None:
    if not rows:
        st.caption("Keine Daten vorhanden.")
        return
    headers = list(rows[0])
    markup = '<div class="w-table-wrap"><table class="w-table"><thead><tr>'
    markup += "".join(f"<th>{text(key)}</th>" for key in headers) + "</tr></thead><tbody>"
    for row in rows:
        markup += "<tr>" + "".join(f"<td>{text(row.get(key))}</td>" for key in headers) + "</tr>"
    st.markdown(markup + "</tbody></table></div>", unsafe_allow_html=True)
