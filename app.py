"""SSR Checker - a Streamlit port of a side-by-side SSR/CSR comparison tool.

Run with:

    streamlit run app.py
"""

from __future__ import annotations

import base64
import json
import time

import streamlit as st

from ssr_checker.component import render_compare
from ssr_checker.devices import DEFAULT_DEVICE_LABEL, DEVICES, get_device
from ssr_checker.diff import build_report
from ssr_checker.renderer import capture, ensure_chromium, normalize_url

st.set_page_config(
    page_title="SSR Checker",
    page_icon="🔍",
    layout="wide",
)

_EXAMPLES = {
    "nextjs.org": "https://nextjs.org",
    "react.dev": "https://react.dev",
    "vercel.com": "https://vercel.com",
}

_VERDICT_CALLOUT = {
    "full": (st.success, "Crawlers see essentially the same content users do."),
    "mostly": (st.info, "Most content is server-rendered; some is added by JavaScript."),
    "partial": (st.warning, "A noticeable share of content only appears after JavaScript runs."),
    "none": (st.error, "Almost all content requires JavaScript - crawlers may see a near-empty page."),
    "unknown": (st.info, "Not enough client-rendered content to judge."),
}


@st.cache_resource(show_spinner="Preparing the browser (first run only)...")
def _check_browser() -> tuple[bool, str]:
    return ensure_chromium()


@st.cache_data(ttl=600, show_spinner=False)
def _run_check(url: str, device_label: str, nonce: int = 0) -> dict:
    device = get_device(device_label)
    captured = capture(url, device)
    return build_report(captured["ssr"], captured["csr"], device.label)


def _fill_url(target: str) -> None:
    st.session_state.url_input = target


def _b64_to_bytes(data_b64: str) -> bytes:
    return base64.b64decode(data_b64)


def _render_verdict(report: dict) -> None:
    verdict = report["verdict"]
    coverage = min(max(verdict["coverage"], 0.0), 1.0)
    pct = round(verdict["coverage"] * 100)

    st.subheader(verdict["label"])
    st.progress(
        coverage,
        text=f"{pct}% of the JavaScript-rendered text is present in the "
        "server-rendered HTML.",
    )

    callout, message = _VERDICT_CALLOUT.get(verdict["level"], _VERDICT_CALLOUT["unknown"])
    callout(message)

    for note in verdict["notes"]:
        st.info(note)


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


def _render_exports(report: dict) -> None:
    st.subheader("Export")
    clean = {
        "device_label": report["device_label"],
        "elapsed_seconds": report.get("elapsed_seconds"),
        "ssr": {k: v for k, v in report["ssr"].items() if k != "screenshot_b64"},
        "csr": {k: v for k, v in report["csr"].items() if k != "screenshot_b64"},
        "verdict": report["verdict"],
        "pixel_diff": {
            k: v for k, v in report["pixel_diff"].items() if k != "overlay_b64"
        },
        "html_diff": report["html_diff"],
    }

    cols = st.columns(4)
    cols[0].download_button(
        "Report (JSON)",
        data=json.dumps(clean, indent=2),
        file_name="ssr-report.json",
        mime="application/json",
        use_container_width=True,
    )
    ssr_shot = report["ssr"]["screenshot_b64"]
    if ssr_shot:
        cols[1].download_button(
            "JS-disabled PNG",
            data=_b64_to_bytes(ssr_shot),
            file_name="js-disabled.png",
            mime="image/png",
            use_container_width=True,
        )
    csr_shot = report["csr"]["screenshot_b64"]
    if csr_shot:
        cols[2].download_button(
            "JS-enabled PNG",
            data=_b64_to_bytes(csr_shot),
            file_name="js-enabled.png",
            mime="image/png",
            use_container_width=True,
        )
    overlay = report["pixel_diff"].get("overlay_b64")
    if overlay:
        cols[3].download_button(
            "Diff overlay PNG",
            data=_b64_to_bytes(overlay),
            file_name="diff-overlay.png",
            mime="image/png",
            use_container_width=True,
        )


def _render_details(report: dict) -> None:
    verdict = report["verdict"]
    pixels = report["pixel_diff"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Server-rendered text", f"{verdict['ssr_char_count']:,} chars")
    col2.metric("Client-rendered text", f"{verdict['csr_char_count']:,} chars")
    col3.metric("Text coverage", f"{round(verdict['coverage'] * 100)}%")
    col4.metric(
        "Screenshot mismatch",
        f"{pixels['mismatch_percent']}%" if pixels["available"] else "n/a",
    )

    st.subheader("Content only present after JavaScript runs")
    client_lines = verdict["client_rendered_lines"]
    if client_lines:
        st.caption(
            f"{len(client_lines)} line(s) of text were missing from the "
            "JavaScript-disabled render. These are likely client-rendered."
        )
        preview = "\n".join(f"- {line}" for line in client_lines[:50])
        st.markdown(preview)
        if len(client_lines) > 50:
            st.caption(f"...and {len(client_lines) - 50} more lines.")
    else:
        st.success("No text differences detected between both versions.")

    _render_exports(report)


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

    if "device_label" not in st.session_state:
        st.session_state.device_label = DEFAULT_DEVICE_LABEL
    if "refresh_nonce" not in st.session_state:
        st.session_state.refresh_nonce = 0
    if "url_input" not in st.session_state:
        st.session_state.url_input = ""

    with st.container(border=True):
        col_url, col_device = st.columns([3, 1])
        url = col_url.text_input(
            "URL",
            key="url_input",
            placeholder="https://example.com",
            help="The page to inspect. Redirects are followed like a real browser.",
        )
        with col_device:
            st.markdown("**User-agent**")
            with st.popover(st.session_state.device_label, use_container_width=True):
                for label in DEVICES:
                    if st.button(
                        label,
                        key=f"device_{label}",
                        use_container_width=True,
                        type="primary"
                        if label == st.session_state.device_label
                        else "secondary",
                    ):
                        st.session_state.device_label = label
                        st.rerun()

        example_cols = st.columns([1, 1, 1, 3])
        for col, (label, target) in zip(example_cols, _EXAMPLES.items()):
            col.button(
                f"Try {label}",
                key=f"example_{label}",
                use_container_width=True,
                on_click=_fill_url,
                args=(target,),
            )

        _spacer, col_check = st.columns([4, 1])
        submitted = col_check.button(
            "Check", key="check", type="primary", use_container_width=True
        )

    device_label = st.session_state.device_label
    run_now = submitted or st.session_state.pop("force_run", False)

    if run_now:
        normalized = normalize_url(url)
        if not normalized or "." not in normalized:
            st.warning("Enter a valid URL, for example https://example.com")
        else:
            nonce = st.session_state.refresh_nonce
            with st.status("Launching browser...", expanded=True) as status:
                st.write(f"Rendering `{normalized}` as **{device_label}**")
                start = time.perf_counter()
                try:
                    report = _run_check(normalized, device_label, nonce)
                    elapsed = time.perf_counter() - start
                    report["elapsed_seconds"] = round(elapsed, 1)
                    st.session_state["report"] = report
                    st.session_state["checked_url"] = normalized
                    status.update(
                        label=f"Check complete in {elapsed:.1f}s",
                        state="complete",
                        expanded=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    status.update(label="Check failed", state="error", expanded=True)
                    st.exception(exc)

    report = st.session_state.get("report")
    if not report:
        st.info(
            "Enter a URL above and press **Check** to compare the "
            "JavaScript-disabled and JavaScript-enabled renders."
        )
        st.markdown(
            "- **Side-by-side** screenshots of both renders with synced scrolling.\n"
            "- **Pixel and text diffs** showing exactly what only appears after "
            "JavaScript runs.\n"
            "- **Coverage verdict** estimating how much a crawler sees without "
            "JavaScript."
        )
        return

    st.divider()
    checked_url = st.session_state.get("checked_url", "")
    elapsed = report.get("elapsed_seconds")
    ssr, csr = report["ssr"], report["csr"]
    final_url = csr.get("final_url") or checked_url

    _render_verdict(report)

    meta_left, meta_right = st.columns([4, 1])
    with meta_left:
        bits = [f"Device **{report['device_label']}**"]
        if elapsed is not None:
            bits.append(f"checked in **{elapsed}s**")
        bits.append(
            f"JS-off HTTP **{ssr['status']}**"
            if ssr["status"]
            else "JS-off HTTP **n/a**"
        )
        bits.append(
            f"JS-on HTTP **{csr['status']}**"
            if csr["status"]
            else "JS-on HTTP **n/a**"
        )
        st.caption(" · ".join(bits))
        if final_url and final_url != checked_url:
            st.caption(f"↪ `{checked_url}` redirected to `{final_url}`")
    with meta_right:
        if final_url:
            st.link_button("Open page", final_url, use_container_width=True)
        if st.button("Re-run", key="rerun", use_container_width=True):
            st.session_state.refresh_nonce += 1
            st.session_state.force_run = True
            st.rerun()

    tab_side, tab_text, tab_details = st.tabs(
        ["Side-by-side", "Text diff", "Details"]
    )
    with tab_side:
        _render_ui_tab(report)
    with tab_text:
        _render_html_tab(report)
    with tab_details:
        _render_details(report)


if __name__ == "__main__":
    main()
