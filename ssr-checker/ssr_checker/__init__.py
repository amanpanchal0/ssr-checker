"""SSR Checker: compare server-side rendered and client-side rendered pages."""

from .devices import DEVICES, Device, get_device
from .renderer import (
    CaptureResult,
    capture,
    ensure_chromium,
    install_chromium,
    normalize_url,
)
from .diff import SsrVerdict, build_report

__all__ = [
    "DEVICES",
    "Device",
    "get_device",
    "CaptureResult",
    "capture",
    "ensure_chromium",
    "install_chromium",
    "normalize_url",
    "SsrVerdict",
    "build_report",
]

__version__ = "0.1.0"
