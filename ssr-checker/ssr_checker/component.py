"""Renders the synced-scroll side-by-side comparison inside Streamlit."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_TEMPLATE_PATH = Path(__file__).with_name("compare_component.html")
_TEMPLATE = _TEMPLATE_PATH.read_text(encoding="utf-8")

# ``st.components.v1.html`` is deprecated in favour of ``st.iframe``. Prefer the
# modern API when present and fall back for older Streamlit releases.
_IFRAME = getattr(st, "iframe", None)


def _embed_html(html: str, height: int) -> None:
    if _IFRAME is not None:
        _IFRAME(html, height=height)
    else:
        components.html(html, height=height, scrolling=False)


def _img(data_b64: str, css_class: str, alt: str) -> str:
    if not data_b64:
        return f'<div class="empty">{alt} - no screenshot available</div>'
    return (
        f'<img class="{css_class}" alt="{alt}" '
        f'src="data:image/png;base64,{data_b64}" />'
    )


def _pill(status, error) -> str:
    if error:
        return "error"
    if status is None:
        return "-"
    return f"HTTP {status}"


def render_compare(
    ssr_b64: str,
    csr_b64: str,
    diff_b64: str,
    ssr_status=None,
    csr_status=None,
    ssr_error=None,
    csr_error=None,
    *,
    height: int = 720,
) -> None:
    """Embed the two-pane comparison component."""

    ssr_body = _img(ssr_b64, "base", "Server-rendered screenshot")
    csr_body = (
        _img(csr_b64, "base", "Client-rendered screenshot")
        + _img(diff_b64, "swap", "Difference overlay")
    )

    html = (
        _TEMPLATE.replace("__HEIGHT__", str(height))
        .replace("__SSR_BODY__", ssr_body)
        .replace("__CSR_BODY__", csr_body)
        .replace("__SSR_PILL__", _pill(ssr_status, ssr_error))
        .replace("__CSR_PILL__", _pill(csr_status, csr_error))
    )
    _embed_html(html, height=height + 48)
