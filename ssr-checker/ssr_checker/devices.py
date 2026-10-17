"""User-agent and viewport presets for the SSR checker.

Each device maps a friendly label (shown in the UI) to the exact user-agent
string, viewport size and mobile flags used when rendering a page.
"""

from __future__ import annotations

from dataclasses import dataclass

GOOGLEBOT_SMARTPHONE_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

GOOGLEBOT_DESKTOP_UA = (
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; "
    "+http://www.google.com/bot.html) Chrome/120.0.0.0 Safari/537.36"
)

CHROME_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

CHROME_IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.0.0 "
    "Mobile/15E148 Safari/604.1"
)

CHROME_WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Device:
    """A rendering preset (user-agent + viewport + mobile behaviour)."""

    label: str
    user_agent: str
    width: int
    height: int
    is_mobile: bool = False
    device_scale_factor: float = 1.0


DEVICES: dict[str, Device] = {
    "Googlebot - Smartphone": Device(
        label="Googlebot - Smartphone",
        user_agent=GOOGLEBOT_SMARTPHONE_UA,
        width=411,
        height=823,
        is_mobile=True,
        device_scale_factor=2.625,
    ),
    "Googlebot - Desktop": Device(
        label="Googlebot - Desktop",
        user_agent=GOOGLEBOT_DESKTOP_UA,
        width=1440,
        height=900,
    ),
    "Chrome - Android Mobile": Device(
        label="Chrome - Android Mobile",
        user_agent=CHROME_ANDROID_UA,
        width=412,
        height=915,
        is_mobile=True,
        device_scale_factor=2.625,
    ),
    "Chrome - iPhone": Device(
        label="Chrome - iPhone",
        user_agent=CHROME_IPHONE_UA,
        width=390,
        height=844,
        is_mobile=True,
        device_scale_factor=3.0,
    ),
    "Chrome - Windows": Device(
        label="Chrome - Windows",
        user_agent=CHROME_WINDOWS_UA,
        width=1440,
        height=900,
    ),
}

DEFAULT_DEVICE_LABEL = "Googlebot - Smartphone"


def get_device(label: str) -> Device:
    """Return the :class:`Device` for ``label`` (falls back to the default)."""

    return DEVICES.get(label, DEVICES[DEFAULT_DEVICE_LABEL])
