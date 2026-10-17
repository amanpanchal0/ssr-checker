"""SSR Checker - server-rendered vs client-rendered comparison tool.

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
    page_title="SSR Checker – SSR vs CSR Side-by-Side Checker",
    page_icon="🔍",
    layout="wide",
    menu_items={
        "About": (
            "SSR Checker is a free side-by-side SSR and CSR checker. It loads a "
            "URL with JavaScript disabled (server-side rendered) and enabled "
            "(client-side rendered), then compares them with pixel and "
            "visible-text diffs so you can see what crawlers get without "
            "JavaScript."
        )
    },
)

_CSS = """
<style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    .support-box {
        border: 1px solid #fbbf24; border-radius: 12px;
        padding: 18px 22px; margin-top: 10px;
    }
    .hint-box {
        background: #f8fafc; border: 1px solid #e2e8f0;
        border-radius: 10px; padding: 14px 16px;
    }
    code {word-break: break-all;}
</style>
"""

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

_VERDICT_FILTERS = [
    ("full", "✅ Fully server-rendered"),
    ("mostly", "🟢 Mostly server-rendered"),
    ("partial", "🟠 Partially server-rendered"),
    ("none", "🔴 Client-side rendered only"),
    ("unknown", "⚪ Unknown"),
]

_SSR_FAQS = [
    (
        "What is an SSR checker?",
        "An SSR checker compares how a page renders with JavaScript disabled "
        "versus enabled to measure how much of the content is server-side "
        "rendered. High coverage means crawlers see the same content that "
        "visitors do.",
    ),
    (
        "What is the difference between SSR and CSR?",
        "SSR (server-side rendering) sends fully rendered HTML from the server, "
        "so content is visible immediately and to crawlers that do not run "
        "JavaScript. CSR (client-side rendering) builds the page in the browser "
        "with JavaScript, so crawlers that skip JavaScript can see an empty page.",
    ),
    (
        "Is this SSR vs CSR checker free?",
        "Yes. Check as many URLs as you like at no cost, one at a time or up to "
        "ten at once with the bulk checker.",
    ),
    (
        "Can I test how Googlebot sees my page?",
        "Yes. Choose a Googlebot Smartphone or Googlebot Desktop user-agent to "
        "see the JavaScript-disabled HTML the crawler receives, then compare it "
        "with the fully rendered page.",
    ),
    (
        "Can I compare several URLs at once?",
        "Yes. Switch to Bulk · max 10, paste up to ten URLs (one per line) and "
        "get one result tab per URL, plus a summary table and CSV export.",
    ),
    (
        "How accurate is the SSR and CSR comparison?",
        "Checks run in a real headless Chromium browser at the selected "
        "viewport, so redirects, viewport-specific markup and late-loading "
        "scripts behave like a real visit. A pixel diff reports the mismatch "
        "percentage between the two renders.",
    ),
]


# --------------------------------------------------------------------------- #
# Browser + checks
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner="Preparing the browser (first run only)...")
def _check_browser() -> tuple[bool, str]:
    return ensure_chromium()


@st.cache_data(ttl=600, show_spinner=False)
def _run_check(
    url: str,
    device_label: str,
    nonce: int,
    nav_timeout_ms: int,
    settle_ms: int,
) -> dict:
    device = get_device(device_label)
    captured = capture(
        url, device, nav_timeout_ms=nav_timeout_ms, settle_ms=settle_ms
    )
    return build_report(captured["ssr"], captured["csr"], device.label)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _settings() -> tuple[str, int, int]:
    device_label = st.session_state.get("device_label", DEFAULT_DEVICE_LABEL)
    nav_timeout_ms = int(st.session_state.get("nav_timeout_s", 45) * 1000)
    settle_ms = int(st.session_state.get("settle_s", 2.5) * 1000)
    return device_label, nav_timeout_ms, settle_ms


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


# --------------------------------------------------------------------------- #
# Rendering primitives
# --------------------------------------------------------------------------- #
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


def _render_report(report: dict, *, on_rerun=None) -> None:
    checked_url = report.get("url", "")
    key_prefix = checked_url or "report"
    elapsed = report.get("elapsed_seconds")
    ssr, csr = report["ssr"], report["csr"]
    final_url = csr.get("final_url") or checked_url

    if checked_url:
        st.markdown(f"#### `{checked_url}`")

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
                width="stretch",
                key=f"open-{key_prefix}",
            )
        if on_rerun is not None:
            if st.button(
                "Re-run",
                key=f"rerun-{key_prefix}",
                width="stretch",
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
        _render_details(report)


# --------------------------------------------------------------------------- #
# Export helpers
# --------------------------------------------------------------------------- #
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


def _render_single_exports(report: dict) -> None:
    st.subheader("3. Export results")
    clean = _clean_report(report)
    cols = st.columns(4)
    cols[0].download_button(
        "⬇️ Report (JSON)",
        data=json.dumps(clean, indent=2),
        file_name="ssr-report.json",
        mime="application/json",
        width="stretch",
        key="export-json",
    )
    ssr_shot = report["ssr"]["screenshot_b64"]
    if ssr_shot:
        cols[1].download_button(
            "⬇️ JS-disabled PNG",
            data=_b64_to_bytes(ssr_shot),
            file_name="js-disabled.png",
            mime="image/png",
            width="stretch",
            key="export-ssr-png",
        )
    csr_shot = report["csr"]["screenshot_b64"]
    if csr_shot:
        cols[2].download_button(
            "⬇️ JS-enabled PNG",
            data=_b64_to_bytes(csr_shot),
            file_name="js-enabled.png",
            mime="image/png",
            width="stretch",
            key="export-csr-png",
        )
    overlay = report["pixel_diff"].get("overlay_b64")
    if overlay:
        cols[3].download_button(
            "⬇️ Diff overlay PNG",
            data=_b64_to_bytes(overlay),
            file_name="diff-overlay.png",
            mime="image/png",
            width="stretch",
            key="export-overlay-png",
        )


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


def _trim_text(raw: str) -> tuple[str, int]:
    """Return ``(cleaned_text, overflow_count)`` for the first :data:`MAX_BULK`."""

    urls, _invalid, overflow = _resolve_urls(raw)
    return "\n".join(urls), overflow


def _trim_bulk_input() -> None:
    """Callback: drop everything past the first :data:`MAX_BULK` URLs."""

    text, overflow = _trim_text(st.session_state.get("bulk_input", ""))
    st.session_state["bulk_trimmed"] = overflow
    if overflow:
        st.session_state["bulk_input"] = text


def _urls_from_files(files) -> list[str]:
    tokens: list[str] = []
    for handle in files or []:
        try:
            text = handle.read().decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if handle.name.lower().endswith(".csv"):
                tokens.extend(cell.strip() for cell in line.split(",") if cell.strip())
            else:
                tokens.append(line)
    return tokens


def _run_bulk(
    urls: list[str],
    device_label: str,
    nonce: int,
    nav_timeout_ms: int,
    settle_ms: int,
) -> list[dict]:
    device = get_device(device_label)
    cache: dict = st.session_state.setdefault("bulk_cache", {})

    missing = [
        url for url in urls if (url, device_label, nonce, nav_timeout_ms, settle_ms) not in cache
    ]
    if missing:
        progress = st.progress(0.0, text="Launching browser...")

        def on_progress(done: int, total: int, url: str) -> None:
            progress.progress(done / total, text=f"Rendering {done}/{total} · {url}")

        captured = capture_many(
            missing,
            device,
            on_progress=on_progress,
            nav_timeout_ms=nav_timeout_ms,
            settle_ms=settle_ms,
        )
        for url, result in captured.items():
            for mode in ("ssr", "csr"):
                shot = result[mode].screenshot_png
                if shot:
                    result[mode].screenshot_png = _downscale_png(
                        shot, _MAX_BULK_SHOT_WIDTH
                    )
            report = build_report(result["ssr"], result["csr"], device.label)
            report["url"] = url
            cache[(url, device_label, nonce, nav_timeout_ms, settle_ms)] = report
        progress.progress(1.0, text=f"Done · {len(captured)} page(s) rendered")

    return [
        cache[(url, device_label, nonce, nav_timeout_ms, settle_ms)] for url in urls
    ]


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


def _bulk_view() -> None:
    device_label, nav_timeout_ms, settle_ms = _settings()

    st.subheader("1. Add your URLs")
    col_input, col_upload = st.columns([2, 1])

    # Uploader is handled before the text area is created so the "load" button
    # can safely write the parsed URLs into the text box.
    with col_upload:
        uploaded = st.file_uploader(
            "Or upload TXT / CSV",
            type=["txt", "csv"],
            accept_multiple_files=True,
            key="bulk_files",
            help="One URL per line (TXT), or URLs anywhere in the CSV cells.",
        )
        if uploaded and st.button(
            "Load URLs from file", key="load-files", use_container_width=True
        ):
            tokens = _urls_from_files(uploaded)
            if tokens:
                existing = st.session_state.get("bulk_input", "").strip()
                st.session_state["bulk_input"] = "\n".join(
                    part for part in [existing, "\n".join(tokens)] if part
                )

    with col_input:
        raw = st.text_area(
            "Paste URLs (one per line)",
            key="bulk_input",
            height=180,
            placeholder="https://example.com\nhttps://nextjs.org\nhttps://react.dev",
        )

    urls, invalid, overflow = _resolve_urls(raw)
    if urls:
        message = f"✅ {len(urls)} URL(s) ready to check."
        if invalid:
            message += f" ({len(invalid)} line(s) skipped - didn't look like a URL.)"
        st.success(message)
    else:
        st.info(
            f"Paste up to {MAX_BULK} URLs (one per line) or upload a TXT/CSV file."
        )
        if invalid:
            st.warning(
                f"⚠️ {len(invalid)} line(s) didn't look like a valid URL and were skipped."
            )

    run_clicked = st.button(
        "🚀 Check SSR vs CSR",
        key="check-all",
        type="primary",
        width="stretch",
        on_click=_trim_bulk_input,
        disabled=not urls,
    )

    if run_clicked and urls:
        trimmed = st.session_state.pop("bulk_trimmed", 0)
        if trimmed:
            st.warning(
                f"Only the first {MAX_BULK} URLs are checked; "
                f"{trimmed} extra URL(s) were removed."
            )
        elif overflow:
            st.warning(
                f"Only the first {MAX_BULK} URLs are checked; {overflow} were ignored."
            )

        start = time.perf_counter()
        with st.status("Launching browser...", expanded=True) as status:
            st.write(f"Rendering {len(urls)} URL(s) as **{device_label}**")
            try:
                reports = _run_bulk(
                    urls,
                    device_label,
                    st.session_state.refresh_nonce,
                    nav_timeout_ms,
                    settle_ms,
                )
                st.session_state["bulk_reports"] = reports
                st.session_state["bulk_meta"] = {
                    "count": len(reports),
                    "duration": round(time.perf_counter() - start, 2),
                }
                status.update(
                    label=f"Rendered {len(urls)} page(s)",
                    state="complete",
                    expanded=False,
                )
            except Exception as exc:  # noqa: BLE001
                status.update(label="Bulk check failed", state="error", expanded=True)
                st.exception(exc)

    reports = st.session_state.get("bulk_reports")
    if not reports:
        return

    st.divider()
    st.subheader("2. Results")
    _render_bulk_results(reports)

    st.divider()
    if st.button("🔄 Re-run all", key="rerun-all"):
        st.session_state.refresh_nonce += 1
        st.session_state.force_run = True
        st.rerun()


def _render_bulk_results(reports: list[dict]) -> None:
    counts = {"full": 0, "mostly": 0, "partial": 0, "none": 0, "unknown": 0}
    for report in reports:
        level = report["verdict"]["level"]
        counts[level] = counts.get(level, 0) + 1

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Total URLs", len(reports))
    m2.metric("✅ Fully SSR", counts["full"])
    m3.metric("🟢 Mostly SSR", counts["mostly"])
    m4.metric("🟠 Partially SSR", counts["partial"])
    m5.metric("🔴 Client-only", counts["none"])
    m6.metric("⚪ Unknown / error", counts["unknown"])

    meta = st.session_state.get("bulk_meta")
    if meta:
        st.caption(
            f"Finished {meta['count']} checks in {meta['duration']}s. "
            "One result tab per URL below."
        )

    fcol1, fcol2 = st.columns([1, 2])
    levels = fcol1.multiselect(
        "Filter by verdict",
        options=[key for key, _ in _VERDICT_FILTERS],
        format_func=lambda key: dict(_VERDICT_FILTERS)[key],
        default=[],
    )
    search = fcol2.text_input("Search URL contains… (plain text, not regex)", "")

    filtered = [
        report
        for report in reports
        if (not levels or report["verdict"]["level"] in levels)
        and (not search or search.lower() in report.get("url", "").lower())
    ]

    if not filtered:
        st.info("No results match the current filters.")
        return

    rows = _bulk_rows(filtered)
    st.dataframe(
        rows,
        width="stretch",
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

    tabs = st.tabs(_unique_tab_labels(filtered))
    for tab, report in zip(tabs, filtered):
        with tab:
            _render_report(report)

    st.subheader("3. Export results")
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["URL", "HTTP", "Coverage %", "Verdict", "Mismatch %"],
    )
    writer.writeheader()
    writer.writerows(rows)

    e1, e2, e3 = st.columns(3)
    e1.download_button(
        "⬇️ Summary (CSV)",
        data=buffer.getvalue(),
        file_name="ssr-summary.csv",
        mime="text/csv",
        width="stretch",
        key="bulk-csv",
    )
    e2.download_button(
        "⬇️ Summary (JSON)",
        data=json.dumps(rows, indent=2),
        file_name="ssr-summary.json",
        mime="application/json",
        width="stretch",
        key="bulk-json",
    )
    e3.download_button(
        "⬇️ Full reports (JSON)",
        data=json.dumps([_clean_report(r) for r in filtered], indent=2),
        file_name="ssr-reports.json",
        mime="application/json",
        width="stretch",
        key="bulk-reports-json",
    )


# --------------------------------------------------------------------------- #
# Single mode
# --------------------------------------------------------------------------- #
def _single_view() -> None:
    device_label, nav_timeout_ms, settle_ms = _settings()

    st.subheader("1. Enter a URL")
    url = st.text_input(
        "URL",
        key="url_input",
        placeholder="https://example.com",
        help="The page to inspect. Redirects are followed like a real browser.",
    )

    example_cols = st.columns([1, 1, 1, 3])
    for col, (label, target) in zip(example_cols, _EXAMPLES.items()):
        col.button(
            f"Try {label}",
            key=f"example_{label}",
            width="stretch",
            on_click=_fill_url,
            args=(target,),
        )

    submitted = st.button(
        "🚀 Check SSR vs CSR",
        key="check",
        type="primary",
        width="stretch",
    )

    run_now = submitted or st.session_state.pop("force_run", False)

    if run_now:
        normalized = normalize_url(url)
        if not normalized or "." not in normalized:
            st.warning("Enter a valid URL, for example https://example.com")
        else:
            with st.status("Launching browser...", expanded=True) as status:
                st.write(f"Rendering `{normalized}` as **{device_label}**")
                start = time.perf_counter()
                try:
                    report = _run_check(
                        normalized,
                        device_label,
                        st.session_state.refresh_nonce,
                        nav_timeout_ms,
                        settle_ms,
                    )
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
            "Enter a URL above and press **Check SSR vs CSR** to compare the "
            "JavaScript-disabled and JavaScript-enabled renders."
        )
        return

    st.divider()
    st.subheader("2. Results")
    _render_report(report, on_rerun=_single_rerun)

    st.divider()
    _render_single_exports(report)


# --------------------------------------------------------------------------- #
# SEO / FAQ content and support box
# --------------------------------------------------------------------------- #
def _inject_structured_data() -> None:
    """Emit JSON-LD (WebApplication + FAQPage) for search engines."""

    faq_schema = [
        {
            "@type": "Question",
            "name": question,
            "acceptedAnswer": {"@type": "Answer", "text": answer},
        }
        for question, answer in _SSR_FAQS
    ]

    blocks = [
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": "SSR Checker",
            "alternateName": "SSR vs CSR Side-by-Side Checker",
            "applicationCategory": "SEO Tool",
            "operatingSystem": "Web",
            "browserRequirements": "Requires JavaScript",
            "description": (
                "Free SSR checker that loads a URL with JavaScript disabled "
                "(server-side rendered) and enabled (client-side rendered) and "
                "compares them side by side with pixel and visible-text diffs."
            ),
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "featureList": [
                "Side-by-side SSR and CSR comparison with synced scrolling",
                "Pixel difference overlay and mismatch percentage",
                "Visible-text diff of content only added by JavaScript",
                "Googlebot Smartphone and Desktop, plus Chrome mobile/desktop",
                "Bulk check up to 10 URLs at once",
            ],
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": faq_schema,
        },
    ]

    for block in blocks:
        st.markdown(
            f'<script type="application/ld+json">{json.dumps(block)}</script>',
            unsafe_allow_html=True,
        )


def _render_seo_content() -> None:
    """Keyword-rich, genuinely useful content, kept below the fold."""

    st.divider()

    with st.expander("ℹ️ What are SSR and CSR?", expanded=False):
        st.markdown(
            "- **SSR (server-side rendering):** the server returns fully rendered "
            "HTML, so content is visible immediately and to crawlers that do not "
            "execute JavaScript.\n"
            "- **CSR (client-side rendering):** the browser downloads a minimal "
            "HTML shell and builds the page with JavaScript, so crawlers that "
            "skip JavaScript can see little or no content.\n\n"
            "Most modern frameworks - Next.js, Nuxt, React, Vue, Angular and "
            "others - support both, often as a mix."
        )

    with st.expander("🔁 How the side-by-side SSR vs CSR comparison works", expanded=False):
        st.markdown(
            "1. Pick a **user-agent**: Googlebot Smartphone/Desktop or a real "
            "Chrome mobile/desktop profile.\n"
            "2. The URL is rendered twice at the same viewport with JavaScript "
            "**off** (SSR) and JavaScript **on** (CSR).\n"
            "3. Both screenshots appear **side by side with synced scrolling**, "
            "plus a **pixel-difference overlay** (red = differs).\n"
            "4. A **visible-text diff** lists exactly what only appears once "
            "JavaScript runs, and a **coverage verdict** estimates how much a "
            "crawler sees without JavaScript."
        )

    with st.expander("🔍 Why check SSR for SEO?", expanded=False):
        st.markdown(
            "If your content is client-side rendered, search and social crawlers "
            "may see an empty page. Run this **SSR SEO checker** to confirm your "
            "titles, headings, body copy and internal links are present in the "
            "initial HTML, and to catch **server-side rendering** regressions "
            "before they affect rankings."
        )

    with st.expander("❓ Frequently asked questions", expanded=False):
        faq_md = "\n\n".join(
            f"**{question}**\n\n{answer}" for question, answer in _SSR_FAQS
        )
        st.markdown(faq_md)

    _inject_structured_data()


def _render_support_box() -> None:
    st.markdown(
        """
<div class="support-box">
<h3>🌟 Support this project</h3>
<p>If this <strong>SSR &amp; CSR checker</strong> saved you time, a share or a
star helps others find it.</p>
<p>Found a bug or have an idea? Open an issue on the project repository and
let's make it better.</p>
</div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    st.title("🔍 SSR Checker")
    st.markdown(
        "#### Side-by-Side SSR & CSR Checker — see exactly what renders "
        "without JavaScript"
    )
    st.markdown(
        "This free **SSR checker** loads a URL twice in a real headless "
        "Chromium browser - once with JavaScript disabled (the **server-side "
        "rendered / SSR** version a non-executing crawler such as Googlebot "
        "sees) and once with JavaScript enabled (the fully **client-side "
        "rendered / CSR** version a visitor sees). The **side-by-side SSR and "
        "CSR checker** highlights pixel differences and any text that only "
        "appears after JavaScript runs."
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

    with st.sidebar:
        st.header("⚙️ Check settings")
        st.radio(
            "User-agent",
            list(DEVICES),
            key="device_label",
            help="The browser profile used for both renders.",
        )
        st.slider(
            "Navigation timeout (seconds)",
            10,
            90,
            45,
            key="nav_timeout_s",
            help="How long to wait for the page to respond before giving up.",
        )
        st.slider(
            "Extra settle time (seconds)",
            0.5,
            8.0,
            2.5,
            step=0.5,
            key="settle_s",
            help="How long to wait after load for late content or animations to "
            "settle before the screenshot is captured.",
        )
        st.divider()
        st.caption(
            "Each URL is rendered twice in headless Chromium - JavaScript off "
            "then on. Raise the settle time for slow, animated or "
            "JavaScript-heavy pages."
        )

    st.divider()

    mode = st.segmented_control(
        "Mode",
        [MODE_SINGLE, MODE_BULK],
        key="mode",
    )

    if mode == MODE_BULK:
        _bulk_view()
    else:
        _single_view()

    _render_seo_content()
    _render_support_box()


if __name__ == "__main__":
    main()
