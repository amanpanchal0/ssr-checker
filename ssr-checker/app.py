"""SSR Checker - a Streamlit port of a side-by-side SSR/CSR comparison tool.

Run with:

    streamlit run app.py
"""

from __future__ import annotations

import base64
import csv
import io
import json
import time
from urllib.parse import urlparse

import streamlit as st
from PIL import Image

from ssr_checker.component import render_compare
from ssr_checker.devices import DEFAULT_DEVICE_LABEL, DEVICES, get_device
from ssr_checker.diff import build_report
from ssr_checker.renderer import (
    capture,
    capture_many,
    ensure_chromium,
    normalize_url,
)

st.set_page_config(
    page_title="SSR Checker",
    page_icon="🔍",
    layout="wide",
)

MODE_SINGLE = "Single URL"
MODE_BULK = "Bulk · max 10"
MAX_BULK = 10
_MAX_BULK_SHOT_WIDTH = 1000

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

_VERDICT_MARK = {
    "full": "✅",
    "mostly": "🟢",
    "partial": "🟠",
    "none": "🔴",
    "unknown": "⚪",
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


def _single_rerun() -> None:
    st.session_state.refresh_nonce += 1
    st.session_state.force_run = True
    st.rerun()


def _b64_to_bytes(data_b64: str) -> bytes:
    return base64.b64decode(data_b64)


def _downscale_png(png_bytes: bytes, max_width: int) -> bytes:
    """Shrink a screenshot so bulk results stay within memory limits."""

    if not png_bytes:
        return png_bytes
    try:
        image = Image.open(io.BytesIO(png_bytes))
    except Exception:  # noqa: BLE001 - never fail a check over a decode issue
        return png_bytes
    if image.width <= max_width:
        return png_bytes
    ratio = max_width / image.width
    resized = image.convert("RGB").resize(
        (max_width, max(1, round(image.height * ratio))), Image.LANCZOS
    )
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG")
    return buffer.getvalue()


def _device_selector() -> None:
    st.markdown("**User-agent**")
    current = st.session_state.device_label
    with st.popover(current, use_container_width=True):
        for label in DEVICES:
            if st.button(
                label,
                key=f"device_{label}",
                use_container_width=True,
                type="primary" if label == current else "secondary",
            ):
                st.session_state.device_label = label
                st.rerun()


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


def _clean_report(report: dict) -> dict:
    """Return a JSON-serialisable copy of a report with images removed."""

    return {
        "url": report.get("url", ""),
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


def _render_exports(report: dict, key_prefix: str) -> None:
    st.subheader("Export")
    clean = _clean_report(report)

    cols = st.columns(4)
    cols[0].download_button(
        "Report (JSON)",
        data=json.dumps(clean, indent=2),
        file_name="ssr-report.json",
        mime="application/json",
        use_container_width=True,
        key=f"json-{key_prefix}",
    )
    ssr_shot = report["ssr"]["screenshot_b64"]
    if ssr_shot:
        cols[1].download_button(
            "JS-disabled PNG",
            data=_b64_to_bytes(ssr_shot),
            file_name="js-disabled.png",
            mime="image/png",
            use_container_width=True,
            key=f"ssr-png-{key_prefix}",
        )
    csr_shot = report["csr"]["screenshot_b64"]
    if csr_shot:
        cols[2].download_button(
            "JS-enabled PNG",
            data=_b64_to_bytes(csr_shot),
            file_name="js-enabled.png",
            mime="image/png",
            use_container_width=True,
            key=f"csr-png-{key_prefix}",
        )
    overlay = report["pixel_diff"].get("overlay_b64")
    if overlay:
        cols[3].download_button(
            "Diff overlay PNG",
            data=_b64_to_bytes(overlay),
            file_name="diff-overlay.png",
            mime="image/png",
            use_container_width=True,
            key=f"overlay-png-{key_prefix}",
        )


def _render_details(report: dict, key_prefix: str) -> None:
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

    _render_exports(report, key_prefix)


def _render_report(report: dict, *, on_rerun=None) -> None:
    checked_url = report.get("url", "")
    key_prefix = checked_url or "report"
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
            st.link_button(
                "Open page",
                final_url,
                use_container_width=True,
                key=f"open-{key_prefix}",
            )
        if on_rerun is not None:
            if st.button(
                "Re-run",
                key=f"rerun-{key_prefix}",
                use_container_width=True,
            ):
                on_rerun()

    tab_side, tab_text, tab_details = st.tabs(
        ["Side-by-side", "Text diff", "Details"]
    )
    with tab_side:
        _render_ui_tab(report)
    with tab_text:
        _render_html_tab(report)
    with tab_details:
        _render_details(report, key_prefix)


# --------------------------------------------------------------------------- #
# Bulk mode
# --------------------------------------------------------------------------- #
def _resolve_urls(raw_text: str) -> tuple[list[str], list[str], int]:
    """Parse pasted lines into ``(urls, invalid_lines, overflow_count)``."""

    seen: set[str] = set()
    accepted: list[str] = []
    invalid: list[str] = []

    for line in (raw_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        candidate = normalize_url(stripped)
        if not candidate or "." not in candidate:
            invalid.append(stripped)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        accepted.append(candidate)

    overflow = max(0, len(accepted) - MAX_BULK)
    return accepted[:MAX_BULK], invalid, overflow


def _run_bulk(urls: list[str], device_label: str, nonce: int) -> list[dict]:
    device = get_device(device_label)
    cache: dict = st.session_state.setdefault("bulk_cache", {})

    missing = [url for url in urls if (url, device_label, nonce) not in cache]
    if missing:
        progress = st.progress(0.0, text="Launching browser...")

        def on_progress(done: int, total: int, url: str) -> None:
            progress.progress(done / total, text=f"Rendering {done}/{total} · {url}")

        captured = capture_many(missing, device, on_progress=on_progress)
        for url, result in captured.items():
            for mode in ("ssr", "csr"):
                shot = result[mode].screenshot_png
                if shot:
                    result[mode].screenshot_png = _downscale_png(
                        shot, _MAX_BULK_SHOT_WIDTH
                    )
            report = build_report(result["ssr"], result["csr"], device.label)
            report["url"] = url
            cache[(url, device_label, nonce)] = report
        progress.progress(1.0, text=f"Done · {len(captured)} page(s) rendered")

    return [cache[(url, device_label, nonce)] for url in urls]


def _tab_label(report: dict) -> str:
    url = report.get("url", "")
    host = urlparse(url).netloc or url
    host = host[4:] if host.startswith("www.") else host
    mark = _VERDICT_MARK.get(report["verdict"]["level"], "")
    label = f"{mark} {host}".strip()
    return label if len(label) <= 34 else label[:31] + "..."


def _unique_tab_labels(reports: list[dict]) -> list[str]:
    seen: dict[str, int] = {}
    labels: list[str] = []
    for report in reports:
        base = _tab_label(report)
        if base in seen:
            seen[base] += 1
            base = f"{base} ({seen[base]})"
        else:
            seen[base] = 1
        labels.append(base)
    return labels


def _bulk_rows(reports: list[dict]) -> list[dict]:
    rows = []
    for report in reports:
        verdict = report["verdict"]
        pixels = report["pixel_diff"]
        rows.append(
            {
                "URL": report.get("url", ""),
                "HTTP": report["csr"]["status"],
                "Coverage %": round(verdict["coverage"] * 100),
                "Verdict": verdict["label"],
                "Mismatch %": (
                    pixels["mismatch_percent"] if pixels["available"] else None
                ),
            }
        )
    return rows


def _render_bulk_summary(reports: list[dict]) -> None:
    rows = _bulk_rows(reports)
    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "URL": st.column_config.LinkColumn("URL", display_text="Open"),
            "HTTP": st.column_config.NumberColumn("HTTP", format="%d"),
            "Coverage %": st.column_config.ProgressColumn(
                "Coverage %", min_value=0, max_value=100, format="%d%%"
            ),
            "Mismatch %": st.column_config.NumberColumn("Mismatch %", format="%.1f%%"),
        },
    )

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["URL", "HTTP", "Coverage %", "Verdict", "Mismatch %"],
    )
    writer.writeheader()
    writer.writerows(rows)

    col_csv, col_json = st.columns(2)
    col_csv.download_button(
        "Summary (CSV)",
        data=buffer.getvalue(),
        file_name="ssr-summary.csv",
        mime="text/csv",
        use_container_width=True,
        key="bulk-csv",
    )
    col_json.download_button(
        "Reports (JSON)",
        data=json.dumps([_clean_report(r) for r in reports], indent=2),
        file_name="ssr-reports.json",
        mime="application/json",
        use_container_width=True,
        key="bulk-json",
    )


def _bulk_view() -> None:
    with st.container(border=True):
        col_urls, col_device = st.columns([3, 1])
        raw = col_urls.text_area(
            "URLs - one per line (max 10)",
            key="bulk_input",
            height=170,
            placeholder="https://example.com\nhttps://nextjs.org\nhttps://react.dev",
        )
        with col_device:
            _device_selector()

        _spacer, col_check = st.columns([4, 1])
        submitted = col_check.button(
            "Check all", key="check-all", type="primary", use_container_width=True
        )

    device_label = st.session_state.device_label
    run_now = submitted or st.session_state.pop("force_run", False)

    if run_now:
        urls, invalid, overflow = _resolve_urls(raw)
        if not urls:
            st.warning("Enter at least one valid URL, one per line.")
        else:
            if invalid:
                preview = ", ".join(f"`{line}`" for line in invalid[:5])
                more = "" if len(invalid) <= 5 else f" (+{len(invalid) - 5} more)"
                st.warning(f"Skipped {len(invalid)} invalid line(s): {preview}{more}")
            if overflow:
                st.warning(
                    f"Only the first {MAX_BULK} URLs are checked; "
                    f"{overflow} were ignored."
                )
            with st.status("Launching browser...", expanded=True) as status:
                st.write(f"Rendering {len(urls)} URL(s) as **{device_label}**")
                try:
                    reports = _run_bulk(urls, device_label, st.session_state.refresh_nonce)
                    st.session_state["bulk_reports"] = reports
                    status.update(
                        label=f"Rendered {len(urls)} page(s)",
                        state="complete",
                        expanded=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    status.update(
                        label="Bulk check failed", state="error", expanded=True
                    )
                    st.exception(exc)

    reports = st.session_state.get("bulk_reports")
    if not reports:
        st.info("Paste up to 10 URLs (one per line) above and press **Check all**.")
        return

    st.divider()
    st.subheader("Bulk results")
    _render_bulk_summary(reports)

    if st.button("Re-run all", key="rerun-all"):
        st.session_state.refresh_nonce += 1
        st.session_state.force_run = True
        st.rerun()

    tabs = st.tabs(_unique_tab_labels(reports))
    for tab, report in zip(tabs, reports):
        with tab:
            _render_report(report)


# --------------------------------------------------------------------------- #
# Single mode
# --------------------------------------------------------------------------- #
def _single_view() -> None:
    with st.container(border=True):
        col_url, col_device = st.columns([3, 1])
        url = col_url.text_input(
            "URL",
            key="url_input",
            placeholder="https://example.com",
            help="The page to inspect. Redirects are followed like a real browser.",
        )
        with col_device:
            _device_selector()

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
                    report["url"] = normalized
                    st.session_state["report"] = report
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
    _render_report(report, on_rerun=_single_rerun)


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
    if "mode" not in st.session_state:
        st.session_state.mode = MODE_SINGLE

    mode = st.segmented_control(
        "Mode",
        [MODE_SINGLE, MODE_BULK],
        key="mode",
    )

    if mode == MODE_BULK:
        _bulk_view()
    else:
        _single_view()


if __name__ == "__main__":
    main()
