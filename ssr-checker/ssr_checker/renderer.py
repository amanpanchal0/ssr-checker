"""Headless-browser rendering for the SSR checker.

The page is loaded twice through Playwright/Chromium: once with JavaScript
disabled (this is what a non-executing crawler sees -- the "server-side
rendered" version) and once with JavaScript enabled (the regular, hydrated
version).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .devices import Device

DEFAULT_NAV_TIMEOUT_MS = 45_000
DEFAULT_SETTLE_MS = 2_500

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


@dataclass
class CaptureResult:
    """The result of loading a URL in one rendering mode."""

    url: str
    js_enabled: bool
    final_url: str = ""
    status: Optional[int] = None
    screenshot_png: bytes = b""
    html: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.html)


def normalize_url(url: str) -> str:
    """Add a scheme to ``url`` if it is missing one."""

    url = (url or "").strip()
    if not url:
        return url
    if not urlparse(url).scheme:
        url = "https://" + url.lstrip("/")
    return url


def _probe_chromium() -> tuple[bool, str]:
    """Return ``(ok, message)`` describing whether Chromium can be launched."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright is not installed. Run `pip install -r requirements.txt`."

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            browser.close()
        print("[ssr-checker] Chromium launch OK.", flush=True)
        return True, "Chromium is ready."
    except Exception as exc:  # noqa: BLE001
        print(f"[ssr-checker] Chromium launch failed: {exc}", flush=True)
        return False, str(exc)


def install_chromium(timeout_s: int = 600) -> tuple[bool, str]:
    """Download the Chromium browser binary that Playwright drives.

    This is needed on hosts (such as Streamlit Community Cloud) that install
    Python packages but never run a shell setup script.
    """

    print("[ssr-checker] Running `playwright install chromium`...", flush=True)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ssr-checker] install failed: {exc}", flush=True)
        return False, f"Could not run `playwright install chromium`: {exc}"

    if proc.stdout:
        print("[ssr-checker] install stdout:\n" + proc.stdout.strip(), flush=True)
    if proc.stderr:
        print("[ssr-checker] install stderr:\n" + proc.stderr.strip(), flush=True)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-1500:]
        return False, detail or f"`playwright install chromium` exited {proc.returncode}."

    return _probe_chromium()


def ensure_chromium() -> tuple[bool, str]:
    """Make sure Chromium is available, installing it on first run if needed.

    Safe to call repeatedly; only the first call in a process may download.
    """

    ok, message = _probe_chromium()
    if ok:
        return True, message

    ok, message = install_chromium()
    if ok:
        return True, message

    return False, (
        "Could not prepare Chromium for Playwright. On Streamlit Community "
        "Cloud make sure `packages.txt` lists the required Debian libraries.\n\n"
        f"{message}"
    )


def _capture_once(
    browser,
    device: Device,
    url: str,
    js_enabled: bool,
    nav_timeout_ms: int,
    settle_ms: int,
) -> CaptureResult:
    context = browser.new_context(
        user_agent=device.user_agent,
        viewport={"width": device.width, "height": device.height},
        device_scale_factor=device.device_scale_factor,
        is_mobile=device.is_mobile,
        has_touch=device.is_mobile,
        java_script_enabled=js_enabled,
        ignore_https_errors=True,
    )

    page = context.new_page()
    page.set_default_navigation_timeout(nav_timeout_ms)
    result = CaptureResult(url=url, js_enabled=js_enabled)

    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        if response is not None:
            result.status = response.status

        # Give late-loading assets a chance to settle, but never fail on this.
        try:
            page.wait_for_load_state("networkidle", timeout=settle_ms)
        except Exception:  # noqa: BLE001 - networkidle is best-effort
            pass

        if js_enabled:
            page.wait_for_timeout(settle_ms)

        result.html = page.content()
        result.screenshot_png = page.screenshot(full_page=True, animations="disabled")
    except Exception as exc:  # noqa: BLE001 - surface any rendering failure
        result.error = f"{type(exc).__name__}: {exc}".strip()
    finally:
        try:
            result.final_url = page.url
        except Exception:  # noqa: BLE001
            result.final_url = url
        context.close()

    return result


def capture(
    url: str,
    device: Device,
    *,
    nav_timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    settle_ms: int = DEFAULT_SETTLE_MS,
) -> dict[str, CaptureResult]:
    """Load ``url`` twice and return ``{"ssr": ..., "csr": ...}``.

    ``ssr`` is rendered with JavaScript disabled, ``csr`` with it enabled.
    """

    url = normalize_url(url)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        try:
            ssr = _capture_once(browser, device, url, False, nav_timeout_ms, settle_ms)
            csr = _capture_once(browser, device, url, True, nav_timeout_ms, settle_ms)
        finally:
            browser.close()

    return {"ssr": ssr, "csr": csr}
