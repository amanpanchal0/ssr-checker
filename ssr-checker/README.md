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

Local use needs nothing beyond the setup above. To deploy to **Streamlit
Community Cloud**, Chromium's system libraries are not present by default, so
add the files in this repo:

- `packages.txt` - apt packages Chromium needs.
- `setup.sh` - installs the Playwright Chromium browser at build time.

Point Streamlit Cloud at `ssr-checker/app.py` as the main file.
