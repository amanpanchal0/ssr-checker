# SSR Checker (Streamlit)

A Streamlit port of a "check SSR" style tool. It loads a URL twice in headless
Chromium:

- **Server-side rendered version** - JavaScript disabled (what a non-executing
  crawler sees).
- **Regular rendered version** - JavaScript enabled (the hydrated page).

It then shows both side by side with synced scrolling, a pixel-difference
highlight, a visible-text/DOM diff, and a plain-language SSR verdict.

## Features

- User-agent presets: Googlebot (Smartphone/Desktop) and Chrome
  (Android/iPhone/Windows), each with the right viewport.
- Two-pane comparison with **synced scrolling**.
- **Pixel diff** overlay (red = differs), plus a mismatch percentage.
- **HTML / visible-text diff** so you can see exactly what JavaScript adds.
- **SSR verdict**: fully / partially / client-only rendered.

## Requirements

- Python 3.10+
- Chromium for Playwright (installed in the next step)

## Setup

```powershell
cd ssr-checker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```powershell
streamlit run app.py
```

Then open the URL Streamlit prints (default http://localhost:8501), paste a URL,
pick a user-agent, and press **Check**.

## How it works

| Module | Responsibility |
| --- | --- |
| `ssr_checker/devices.py` | User-agent + viewport presets. |
| `ssr_checker/renderer.py` | Playwright capture (JS on and off), redirects, timeouts. |
| `ssr_checker/diff.py` | SSR verdict, pixel diff, visible-text diff. |
| `ssr_checker/component.py` | Injects screenshots into the synced-scroll HTML component. |
| `app.py` | Streamlit UI, caching and tabs. |

Results are cached for 10 minutes per `(URL, user-agent)`; use **Re-run** to
bypass the cache.

## Deployment notes

### Streamlit Community Cloud

Everything needed is already in this folder:

| File | Purpose |
| --- | --- |
| `requirements.txt` | Python deps. Playwright is **pinned to `1.49.0`** because newer builds need system libs that are missing from Community Cloud's Debian 11 image. |
| `packages.txt` | Chromium's system libraries, installed with `apt-get`. |
| `runtime.txt` | Pins Python to 3.11. |
| `setup.sh` | Local convenience only - Community Cloud does **not** run it. |

The app downloads the Chromium browser binary **on first run** (see
`ensure_chromium()` in `ssr_checker/renderer.py`), because Community Cloud never
runs a shell setup script. The first load takes ~30-60s; later runs are cached.

Steps:

1. Push this folder to a GitHub repo.
2. On <https://share.streamlit.io> click **New app** and pick the repo/branch.
3. Set **Main file path** to `app.py` if this folder is the repo root, or
   `ssr-checker/app.py` if it lives inside a larger repo.
   (Streamlit looks for `requirements.txt` / `packages.txt` next to the
   entrypoint first, so both layouts work.)
4. Deploy. If the first build fails on apt, wait a few minutes and reboot -
   the Debian security mirror occasionally expires and then recovers.

### Other platforms

Docker/Render/Fly etc. just need the `packages.txt` libraries installed via the
distro package manager and `playwright install chromium` run once at build time.

### Local

Nothing beyond the setup above.
