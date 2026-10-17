"""SSR Checker: compare server-side rendered and client-side rendered pages."""

from .devices import DEVICES, Device, get_device
from .renderer import CaptureResult, capture, normalize_url, playwright_available
from .diff import SsrVerdict, build_report

__all__ = [
    "DEVICES",
    "Device",
    "get_device",
    "CaptureResult",
    "capture",
    "normalize_url",
    "playwright_available",
    "SsrVerdict",
    "build_report",
]

__version__ = "0.1.0"
