"""Comparison logic: SSR verdict, pixel diff and DOM/text diff."""

from __future__ import annotations

import base64
import difflib
import io
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from PIL import Image, ImageChops

from .renderer import CaptureResult

PIXEL_THRESHOLD = 32  # per-channel difference that counts as "changed"


# --------------------------------------------------------------------------- #
# Text / DOM helpers
# --------------------------------------------------------------------------- #
def extract_visible_text(html: str) -> list[str]:
    """Return non-empty visible text lines from ``html``.

    ``<script>``, ``<style>``, ``<noscript>`` and ``<template>`` nodes are
    removed first so only content a user (or a non-executing crawler) sees
    remains.
    """

    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()

    raw = soup.get_text(separator="\n")
    lines = (line.strip() for line in raw.splitlines())
    return [line for line in lines if line]


def _text_char_count(lines: list[str]) -> int:
    return sum(len(line) for line in lines)


def html_diff(ssr_html: str, csr_html: str, *, context: int = 3) -> str:
    """Return a human-readable unified diff of the visible text of both pages."""

    ssr_lines = extract_visible_text(ssr_html)
    csr_lines = extract_visible_text(csr_html)
    diff = difflib.unified_diff(
        ssr_lines,
        csr_lines,
        fromfile="server-rendered (JS disabled)",
        tofile="client-rendered (JS enabled)",
        lineterm="",
        n=context,
    )
    return "\n".join(diff)


# --------------------------------------------------------------------------- #
# Pixel diff
# --------------------------------------------------------------------------- #
def _png_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _pad_to_common_size(a: Image.Image, b: Image.Image) -> tuple[Image.Image, Image.Image]:
    width = max(a.width, b.width)
    height = max(a.height, b.height)
    if a.size != (width, height):
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(a, (0, 0))
        a = canvas
    if b.size != (width, height):
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(b, (0, 0))
        b = canvas
    return a, b


def pixel_diff(ssr_png: bytes, csr_png: bytes) -> dict:
    """Compare two screenshots and return stats + a highlight overlay (base64)."""

    if not ssr_png or not csr_png:
        return {"available": False, "mismatch_percent": 0.0, "overlay_b64": ""}

    a = Image.open(io.BytesIO(ssr_png)).convert("RGB")
    b = Image.open(io.BytesIO(csr_png)).convert("RGB")
    a, b = _pad_to_common_size(a, b)

    diff = ImageChops.difference(a, b).convert("L")
    mask = diff.point(lambda value: 255 if value > PIXEL_THRESHOLD else 0)

    histogram = mask.histogram()
    changed_pixels = histogram[255]
    total_pixels = mask.width * mask.height or 1

    # Red highlight where the two versions differ, drawn over a faded CSR image.
    faded = Image.blend(b, Image.new("RGB", b.size, "white"), 0.7)
    highlight = Image.new("RGB", b.size, (255, 0, 0))
    overlay = Image.composite(highlight, faded, mask)

    buffer = io.BytesIO()
    overlay.save(buffer, format="PNG")

    bbox = mask.getbbox()
    return {
        "available": True,
        "mismatch_percent": round(changed_pixels / total_pixels * 100, 2),
        "changed_pixels": changed_pixels,
        "total_pixels": total_pixels,
        "width": mask.width,
        "height": mask.height,
        "bbox": bbox,
        "overlay_b64": _png_to_base64(buffer.getvalue()),
    }


# --------------------------------------------------------------------------- #
# SSR verdict
# --------------------------------------------------------------------------- #
@dataclass
class SsrVerdict:
    level: str  # "full" | "mostly" | "partial" | "none" | "unknown"
    label: str
    coverage: float
    ssr_char_count: int
    csr_char_count: int
    client_rendered_lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def evaluate_ssr(ssr: CaptureResult, csr: CaptureResult) -> SsrVerdict:
    """Decide how much of the visible page is present in the server HTML.

    ``coverage`` is the share of the JavaScript-rendered visible text that also
    appears in the JavaScript-disabled (server-rendered) HTML. High coverage
    means the crawler sees the same content a user does.
    """

    notes: list[str] = []
    if ssr.error:
        notes.append(f"JavaScript-disabled render failed: {ssr.error}")
    if csr.error:
        notes.append(f"JavaScript-enabled render failed: {csr.error}")

    ssr_lines = extract_visible_text(ssr.html)
    csr_lines = extract_visible_text(csr.html)

    ssr_chars = _text_char_count(ssr_lines)
    csr_chars = _text_char_count(csr_lines)

    if csr_chars == 0:
        return SsrVerdict(
            level="unknown",
            label="Unknown - no client-rendered content to compare",
            coverage=0.0,
            ssr_char_count=ssr_chars,
            csr_char_count=csr_chars,
            notes=notes,
        )

    ssr_set = set(ssr_lines)
    client_rendered = [line for line in csr_lines if line not in ssr_set]
    shared_chars = sum(len(line) for line in csr_lines if line in ssr_set)
    coverage = shared_chars / csr_chars

    if coverage >= 0.9:
        level, label = "full", "Content is server-side rendered"
    elif coverage >= 0.5:
        level, label = "mostly", "Mostly server-side rendered"
    elif coverage >= 0.1:
        level, label = "partial", "Partially server-side rendered"
    else:
        level, label = "none", "Client-side rendered only"

    if ssr.status and ssr.status >= 400:
        notes.append(f"Server returned HTTP {ssr.status} for the JS-disabled render.")
    if not client_rendered:
        notes.append("No text differences detected between both versions.")

    return SsrVerdict(
        level=level,
        label=label,
        coverage=round(coverage, 3),
        ssr_char_count=ssr_chars,
        csr_char_count=csr_chars,
        client_rendered_lines=client_rendered,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
def build_report(ssr: CaptureResult, csr: CaptureResult, device_label: str) -> dict:
    """Bundle everything the UI needs into a plain, cache-friendly dict."""

    verdict = evaluate_ssr(ssr, csr)
    pixels = pixel_diff(ssr.screenshot_png, csr.screenshot_png)

    return {
        "device_label": device_label,
        "ssr": {
            "final_url": ssr.final_url,
            "status": ssr.status,
            "error": ssr.error,
            "screenshot_b64": _png_to_base64(ssr.screenshot_png),
            "html_chars": len(ssr.html or ""),
        },
        "csr": {
            "final_url": csr.final_url,
            "status": csr.status,
            "error": csr.error,
            "screenshot_b64": _png_to_base64(csr.screenshot_png),
            "html_chars": len(csr.html or ""),
        },
        "pixel_diff": pixels,
        "verdict": {
            "level": verdict.level,
            "label": verdict.label,
            "coverage": verdict.coverage,
            "ssr_char_count": verdict.ssr_char_count,
            "csr_char_count": verdict.csr_char_count,
            "client_rendered_lines": verdict.client_rendered_lines,
            "notes": verdict.notes,
        },
        "html_diff": html_diff(ssr.html, csr.html),
    }
