<div align="center">

![YT Audio Extractor — hero screenshot](docs/screenshots/hero.png)

# YT Audio Extractor

**A polished local web app for extracting audio from YouTube videos and playlists.**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-backend-000000?logo=flask&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-engine-FF0000?logo=youtube&logoColor=white)
![ffmpeg](https://img.shields.io/badge/ffmpeg-converter-007808?logo=ffmpeg&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow)

</div>

Search YouTube, preview audio in-browser before committing, extract one track or a whole playlist to MP3 / FLAC / WAV. Runs entirely on your machine — nothing leaves localhost except requests to YouTube itself. No cloud, no account, no telemetry.

I built this as an upgrade to my earlier static "yt-dlp command generator" — a real GUI on top of yt-dlp that handles the search, the queueing, the conversion, and the file management so you don't have to touch a terminal.

---

## Features

- 🔎 **Unified search bar** — paste a URL (single video or playlist) or type a search query
- ▶ **In-browser audio preview** — small play button on each result streams the audio directly via a yt-dlp-resolved stream URL, so you can decide *before* downloading
- 📦 **Batch extraction** — select multiple tracks, watch each one's progress in real time via Server-Sent Events
- 🎵 **MP3, FLAC, WAV** — best-quality VBR for MP3; lossless for FLAC and WAV
- 📁 **Direct-to-disk output** — files land in `~/Downloads/YT-Audio` and persist; no zip-and-download round trip through the browser
- 🍪 **Chrome cookies passthrough** — when Chrome is closed, the app probes whether it can read your local Chrome cookies and passes them to yt-dlp, so YouTube treats requests as authenticated
- ⏱ **Anti-bot pacing** — randomized 3-7 s pause between consecutive tracks, plus a 1 s interval between yt-dlp's internal requests during search
- 🚀 **One-click Windows launcher** — auto-cleans stale processes, verifies environment (Python, ffmpeg, JS runtime), installs Python deps on first run, opens the browser
- 🪟 **Open Folder that actually focuses** — uses a Win32 Alt-tap trick to bypass Windows' focus-stealing prevention, so the new Explorer window jumps in front of the browser

---

## Screenshots

### Search results with audio preview
![Search results](docs/screenshots/search-results.png)

Each row has a small ▶ button next to the duration. Click it to preview the audio without leaving the page — the button pulses while the stream URL resolves (~1-2 s on first click, cached after that), then fills amber while playing.

### Real-time extraction progress
![Progress panel](docs/screenshots/progress.png)

Server-Sent Events push state changes from the Python worker thread to the browser: per-track download progress, conversion status, completion count, and any errors. No polling.

### Done — open the folder
![Download panel](docs/screenshots/download.png)

Files persist in `~/Downloads/YT-Audio`. The Open Folder button uses an OS-level shim that raises File Explorer above the browser window.

### Launcher: environment checks + auto-cleanup
![Launcher console](docs/screenshots/launcher.png)

The Windows launcher kills any leftover Flask server on port 5000, then verifies every dependency before starting. Missing pieces get actionable error messages, not Python tracebacks.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask |
| Engine | yt-dlp (Python API), ffmpeg |
| Frontend | Vanilla HTML + CSS + JS (no framework, no build step, single file) |
| Realtime | Server-Sent Events for job progress |
| Launcher | Windows `.bat` with environment probes, auto-cleanup, and a one-time desktop-shortcut installer |
| Icon | Generated programmatically from a Pillow script (kept in repo) |

---

## Install & Run

### Prerequisites (all platforms)

- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/) (check "Add Python to PATH" on Windows)
- **ffmpeg** on PATH — [ffmpeg.org/download](https://ffmpeg.org/download.html), `brew install ffmpeg`, or `apt install ffmpeg`
- **A JavaScript runtime** on PATH — [Deno](https://deno.com) recommended (`winget install DenoLand.Deno` on Windows, `brew install deno` on macOS). yt-dlp needs one for current YouTube formats.

Python packages bootstrap themselves on first launch.

### Windows (recommended)

1. Clone or download this repo
2. Double-click `Create-Desktop-Shortcut.bat` **once** — drops a labeled, custom-icon shortcut on your desktop
3. From then on, launch from the desktop icon

### macOS / Linux

```bash
git clone <this repo>
cd <repo dir>
pip install -r requirements.txt
python app.py
```

The app opens at `http://localhost:5000` in your default browser.

---

## Usage

1. **Search** — type a query or paste a YouTube URL.
2. **Preview** — click the small ▶ next to any result to audition the audio.
3. **Pick** — tick the videos you want. **Select All** grabs the whole list.
4. **Choose format** — MP3, FLAC, or WAV. Quality is always set to best.
5. **Extract** — click **Extract Audio (N)**. The Progress panel shows the active track, a progress bar, and a per-track count. Between tracks the app pauses 3-7 s.
6. **Open Folder** — when the job finishes, click **Open Folder** to jump to `~/Downloads/YT-Audio` in File Explorer.

---

## Design decisions

A few choices worth explaining — the things that aren't obvious from reading the code.

### Why Server-Sent Events for progress?

The progress stream is one-way (server → browser) and the natural unit is a small JSON payload pushed whenever state changes. Polling at 500 ms intervals would have worked but wastes HTTP round-trips and adds latency for state changes. WebSockets would have been overkill — they're two-way and require dependency upgrades. SSE is built into the browser (`EventSource`), trivially proxied through Flask's `Response(generator, mimetype='text/event-stream')`, and matches the data flow exactly.

### Why direct-to-disk instead of zip-and-download?

The earlier design wrote files to a per-job temp dir, then served them as a zip through the browser when the user clicked Download. That's the standard web-app pattern, but it's a redundant round-trip for a *local* app — the files were already on the user's disk; making them stream through Flask just to land in `~/Downloads` is silly. The current version writes straight to `~/Downloads/YT-Audio` and offers an **Open Folder** button that opens File Explorer at that path. No zip, no Content-Disposition dance, no temp cleanup.

### Why Chrome cookies passthrough, and how does it fall back?

yt-dlp accepts a `cookiesfrombrowser=('chrome',)` option that reads your Chrome cookie DB and includes the session cookies on requests. This makes YouTube treat the app as a logged-in user, which dramatically reduces rate-limiting and bot-flagging. The catch: when Chrome is running on Windows, it locks the cookie database and the read fails with a `DownloadError`. Naive code would crash every request. The app probes once at startup by calling `yt_dlp.cookies.extract_cookies_from_browser('chrome', ...)` with a silent logger; on failure, a module-level `CHROME_COOKIES_AVAILABLE = False` flag flips and all downstream `_maybe_with_cookies()` calls quietly skip the option. The startup log says `Chrome cookies: enabled` or `Chrome cookies: disabled`. No request ever sees the cookie error.

### Why an Alt-tap before opening Explorer?

`os.startfile()` correctly spawns an Explorer window pointed at the output folder, but on Windows the new window often opens behind the browser. The culprit is [foreground-window lock](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow): a non-foreground process (Python) is not allowed to raise a window over the active app (the browser). The standard workaround is to simulate an Alt key down+up via `keybd_event`, which Windows treats as user-initiated input and which clears the foreground lock for the calling process. With the lock cleared, `SwitchToThisWindow` + `SetForegroundWindow` succeeds. The whole sequence runs in a daemon thread that polls `EnumWindows` for up to 2 s waiting for the Explorer window to appear.

### Why a custom launcher instead of a README "Quick Start"?

A README that says "open a terminal and run `python app.py`" filters out everyone who doesn't already use a terminal. `YT-Audio-Extractor.bat` is a 130-line script that:
- Kills any stale Flask listener on port 5000 (auto-recovery from previous runs)
- Probes for Python ≥ 3.10, pip, ffmpeg, and a JS runtime, with actionable install hints for any missing piece
- Installs Python deps (`pip install -r requirements.txt`) on first run with a Y/N prompt
- Launches the app and the browser
- Pauses on non-zero exit so error messages stay visible

Combined with `Create-Desktop-Shortcut.bat` (which uses `WScript.Shell` COM to drop a custom-icon `.lnk` on the user's desktop), the install reduces to "double-click an icon."

### Why generate the icon programmatically?

The icon is a 6-resolution `.ico` (16/32/48/64/128/256) rendered fresh at each size to avoid downsampling artifacts at small dimensions. `generate_icon.py` uses Pillow to draw the amber play triangle on the dark rounded-square background that matches the app's color palette. Keeping the generator in the repo means the icon can be regenerated or tweaked without manual image editing.

---

## Project layout

```
.
├── app.py                       # Flask backend: search, preview, extract, status (SSE), open-folder
├── templates/
│   └── index.html               # Single-page frontend (HTML + inline CSS + inline JS)
├── requirements.txt             # Python deps (flask, yt-dlp)
├── YT-Audio-Extractor.bat       # Windows launcher (cleanup → env checks → run)
├── Create-Desktop-Shortcut.bat  # One-time installer for the desktop shortcut
├── YT-Audio-Extractor.ico       # App icon (6 resolutions)
├── generate_icon.py             # Pillow-based regenerator for the icon
└── docs/
    └── screenshots/             # README screenshots
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[X] Python is not installed or not on PATH` | Install Python 3.10+, check "Add Python to PATH", re-run |
| `[X] ffmpeg is not installed or not on PATH` | Install ffmpeg, add its `bin` to PATH, restart your terminal |
| `[!] No JavaScript runtime found` | Install Deno (`winget install DenoLand.Deno`) |
| `Chrome cookies: disabled` at startup | Quit Chrome (kill background processes too) and relaunch |
| "This video is unavailable / private / age-restricted" | These can't be downloaded anonymously; skip them, or relaunch with cookies enabled |
| Search returns 429 | YouTube rate-limited your IP; wait a minute, or relaunch with cookies enabled |
| Port 5000 already in use | The launcher auto-kills the previous instance; if you still see this, end any stray `python.exe` in Task Manager |

---

## License

MIT
