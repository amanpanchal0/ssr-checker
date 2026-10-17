"""SSR Checker - a Streamlit port of a side-by-side SSR/CSR comparison tool.

Run with:

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from ssr_checker.component import render_compare
from ssr_checker.devices import DEFAULT_DEVICE_LABEL, DEVICES, get_device
from ssr_checker.diff import build_report
from ssr_checker.renderer import capture, normalize_url, playwright_available

st.set_page_config(
    page_title="SSR Checker",
    page_icon="mag",
    layout="wide",
)

_VERDICT_STYLE = {
    "full": ("#ecfdf3", "#027a48"),
    "mostly": ("#f0f9ff", "#026aa2"),
    "partial": ("#fffaeb", "#b54708"),
    "none": ("#fef3f2", "#b42318"),
    "unknown": ("#f2f4f7", "#475467"),
}


@st.cache_resource(show_spinner=False)
def _check_browser() -> tuple[bool, str]:
    return playwright_available()


@st.cache_data(ttl=600, show_spinner=False)
def _run_check(url: str, device_label: str) -> dict:
    device = get_device(device_label)
    captured = capture(url, device)
    return build_report(captured["ssr"], captured["csr"], device.label)


def _render_summary(report: dict) -> None:
    verdict = report["verdict"]
    background, color = _VERDICT_STYLE.get(verdict["level"], _VERDICT_STYLE["unknown"])

    st.markdown(
        f"""
        <div style="background:{background};border-radius:10px;padding:14px 18px;
                    border:1px solid {color}33;margin-bottom:12px">
          <div style="color:{color};font-size:12px;font-weight:600;
                      letter-spacing:.04em;text-transform:uppercase">SSR verdict</div>
          <div style="color:{color};font-size:20px;font-weight:700;
                      margin-top:2px">{verdict['label']}</div>
          <div style="color:{color};opacity:.85;font-size:13px;margin-top:4px">
            {round(verdict['coverage'] * 100)}% of the JavaScript-rendered text is
            present in the server-rendered HTML.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Device", report["device_label"])
    col2.metric(
        "Server-rendered text",
        f"{verdict['ssr_char_count']:,} chars",
    )
    col3.metric(
        "Client-rendered text",
        f"{verdict['csr_char_count']:,} chars",
    )
    pixels = report["pixel_diff"]
    col4.metric(
        "Screenshot mismatch",
        f"{pixels['mismatch_percent']}%" if pixels["available"] else "n/a",
    )

    for note in verdict["notes"]:
        st.info(note)

    client_lines = verdict["client_rendered_lines"]
    if client_lines:
        st.subheader("Content only present after JavaScript runs")
        st.caption(
            f"{len(client_lines)} line(s) of text were missing from the "
            "JavaScript-disabled render. These are likely client-rendered."
        )
        preview = "\n".join(f"- {line}" for line in client_lines[:50])
        st.markdown(preview)
        if len(client_lines) > 50:
            st.caption(f"...and {len(client_lines) - 50} more lines.")


def _render_ui_tab(report: dict) -> None:
    pixels = report["pixel_diff"]
    render_compare(
        report["ssr"]["screenshot_b64"],
        report["csr"]["screenshot_b64"],
        pixels.get("overlay_b64", ""),
        ssr_status=report["ssr"]["status"],
        csr_status=report["csr"]["status"],
        ssr_error=report["ssr"]["error"],
        csr_error=report["csr"]["error"],
        height=720,
    )
    st.caption(
        "Tip: scroll either pane and both move together. "
        "Toggle 'Highlight differences' to paint unchanged areas grey and "
        "differences red."
    )


def _render_html_tab(report: dict) -> None:
    st.caption(
        "Unified diff of the visible text. Lines starting with '-' exist only in "
        "the server-rendered version, lines starting with '+' are added once "
        "JavaScript runs."
    )
    diff_text = report["html_diff"]
    if diff_text.strip():
        st.code(diff_text, language="diff")
    else:
        st.success("No visible text differences between the two versions.")


def main() -> None:
    st.title("SSR Checker")
    st.caption(
        "Load a URL twice - once with JavaScript disabled (server-rendered) and "
        "once with JavaScript enabled (regular) - and compare them side by side."
    )

    ok, message = _check_browser()
    if not ok:
        st.error(message)
        st.stop()

    with st.form("check_form"):
        col_url, col_device = st.columns([3, 1])
        url = col_url.text_input(
            "URL",
            placeholder="https://example.com",
            help="The page to inspect. Redirects are followed like a real browser.",
        )
        device_label = col_device.selectbox(
            "User-agent",
            list(DEVICES.keys()),
            index=list(DEVICES).index(DEFAULT_DEVICE_LABEL),
        )
        submitted = st.form_submit_button("Check", type="primary", use_container_width=True)

    if submitted:
        normalized = normalize_url(url)
        if not normalized or "." not in normalized:
            st.warning("Enter a valid URL, for example https://example.com")
        else:
            with st.status("In queue - launching browser...", expanded=True) as status:
                st.write(f"Processing `{normalized}` as **{device_label}**")
                try:
                    report = _run_check(normalized, device_label)
                    st.session_state["report"] = report
                    st.session_state["checked_url"] = normalized
                    status.update(label="Check complete", state="complete", expanded=False)
                except Exception as exc:  # noqa: BLE001
                    status.update(label="Check failed", state="error", expanded=True)
                    st.exception(exc)

    report = st.session_state.get("report")
    if not report:
        st.info("Enter a URL above and press **Check** to run the comparison.")
        return

    st.divider()
    checked_url = st.session_state.get("checked_url", "")
    header_left, header_right = st.columns([3, 1])
    header_left.markdown(f"### Results for `{checked_url}`")
    if header_right.button("Re-run", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    tab_ui, tab_html, tab_summary = st.tabs(["UI comparison", "HTML / text diff", "Summary"])

    with tab_ui:
        _render_ui_tab(report)
    with tab_html:
        _render_html_tab(report)
    with tab_summary:
        _render_summary(report)


if __name__ == "__main__":
    main()
