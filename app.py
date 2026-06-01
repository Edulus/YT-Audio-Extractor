"""YT Audio Extractor - local Flask web app for downloading audio from YouTube."""

import atexit
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import yt_dlp
from yt_dlp.utils import DownloadError


app = Flask(__name__)

# All extracted audio files land here. Flat folder — yt-dlp will skip existing
# filenames rather than overwrite (yt-dlp's default postprocessor behavior).
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "YT-Audio")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory job registry. Each job tracks its own state; a lock guards updates.
JOBS: dict = {}
JOBS_LOCK = threading.Lock()

ALLOWED_FORMATS = {"mp3", "flac", "wav"}

JOB_TTL_SECONDS = 3600        # 1 hour — finished job entries dropped from registry
CLEANUP_INTERVAL_SECONDS = 1800  # 30 minutes

# Random pause between consecutive tracks in a multi-video extract, to avoid
# hammering YouTube with a predictable cadence.
INTER_TRACK_SLEEP_RANGE = (3, 7)


# ---------------------------------------------------------------------------
# Bundled binaries
# ---------------------------------------------------------------------------
# To spare users from installing ffmpeg + a JS runtime and editing PATH, the
# Windows launcher downloads ffmpeg.exe and deno.exe into bin/ next to this
# file (see fetch-binaries.ps1). We reference them here by explicit path.
#
# On macOS/Linux bin/ is normally absent and we transparently fall back to
# whatever is on PATH (e.g. `brew install ffmpeg` / `apt install ffmpeg`),
# so the Unix flow is unchanged.

BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
_EXE_SUFFIX = ".exe" if platform.system() == "Windows" else ""


def _bundled(name: str) -> str | None:
    """Return the absolute path to a binary bundled in bin/, or None if absent."""
    path = os.path.join(BIN_DIR, name)
    return path if os.path.isfile(path) else None


# Prepend bin/ to PATH so child processes prefer our bundled binaries. yt-dlp
# locates its Deno JS runtime by searching PATH, so this is how the bundled
# deno.exe gets picked up — there is no separate "deno path" option to set.
if os.path.isdir(BIN_DIR):
    os.environ["PATH"] = BIN_DIR + os.pathsep + os.environ.get("PATH", "")

# ffmpeg, by contrast, has an explicit yt-dlp option. None -> yt-dlp uses PATH.
FFMPEG_LOCATION = _bundled(f"ffmpeg{_EXE_SUFFIX}")


def _with_ffmpeg(opts: dict) -> dict:
    """Point yt-dlp at the bundled ffmpeg, if we have one."""
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION
    return opts


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------

def _ffmpeg_available() -> bool:
    return FFMPEG_LOCATION is not None or shutil.which("ffmpeg") is not None


class _SilentLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


# Probed in order of popularity; first one yt-dlp can read wins.
SUPPORTED_BROWSERS = ("chrome", "firefox", "edge", "brave", "opera", "chromium", "vivaldi")


def detect_browser() -> str | None:
    """Return the first browser whose cookies yt-dlp can read, or None.

    Reasons a browser can fail: not installed, currently running and locking the
    cookie DB (Windows), DPAPI key issues, etc. We probe once at startup by
    calling yt-dlp's cookie extractor directly; the first success is used for the
    lifetime of the process. If none succeed we run without cookies.
    """
    from yt_dlp.cookies import extract_cookies_from_browser
    for browser in SUPPORTED_BROWSERS:
        try:
            extract_cookies_from_browser(browser, logger=_SilentLogger())
            return browser
        except Exception:  # noqa: BLE001
            continue
    return None


# Set once at startup. If None, neither search nor extract will pass cookies.
DETECTED_BROWSER = detect_browser()


def _maybe_with_cookies(opts: dict) -> dict:
    """Add cookiesfrombrowser to an opts dict iff a browser's cookies are usable."""
    if DETECTED_BROWSER is not None:
        opts["cookiesfrombrowser"] = (DETECTED_BROWSER,)
    return opts


def _humanize_yt_dlp_error(message: str) -> str:
    """Translate common yt-dlp error strings into actionable hints."""
    lower = message.lower()
    if "ffmpeg" in lower and ("not found" in lower or "could not find" in lower):
        return ("ffmpeg is required but not on PATH. Install ffmpeg "
                "(https://ffmpeg.org/download.html) and restart.")
    if "deno" in lower or "node" in lower or "javascript" in lower or "ejs" in lower:
        return ("YouTube now requires a JavaScript runtime. Install Deno "
                "(https://deno.com) or Node.js, ensure it's on PATH, then retry.")
    if "private video" in lower:
        return "This video is private."
    if "video unavailable" in lower:
        return "This video is unavailable."
    if "sign in to confirm your age" in lower or "age" in lower and "restricted" in lower:
        return "This video is age-restricted and cannot be downloaded anonymously."
    return message


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com|music\.youtube\.com)/",
    re.IGNORECASE,
)


def _is_youtube_url(query: str) -> bool:
    q = query.strip()
    if not q:
        return False
    if q.startswith("http://") or q.startswith("https://"):
        return True
    return bool(URL_PATTERN.match(q))


def _format_duration(seconds) -> str:
    if seconds is None:
        return ""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds < 0:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _thumbnail_for(entry: dict) -> str:
    thumb = entry.get("thumbnail")
    if thumb:
        return thumb
    thumbnails = entry.get("thumbnails") or []
    if thumbnails:
        # Pick the highest-resolution one available.
        best = max(
            thumbnails,
            key=lambda t: (t.get("height") or 0) * (t.get("width") or 0),
        )
        if best.get("url"):
            return best["url"]
    vid_id = entry.get("id")
    if vid_id:
        return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
    return ""


def _video_url_for(entry: dict) -> str:
    url = entry.get("webpage_url") or entry.get("url")
    if url and url.startswith("http"):
        return url
    vid_id = entry.get("id")
    if vid_id:
        return f"https://www.youtube.com/watch?v={vid_id}"
    return ""


def _normalize_entry(entry: dict) -> dict:
    duration = entry.get("duration")
    return {
        "id": entry.get("id") or "",
        "url": _video_url_for(entry),
        "title": entry.get("title") or "(untitled)",
        "duration": duration,
        "duration_string": entry.get("duration_string") or _format_duration(duration),
        "thumbnail": _thumbnail_for(entry),
        "uploader": entry.get("uploader") or entry.get("channel") or "",
        "view_count": entry.get("view_count"),
    }


def _run_extract_info(target: str) -> dict:
    """Wrapper around yt-dlp's extract_info with the right options for metadata."""
    opts = _maybe_with_cookies({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        # 1s pause between requests yt-dlp makes internally during search/metadata.
        "sleep_interval_requests": 1,
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(target, download=False)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon")


@app.route("/")
def index():
    return render_template("index.html")


PAGE_SIZE = 10

@app.post("/api/search")
def api_search():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400

    offset = max(0, int(payload.get("offset") or 0))

    try:
        if _is_youtube_url(query):
            info = _run_extract_info(query)
        else:
            info = _run_extract_info(f"ytsearch{offset + PAGE_SIZE}:{query}")
    except DownloadError as exc:
        message = str(exc)
        if "HTTP Error 429" in message or "Too Many Requests" in message:
            return jsonify({"error": "YouTube is rate-limiting. Try again in a minute."}), 429
        return jsonify({"error": f"Could not fetch: {message}"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Unexpected error: {exc}"}), 500

    if info is None:
        return jsonify({"error": "No results"}), 404

    info_type = info.get("_type")

    # Single video — extract_info returns a video info dict directly.
    if info_type in (None, "video"):
        normalized = _normalize_entry(info)
        return jsonify({
            "type": "video",
            "title": normalized["title"],
            "results": [normalized],
            "has_more": False,
        })

    # Playlist or search results — both come back as _type=playlist with entries[].
    entries = [e for e in (info.get("entries") or []) if e]
    # For paginated search, slice to just the new page.
    is_real_playlist = _is_youtube_url(query) and info_type == "playlist"
    if not is_real_playlist:
        page = entries[offset:offset + PAGE_SIZE]
        has_more = len(page) == PAGE_SIZE
    else:
        page = entries
        has_more = False

    normalized = [_normalize_entry(e) for e in page]

    return jsonify({
        "type": "playlist" if is_real_playlist else "video",
        "title": info.get("title") or "Search results",
        "results": normalized,
        "has_more": has_more,
    })


# ---------------------------------------------------------------------------
# Preview (audio-only stream URL for in-browser playback)
# ---------------------------------------------------------------------------

# Cache resolved stream URLs by video_id. yt-dlp's signed URLs are valid for
# several hours; we cap at 4 hours to leave a safety margin.
PREVIEW_CACHE: dict = {}
PREVIEW_CACHE_LOCK = threading.Lock()
PREVIEW_CACHE_TTL_SECONDS = 4 * 3600

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def _resolve_audio_stream(video_id: str) -> dict:
    """Return {'url': ..., 'mime': ...} for the best audio-only stream."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = _maybe_with_cookies({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best",
        "noplaylist": True,
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise RuntimeError("No stream info returned")

    # Prefer the format yt-dlp selected (info['url'] reflects the resolved best
    # match), but if missing, walk the 'formats' list ourselves.
    stream_url = info.get("url")
    ext = info.get("ext") or ""
    if not stream_url:
        for f in info.get("formats") or []:
            if f.get("vcodec") == "none" and f.get("url"):
                stream_url = f["url"]
                ext = f.get("ext") or ""
                break

    if not stream_url:
        raise RuntimeError("No audio-only format found")

    mime_map = {"m4a": "audio/mp4", "mp4": "audio/mp4", "webm": "audio/webm", "ogg": "audio/ogg"}
    mime = mime_map.get(ext.lower(), "audio/mp4")
    return {"url": stream_url, "mime": mime}


@app.get("/api/preview/<video_id>")
def api_preview(video_id: str):
    if not _VIDEO_ID_RE.match(video_id):
        return jsonify({"error": "Invalid video id"}), 400

    now = time.time()
    with PREVIEW_CACHE_LOCK:
        cached = PREVIEW_CACHE.get(video_id)
        if cached and (now - cached["resolved_at"]) < PREVIEW_CACHE_TTL_SECONDS:
            return jsonify({"url": cached["url"], "mime": cached["mime"]})

    try:
        data = _resolve_audio_stream(video_id)
    except DownloadError as exc:
        return jsonify({"error": _humanize_yt_dlp_error(str(exc))}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not resolve audio: {exc}"}), 500

    with PREVIEW_CACHE_LOCK:
        PREVIEW_CACHE[video_id] = {**data, "resolved_at": now}

    return jsonify(data)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _job_snapshot(job: dict) -> dict:
    """Return a copy of job state safe to serialize/send over the wire."""
    return {
        "status": job["status"],
        "total": job["total"],
        "completed": job["completed"],
        "current_progress": job.get("current_progress", 0.0),
        "current_title": job.get("current_title", ""),
        "errors": list(job.get("errors", [])),
    }


def _update_job(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _run_extract_job(job_id: str, videos: list, audio_format: str) -> None:
    """Worker thread: download and convert each requested video."""
    # All jobs share OUTPUT_DIR (flat). yt-dlp's default postprocessor behavior
    # is to skip files that already exist, so duplicate titles won't clobber.

    def progress_hook(d):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                job["current_progress"] = (downloaded / total) if total else 0.0
                job["status"] = "downloading"
                info = d.get("info_dict") or {}
                if info.get("title"):
                    job["current_title"] = info["title"]
            elif d.get("status") == "finished":
                job["current_progress"] = 1.0
                job["status"] = "converting"

    def postprocessor_hook(d):
        # Capture the final audio file path. Multiple postprocessors run per video
        # (FFmpegExtractAudio, MoveFiles, ...) so we dedupe by path. Counting completion
        # is done in the main loop, not here.
        if d.get("status") != "finished":
            return
        info = d.get("info_dict") or {}
        path = info.get("filepath") or info.get("_filename")
        if not path:
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext != audio_format:
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            if path not in job["files"]:
                job["files"].append(path)

    ydl_opts = _with_ffmpeg(_maybe_with_cookies({
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            # Hardcoded to '0' (best quality). MP3 = ~V0 VBR; ignored for FLAC/WAV.
            "preferredquality": "0",
        }],
        "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,  # suppress yt-dlp's stdout progress bar; we use progress_hook
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "writethumbnail": False,
        "noplaylist": True,  # one URL per call; never expand to a playlist
    }))

    for i, video in enumerate(videos):
        # Between consecutive tracks, pause for a randomized 3-7 seconds. Skipped
        # before the first track and never runs for single-video jobs.
        if i > 0:
            time.sleep(random.uniform(*INTER_TRACK_SLEEP_RANGE))

        url = video.get("url") or (
            f"https://www.youtube.com/watch?v={video['id']}" if video.get("id") else None
        )
        if not url:
            with JOBS_LOCK:
                JOBS[job_id]["errors"].append("Missing URL for one of the selected videos.")
            continue

        _update_job(job_id, current_progress=0.0, current_title=video.get("title") or url)

        with JOBS_LOCK:
            files_before = len(JOBS[job_id]["files"])
            errors_before = len(JOBS[job_id]["errors"])

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except DownloadError as exc:
            with JOBS_LOCK:
                JOBS[job_id]["errors"].append(
                    f"{video.get('title') or url}: {_humanize_yt_dlp_error(str(exc))}"
                )
        except Exception as exc:  # noqa: BLE001
            with JOBS_LOCK:
                JOBS[job_id]["errors"].append(f"{video.get('title') or url}: {exc}")

        # ignoreerrors=True swallows failures inside yt-dlp; detect success by whether
        # the postprocessor produced a new file for this video.
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                continue
            if len(job["files"]) > files_before:
                job["completed"] += 1
            elif len(job["errors"]) == errors_before:
                job["errors"].append(f"{video.get('title') or url}: extraction failed")

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["status"] = "done"
            job["finished_at"] = time.time()
            job["current_progress"] = 1.0
            job["current_title"] = ""


@app.post("/api/extract")
def api_extract():
    payload = request.get_json(silent=True) or {}
    videos = payload.get("videos") or []
    audio_format = (payload.get("format") or "mp3").lower()

    if not isinstance(videos, list) or not videos:
        return jsonify({"error": "No videos provided"}), 400
    if audio_format not in ALLOWED_FORMATS:
        return jsonify({"error": f"Unsupported format: {audio_format}"}), 400

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "total": len(videos),
            "completed": 0,
            "current_progress": 0.0,
            "current_title": "",
            "files": [],
            "errors": [],
            "format": audio_format,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_extract_job,
        args=(job_id, videos, audio_format),
        daemon=True,
        name=f"extract-{job_id[:8]}",
    )
    thread.start()

    return jsonify({"job_id": job_id, "total": len(videos), "output_dir": OUTPUT_DIR})


@app.get("/api/status/<job_id>")
def api_status(job_id: str):
    """Server-Sent Events stream of job progress."""

    def generate():
        last_payload = None
        while True:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                snapshot = _job_snapshot(job) if job is not None else None

            if snapshot is None:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                return

            # Only push frames when something changed, but always emit terminal state.
            if snapshot != last_payload:
                yield f"data: {json.dumps(snapshot)}\n\n"
                last_payload = snapshot

            if snapshot["status"] in ("done", "error"):
                return

            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _focus_explorer_window(path: str) -> None:
    """Find the Explorer window for `path` and force it to the foreground.

    Windows' focus-stealing prevention normally blocks a non-foreground process
    (we are not foreground; the browser is) from raising a newly-spawned window.
    The standard workaround is to simulate an Alt key tap — which clears the
    foreground lock for our process — and then call SetForegroundWindow.

    Runs in a daemon thread because we may have to poll for ~2 s while Explorer
    starts up. No-op on non-Windows.
    """
    if platform.system() != "Windows":
        return

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]

    target_title = os.path.basename(os.path.normpath(path))
    if not target_title:
        return
    target_lower = target_title.lower()

    enum_proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    state = {"hwnd": 0}

    def _enum_cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.lower() == target_lower:
            state["hwnd"] = hwnd
            return False  # stop enumerating
        return True

    enum_proc = enum_proc_t(_enum_cb)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        state["hwnd"] = 0
        user32.EnumWindows(enum_proc, 0)
        if state["hwnd"]:
            # Alt-tap clears the foreground lock so SetForegroundWindow works.
            # keybd_event constants: 0x12 = VK_MENU (Alt), 0x0002 = KEYEVENTF_KEYUP.
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 0x0002, 0)
            user32.SwitchToThisWindow(state["hwnd"], True)
            user32.SetForegroundWindow(state["hwnd"])
            return
        time.sleep(0.05)


def _open_in_file_manager(path: str) -> None:
    """Open `path` (a folder) in the OS file manager. Cross-platform.

    On Windows, also kicks off a background thread to bring the new Explorer
    window to the foreground (Windows otherwise blocks the focus change because
    the browser, not us, is the active app).
    """
    system = platform.system()
    if system == "Windows":
        os.startfile(path)  # type: ignore[attr-defined]
        threading.Thread(target=_focus_explorer_window, args=(path,), daemon=True).start()
    elif system == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


@app.post("/api/open-folder")
def api_open_folder():
    """Open the output folder in the user's file manager.

    No payload required — this app has exactly one output folder. The endpoint
    is just a privileged shim because the browser can't open a local file:// URL
    from an http:// origin.
    """
    if not os.path.isdir(OUTPUT_DIR):
        return jsonify({"error": "Output folder does not exist"}), 404
    try:
        _open_in_file_manager(OUTPUT_DIR)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not open folder: {exc}"}), 500
    return jsonify({"opened": OUTPUT_DIR})


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def _cleanup_old_jobs() -> None:
    """Drop finished job entries older than JOB_TTL_SECONDS from the registry.

    Output files in OUTPUT_DIR are permanent — we never delete them. Only the
    in-memory job state is trimmed so the JOBS dict doesn't grow unbounded.
    """
    now = time.time()
    with JOBS_LOCK:
        for job_id, job in list(JOBS.items()):
            finished_at = job.get("finished_at")
            if job["status"] in ("done", "error") and finished_at and (now - finished_at) > JOB_TTL_SECONDS:
                JOBS.pop(job_id, None)


def _cleanup_loop() -> None:
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            _cleanup_old_jobs()
        except Exception:  # noqa: BLE001
            # Cleanup must never crash the loop.
            pass


def _open_browser() -> None:
    webbrowser.open("http://localhost:5000")


def main() -> None:
    if not _ffmpeg_available():
        if platform.system() == "Windows":
            sys.stderr.write(
                "ERROR: ffmpeg was not found. Audio conversion requires ffmpeg.\n"
                "  Run YT-Audio-Extractor.bat instead of launching app.py directly —\n"
                "  it auto-downloads ffmpeg.exe into bin/ on first launch.\n"
            )
        else:
            sys.stderr.write(
                "ERROR: ffmpeg was not found on PATH. Audio conversion requires ffmpeg.\n"
                "  Install: brew install ffmpeg  (macOS)  /  apt install ffmpeg  (Linux)\n"
                "  Then restart this app.\n"
            )
        sys.exit(1)

    sys.stderr.write(
        f"ffmpeg: {'bundled (bin/)' if FFMPEG_LOCATION else 'system PATH'}\n"
    )
    sys.stderr.write(
        f"JS runtime: {'bundled deno (bin/)' if _bundled('deno' + _EXE_SUFFIX) else 'system PATH'}\n"
    )

    if DETECTED_BROWSER is not None:
        sys.stderr.write(f"Cookie source: {DETECTED_BROWSER}\n")
    else:
        sys.stderr.write(
            "No browser cookies available — running without authentication "
            f"(tried: {', '.join(SUPPORTED_BROWSERS)}). If a supported browser is "
            "installed, close it so its cookie database isn't locked, then restart.\n"
        )

    threading.Thread(target=_cleanup_loop, daemon=True, name="ytaudio-cleanup").start()
    threading.Timer(1.0, _open_browser).start()
    # 127.0.0.1 only — local-only app, never bind to 0.0.0.0.
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
