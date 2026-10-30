"""YT Audio Extractor - local Flask web app for downloading audio from YouTube."""

import atexit
import json
import os
import platform
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import yt_dlp
from yt_dlp.extractor import get_info_extractor
from yt_dlp.postprocessor import FFmpegPostProcessor, PostProcessor
from yt_dlp.utils import DownloadCancelled, DownloadError


app = Flask(__name__)

# All extracted audio files land here. Flat folder; tracks that already exist
# here (or are in the download history) are skipped, never overwritten.
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "YT-Audio")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Download history: one "youtube <video id> <format>" line per finished track.
# A video is never fetched again in a format listed here, even if its file was
# renamed or moved; other formats are still allowed. Delete its line (or the
# whole file) to allow a re-download.
HISTORY_FILE = os.path.join(OUTPUT_DIR, "download-history.txt")
HISTORY_LOCK = threading.Lock()

# In-memory job registry. Each job tracks its own state; a lock guards updates.
JOBS: dict = {}
JOBS_LOCK = threading.Lock()

ALLOWED_FORMATS = {"best", "opus", "mp3", "flac", "wav"}

# Extensions the "best" option can produce (yt-dlp keeps the source codec).
BEST_AUDIO_EXTS = {"opus", "m4a", "webm", "ogg", "aac", "mp3", "flac", "wav"}

# Formats that cannot carry embedded cover art (yt-dlp's EmbedThumbnail
# supports mp3, m4a, ogg/opus and flac). Tags are still written to these.
NO_ARTWORK_FORMATS = {"wav"}

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


def _ejs_available() -> bool:
    """True if the yt-dlp-ejs challenge solver package is installed."""
    try:
        import yt_dlp_ejs  # noqa: F401
        return True
    except ImportError:
        return False


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
# Can be switched off at runtime by _disable_cookies() if YouTube rejects them.
DETECTED_BROWSER = detect_browser()
_COOKIE_LOCK = threading.Lock()


def _maybe_with_cookies(opts: dict) -> dict:
    """Add cookiesfrombrowser to an opts dict iff a browser's cookies are usable."""
    if DETECTED_BROWSER is not None:
        opts["cookiesfrombrowser"] = (DETECTED_BROWSER,)
    return opts


def _disable_cookies(reason: str) -> bool:
    """Stop sending browser cookies for the rest of this session.

    Returns True if cookies were on (so the caller should retry without them),
    False if they were already off (a retry would hit the same error).
    """
    global DETECTED_BROWSER
    with _COOKIE_LOCK:
        if DETECTED_BROWSER is None:
            return False
        sys.stderr.write(
            f"YouTube rejected requests using {DETECTED_BROWSER} cookies ({reason}). "
            "Continuing without cookies for this session.\n"
        )
        DETECTED_BROWSER = None
        return True


# yt-dlp colors its error text with ANSI escape codes; strip them for the UI.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean_error(message: str) -> str:
    """Strip ANSI color codes and yt-dlp's 'ERROR: [youtube] <id>:' prefix."""
    msg = _ANSI_RE.sub("", message).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", msg)
    return msg


def _is_reload_error(message: str) -> bool:
    """YouTube's 'The page needs to be reloaded' rejection."""
    return "page needs to be reloaded" in message.lower()


def _humanize_yt_dlp_error(message: str) -> str:
    """Translate common yt-dlp error strings into actionable hints."""
    message = _clean_error(message)
    lower = message.lower()
    if "ffmpeg" in lower and ("not found" in lower or "could not find" in lower):
        return ("ffmpeg is required but not on PATH. Install ffmpeg "
                "(https://ffmpeg.org/download.html) and restart.")
    if _is_reload_error(message):
        return ("YouTube rejected the request. Close the app and re-run "
                "YT-Audio-Extractor.bat so it updates yt-dlp, then try again.")
    if "deno" in lower or "node" in lower or "javascript" in lower or "ejs" in lower:
        return ("YouTube now requires a JavaScript runtime. Install Deno "
                "(https://deno.com) or Node.js, ensure it's on PATH, then retry.")
    if "private video" in lower:
        return "This video is private."
    if "video unavailable" in lower:
        return "This video is unavailable."
    if "sign in to confirm your age" in lower or ("age" in lower and "restricted" in lower):
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


class _ErrorCollector(_SilentLogger):
    """Logger that remembers error lines, so we can inspect swallowed failures."""

    def __init__(self):
        self.errors: list[str] = []

    def error(self, msg, *a, **k):
        self.errors.append(str(msg))


def _run_extract_info(target: str) -> dict:
    """Wrapper around yt-dlp's extract_info with the right options for metadata.

    ignoreerrors=True means a rejected request comes back as None instead of
    raising, so we collect error lines and retry once without cookies if
    YouTube rejected the signed-in request.
    """
    def attempt():
        logger = _ErrorCollector()
        opts = _maybe_with_cookies({
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            # 1s pause between requests yt-dlp makes internally during search/metadata.
            "sleep_interval_requests": 1,
            "logger": logger,
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False), logger.errors

    info, errors = attempt()
    if info is None and any(_is_reload_error(e) for e in errors):
        if _disable_cookies("search"):
            info, errors = attempt()
    if info is None and errors:
        raise DownloadError(errors[-1])
    return info


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
        return jsonify({"error": f"Could not fetch: {_humanize_yt_dlp_error(message)}"}), 400
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

    def attempt():
        opts = _maybe_with_cookies({
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "format": "bestaudio/best",
            "noplaylist": True,
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = attempt()
    except DownloadError as exc:
        if _is_reload_error(str(exc)) and _disable_cookies("preview"):
            info = attempt()
        else:
            raise

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

# What the Progress panel says the worker is doing. Steps 1-7 are the
# preparation before the audio download starts; yt-dlp reports most of them
# as log lines, which _PhaseLogger turns into plain English.
PREP_STEPS = 7

# (substring of a yt-dlp log line, prep step or None, text). First match wins,
# so "player API JSON" must come before "Downloading player ".
_PHASE_RULES = [
    ("Extracting cookies from", 2, None),  # text names the browser, see below
    ("Extracting URL", 3, "Asking YouTube about the video"),
    ("Downloading webpage", 3, "Asking YouTube about the video"),
    ("player API JSON", 3, "Asking YouTube about the video"),
    ("Downloading player ", 4, "Solving YouTube's JavaScript challenge (with Deno)"),
    ("Solving JS challenges", 4, "Solving YouTube's JavaScript challenge (with Deno)"),
    ("m3u8 information", 5, "Choosing the best audio stream"),
    ("format(s)", 5, "Choosing the best audio stream"),
    ("[ExistingFileGuard]", 6, "Last check: making sure it isn't already in your folder"),
    ("video thumbnail", 7, "Fetching the cover art"),
    ("[ThumbnailsConvertor]", 7, "Fetching the cover art"),
    ("[SquareCover]", 7, "Cropping the cover art square"),
    ("[ExtractAudio] Not converting", None, "Keeping the original audio (no conversion needed)"),
    ("[ExtractAudio]", None, "Converting the audio"),
    ("[Metadata]", None, "Writing the tags (title, artist, date)"),
    ("[EmbedThumbnail]", None, "Embedding the cover art"),
]


def _set_phase(job_id: str, text: str, step: int | None = None) -> None:
    _update_job(job_id, phase={"step": step, "of": PREP_STEPS, "text": text})


class _PhaseLogger:
    """yt-dlp logger that turns its progress messages into the job's phase.

    With a logger set, yt-dlp sends every screen message to debug(), even in
    quiet mode. Errors still go to stderr, as they did without a logger.
    """

    def __init__(self, job_id: str):
        self._job_id = job_id

    def debug(self, msg: str) -> None:
        for needle, step, text in _PHASE_RULES:
            if needle in msg:
                if text is None:
                    browser = msg.split("from", 1)[-1].strip().split()[0].capitalize()
                    text = f"Reading your {browser} sign-in cookies"
                _set_phase(self._job_id, text, step)
                return

    info = debug

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        sys.stderr.write(msg + "\n")


def _job_snapshot(job: dict) -> dict:
    """Return a copy of job state safe to serialize/send over the wire."""
    return {
        "status": job["status"],
        "total": job["total"],
        "completed": job["completed"],
        "skipped": job.get("skipped", 0),
        "current_progress": job.get("current_progress", 0.0),
        "current_title": job.get("current_title", ""),
        "errors": list(job.get("errors", [])),
        "items": [dict(i) for i in job.get("items", [])],
        "paused": job.get("pause_requested", False),
        "stopping": job.get("stop_requested", False),
        "stopped": job.get("stopped", False),
        "phase": job.get("phase"),
    }


class _StopRequested(DownloadCancelled):
    """Raised inside the worker (even from yt-dlp's progress hook) to abort."""
    msg = "Stopped by user"


def _check_control(job_id: str) -> None:
    """Block while the job is paused; raise _StopRequested once it's stopped.

    Called between tracks, during the pause between downloads, and from the
    progress hook, so a pause or stop takes effect mid-download too. Blocking
    in the hook just holds the connection; yt-dlp resumes (or reconnects with
    a Range request) once it returns. ffmpeg conversion can't be interrupted,
    so a pause or stop during conversion lands right after it.
    """
    while True:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None or job.get("stop_requested"):
                raise _StopRequested()
            if not job.get("pause_requested"):
                return
        time.sleep(0.25)


def _sleep_with_control(job_id: str, seconds: float) -> None:
    """time.sleep() that still honours pause and stop."""
    end = time.time() + seconds
    while True:
        _check_control(job_id)
        remaining = end - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))


def _remove_partial_files(partial_path: str | None) -> None:
    """Delete what a cancelled download leaves behind: the .part file, yt-dlp's
    .ytdl resume file, and the not-yet-embedded thumbnail."""
    if not partial_path:
        return
    base = partial_path[:-5] if partial_path.endswith(".part") else partial_path
    stem = os.path.splitext(base)[0]
    for path in (partial_path, base + ".ytdl", *(f"{stem}.{e}" for e in ("webp", "jpg", "png"))):
        try:
            os.remove(path)
        except OSError:
            pass


def _update_job(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _set_item(job_id: str, index: int, status: str) -> None:
    """Set one queue entry's status: queued / active / done / have / failed."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None and 0 <= index < len(job.get("items", [])):
            job["items"][index]["status"] = status


_YOUTUBE_IE = get_info_extractor("Youtube")


def _video_id_for(url: str, video: dict) -> str | None:
    """The YouTube video id, from the search results or else from the URL."""
    if video.get("id"):
        return video["id"]
    return _YOUTUBE_IE.get_temp_id(url) if _YOUTUBE_IE.suitable(url) else None


def _history_key(video_id: str, audio_format: str) -> str:
    """One history line per video and format, e.g. 'youtube jNQXAC9IVRw mp3'.

    v3.6 wrote lines without a format ('youtube <id>'); those never match a
    key, so they no longer block anything.
    """
    return f"youtube {video_id} {audio_format}"


def _in_history(key: str) -> bool:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return any(line.strip() == key for line in f)
    except FileNotFoundError:
        return False


def _add_to_history(key: str) -> None:
    with HISTORY_LOCK, open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")


# ---------------------------------------------------------------------------
# Taking video words out of track titles
# ---------------------------------------------------------------------------
# "(Official Video)", "| Official Audio", "(Visualizer)" and the like describe
# the YouTube upload, not the track, and so does "(Video Edit)". Words that
# identify a version, such as "(Official Remix)" or "(Radio Edit)", stay, and
# so does a song that is really called "Video". Clean-Audio-Filenames.ps1
# holds the same rules for
# files already saved: change both together.

_OFFICIAL = r"offici(?:al|el|elle)"
_MEDIA = r"vid[eé]o|videoclip|clip|audio|visuali[sz]er|visual"
_FILLER = rf"{_OFFICIAL}|music|lyrics?|full|original|hd|hq|4k|8k"
_SEP = r"|｜•·/_~:"
_DASH = r"\-–—"

_BRACKET_RE = re.compile(
    r"[(\[{（［【「『]"
    r"([^()\[\]{}（）［］【】「」『』]*)"
    r"[)\]}）］】」』]")
_UNDERSCORED_RE = re.compile(r"(?<!\S)_([^_]+)_(?!\S)")  # "|" or similar in a saved name
_ONLY_NOISE_RE = re.compile(
    rf"vid[eé]o\s+edit|(?:(?:{_FILLER})\s+)*(?:{_MEDIA}|{_OFFICIAL}|hd|hq|4k|8k)(?:\s+(?:{_FILLER}|{_MEDIA}))*",
    re.IGNORECASE)
_OFFICIAL_MEDIA_RE = re.compile(
    rf"\b{_OFFICIAL}\b(?!.*\b(?:remix|mix|version|bootleg)\b).*\b(?:{_MEDIA}|lyrics?)\b",
    re.IGNORECASE)
_LOOSE_OFFICIAL_RE = re.compile(
    rf"(?:^|\s*[{_SEP}]+\s*|\s+[{_DASH}]*\s*){_OFFICIAL}(?:\s+(?:music|lyrics?|hd|4k))*"
    rf"(?:\s+(?:{_MEDIA}))+(?:\s+(?:hd|hq|4k|8k|edit))?(?=\s*(?:$|[{_SEP}{_DASH}(\[]))",
    re.IGNORECASE)
_LOOSE_TAIL_RE = re.compile(
    rf"(?:\s*[{_SEP}]+\s*|\s+[{_DASH}]+\s*)(?:{_OFFICIAL}|music video|lyrics? video|vid[eé]o edit|visuali[sz]er)\s*$",
    re.IGNORECASE)
_LOOSE_ENDS_RE = re.compile(rf"^[\s{_SEP}{_DASH}]+|[\s{_SEP}{_DASH}]+$")


def clean_title(title: str) -> str:
    """'Song (Official Video)' -> 'Song'. Other titles come back untouched."""
    def drop_noise(match):
        inner = match.group(1).strip()
        noise = _ONLY_NOISE_RE.fullmatch(inner) or _OFFICIAL_MEDIA_RE.search(inner)
        return " " if noise else match.group(0)

    cleaned = _BRACKET_RE.sub(drop_noise, title)
    cleaned = _UNDERSCORED_RE.sub(drop_noise, cleaned)
    cleaned = _LOOSE_OFFICIAL_RE.sub("", cleaned)
    cleaned = _LOOSE_TAIL_RE.sub("", cleaned)
    if cleaned == title:
        return title
    cleaned = re.sub(r"\s{2,}", " ", _LOOSE_ENDS_RE.sub("", cleaned))
    return cleaned or title


class CleanTitlePP(PostProcessor):
    """Save and tag the track under its cleaned title.

    Runs before download, after ExistingFileGuardPP has looked for the file.
    """

    def run(self, info):
        if info.get("title"):
            info["title"] = clean_title(info["title"])
        return [], info


# ---------------------------------------------------------------------------
# Recognizing tracks already in the target folder
# ---------------------------------------------------------------------------
# Since v3.6 every file carries its source URL in a tag (FFmpegMetadata writes
# webpage_url as 'purl'/'comment'). Reading that back lets us recognize a
# track the user renamed. Only these fields are read, and only an exact
# watch-page URL counts: the embedded description often links other videos.

_SOURCE_URL_RE = re.compile(r"https?://(?:www\.|music\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})")
# ID3 (mp3), Vorbis comments (opus/ogg/flac), MP4 (m4a). WAV is read separately.
_SOURCE_TAG_KEYS = ("TXXX:purl", "TXXX:comment", "purl", "comment", "\xa9cmt")


def _match_source_url(text) -> str | None:
    m = _SOURCE_URL_RE.fullmatch(str(text).strip())
    return m.group(1) if m else None


def _wav_source_video_id(path: str) -> str | None:
    """Read the ICMT comment from a WAV's RIFF LIST/INFO chunk (mutagen can't)."""
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return None
        while True:
            header = f.read(8)
            if len(header) < 8:
                return None
            chunk_id, size = header[:4], struct.unpack("<I", header[4:])[0]
            if chunk_id != b"LIST":
                f.seek(size + (size & 1), 1)  # chunks are padded to even sizes
                continue
            data = f.read(size)
            if size & 1:
                f.seek(1, 1)
            if data[:4] != b"INFO":
                continue
            pos = 4
            while pos + 8 <= len(data):
                sub_id, sub_size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
                if sub_id == b"ICMT":
                    text = data[pos + 8:pos + 8 + sub_size].rstrip(b"\0")
                    return _match_source_url(text.decode("utf-8", "replace"))
                pos += 8 + sub_size + (sub_size & 1)


def _source_video_id(path: str) -> str | None:
    """The YouTube video id tagged into a file by this app (v3.6+), or None."""
    try:
        if path.lower().endswith(".wav"):
            return _wav_source_video_id(path)
        import mutagen  # installed with yt-dlp[default]
        tags = getattr(mutagen.File(path), "tags", None)
        if not tags:
            return None
        for key in _SOURCE_TAG_KEYS:
            try:
                value = tags.get(key)
            except (KeyError, ValueError):  # Vorbis comments reject some key names
                continue
            if not value:
                continue
            texts = getattr(value, "text", value)
            for text in texts if isinstance(texts, list) else [texts]:
                video_id = _match_source_url(text)
                if video_id:
                    return video_id
    except Exception:  # noqa: BLE001  (unreadable or not really audio: no id)
        return None
    return None


def _format_exts(audio_format: str) -> set:
    """File extensions that count as 'this format' for the already-have check."""
    return BEST_AUDIO_EXTS if audio_format == "best" else {audio_format}


def _tagged_ids_in(folder: str, exts: set) -> set:
    """Video ids of the files of these formats in folder, read from their tags."""
    ids = set()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return ids
    for entry in entries:
        if entry.is_file() and os.path.splitext(entry.name)[1].lower().lstrip(".") in exts:
            video_id = _source_video_id(entry.path)
            if video_id:
                ids.add(video_id)
    return ids


def _existing_file_for(ydl, info: dict, exts: set) -> str | None:
    """A file in the target folder that this video would be saved over, if any.

    Looks for the cleaned title and for the full title, which is how files
    were named before v4.7.
    """
    title = info.get("title") or ""
    for name in dict.fromkeys((clean_title(title), title)):
        for ext in exts:
            path = ydl.prepare_filename({**info, "title": name, "ext": ext})
            if os.path.isfile(path):
                return path
    return None


def _same_name_is_ours(path: str, video_id: str | None) -> bool:
    """Should a same-name file be recorded in the history as this video?

    Yes unless its tag proves it came from a different video (then we still
    skip, because saving would overwrite it, but don't record this video).
    """
    existing = _source_video_id(path)
    return existing is None or existing == video_id


def _already_have(ydl, video: dict, key: str | None, audio_format: str,
                  video_id: str | None, folder_ids: set) -> bool:
    """True if this video+format is in the history or already in the job's folder.

    Checks, cheapest first: the download history; a file in the folder whose
    tag says it is this video (catches renamed files); a file with the name
    this video would be saved under. Uses the search-result title, so it costs
    no request to YouTube. ExistingFileGuardPP repeats the name check with
    the real title just before downloading.

    A file found in the folder but missing from the history (e.g. downloaded
    before v3.7) is added to the history, so renaming it later won't cause a
    re-fetch.
    """
    if key and _in_history(key):
        return True
    if video_id and video_id in folder_ids:
        if key:
            _add_to_history(key)
        return True

    title = video.get("title")
    if not title:
        return False
    path = _existing_file_for(ydl, {"title": title}, _format_exts(audio_format))
    if path:
        if key and _same_name_is_ours(path, video_id):
            _add_to_history(key)
        return True
    return False


class _AlreadyHave(DownloadCancelled):
    """Raised by ExistingFileGuardPP to skip a track before it downloads."""
    msg = "Already in the target folder"

    def __init__(self, record: bool):
        super().__init__()
        self.record = record  # add to the history?


class ExistingFileGuardPP(PostProcessor):
    """Last check before downloading, with the real id and title from YouTube.

    _already_have() works from search results; if the real title differs
    (or the search gave none), this catches the existing file anyway, so a
    file in the target folder is never downloaded again or overwritten.
    Runs at pre_process, before the audio or thumbnail is fetched.
    """

    def __init__(self, downloader, exts: set, folder_ids: set):
        super().__init__(downloader)
        self._exts = exts
        self._folder_ids = folder_ids

    def run(self, info):
        self.to_screen("Checking the target folder")  # shown as prep step 6
        video_id = info.get("id")
        if video_id and video_id in self._folder_ids:
            raise _AlreadyHave(record=True)
        path = _existing_file_for(self._downloader, info, self._exts)
        if path:
            raise _AlreadyHave(record=_same_name_is_ours(path, video_id))
        return [], info


def _split_artist_title(title: str) -> tuple[str, str] | None:
    """Split 'Artist - Title' on the first ' - ', or None if it doesn't match."""
    artist, sep, track = title.partition(" - ")
    artist, track = artist.strip(), track.strip()
    return (artist, track) if sep and artist and track else None


def _clean_channel_name(name: str) -> str:
    """'Daft Punk - Topic' -> 'Daft Punk', 'DaftPunkVEVO' -> 'DaftPunk'."""
    cleaned = re.sub(r"\s+-\s+Topic$", "", name)
    cleaned = re.sub(r"\s*VEVO$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or name


class MusicTagsPP(PostProcessor):
    """Fill in artist (and track title) when YouTube gives no artist.

    Runs before download. FFmpegMetadata later writes 'artist' and 'track'
    (falling back to the video title) into the file. Uploads YouTube already
    tagged with an artist are left alone.
    """

    def run(self, info):
        if any(info.get(k) for k in ("artist", "artists", "creator", "creators")):
            return [], info
        split = _split_artist_title(info.get("title") or "")
        if split:
            info["artist"], info["track"] = split
        else:
            channel = info.get("uploader") or info.get("channel")
            if channel:
                info["artist"] = _clean_channel_name(channel)
        return [], info


class SquareCoverPP(FFmpegPostProcessor):
    """Center-crop the downloaded thumbnail to a square before it's embedded."""

    def run(self, info):
        for thumb in info.get("thumbnails") or []:
            path = thumb.get("filepath")
            if not path or not os.path.isfile(path):
                continue
            tmp = path + ".square.jpg"
            # ffmpeg's crop centers by default; side = the shorter dimension.
            self.run_ffmpeg(path, tmp, [
                "-vf", "crop='min(iw,ih)':'min(iw,ih)'", "-q:v", "2", "-update", "1",
            ])
            os.replace(tmp, path)
        return [], info


# Postprocessors that run before the download, while _filename still points at
# the source stream. Their hook calls say nothing about the final audio file.
PRE_DOWNLOAD_PPS = {"ThumbnailsConvertor", "SquareCover", "MusicTags", "ExistingFileGuard"}


_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                           *(f"LPT{i}" for i in range(1, 10))}


def _playlist_folder(name: str, base: str | None = None) -> str:
    """<base>/<playlist name>, with the name made safe as a folder name.

    base defaults to OUTPUT_DIR.
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:100].strip(" .")
    if not safe:
        safe = "Playlist"
    if safe.upper() in _WINDOWS_RESERVED_NAMES:
        safe += "_"
    return os.path.join(base or OUTPUT_DIR, safe)


# ---------------------------------------------------------------------------
# Choosing the download folder
# ---------------------------------------------------------------------------
# Clicking Extract opens a native folder picker on this machine. The browser
# can't do this itself (it never learns real local paths), so the server shows
# the dialog. The client never sends a path: it gets back a one-time folder_id
# that /api/extract trades for the folder the user actually picked, so another
# web page can't point downloads at an arbitrary folder.

def _settings_file() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "YT-Audio-Extractor", "settings.json")


def _load_settings() -> dict:
    try:
        with open(_settings_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(settings: dict) -> None:
    path = _settings_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError as exc:
        sys.stderr.write(f"Could not save settings to {path}: {exc}\n")


def _last_download_dir() -> str:
    """The folder picked last time, if it still exists; otherwise OUTPUT_DIR."""
    folder = _load_settings().get("download_dir")
    return folder if isinstance(folder, str) and os.path.isdir(folder) else OUTPUT_DIR


FOLDER_PICKER_LOCK = threading.Lock()
CHOSEN_FOLDERS: dict = {}  # folder_id -> path, each used once by /api/extract
CHOSEN_FOLDERS_LOCK = threading.Lock()
FOLDER_DIALOG_TITLE = "Choose where to save the audio files"


def _ask_for_folder(initial: str) -> str | None:
    """Show a native folder picker. Returns the chosen path, or None if cancelled."""
    if platform.system() == "Darwin":
        # Tk dialogs must run on the main thread on macOS; AppleScript's
        # picker has no such limit.
        quoted = initial.replace("\\", "\\\\").replace('"', '\\"')
        script = (f'POSIX path of (choose folder with prompt "{FOLDER_DIALOG_TITLE}" '
                  f'default location POSIX file "{quoted}")')
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        path = result.stdout.strip()
        return os.path.normpath(path) if result.returncode == 0 and path else None

    if platform.system() == "Windows":
        # Same Alt-tap trick as _focus_explorer_window: without it Windows
        # opens the dialog behind the browser, because we aren't foreground.
        import ctypes
        user32 = ctypes.windll.user32
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        try:
            return _windows_folder_dialog(initial)
        except OSError as exc:
            # Fall back to Tk below, if this Python has it.
            sys.stderr.write(f"Native folder dialog failed ({exc}); trying Tk.\n")

    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        path = filedialog.askdirectory(parent=root, initialdir=initial,
                                       title=FOLDER_DIALOG_TITLE, mustexist=False)
    finally:
        root.destroy()
    return os.path.normpath(path) if path else None


def _windows_folder_dialog(initial: str) -> str | None:
    """Show the Windows folder picker (IFileOpenDialog) through COM.

    This is the dialog Tk's askdirectory shows on Windows, called directly:
    the private Python the launcher downloads has no tkinter. Raises OSError
    if COM fails, so the caller can fall back.
    """
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    def guid(text):
        return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

    def method(obj, index, *argtypes):
        """Bind entry `index` of a COM object's vtable."""
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        fn = proto(vtable[index])
        return lambda *args: fn(obj, *args)

    def check(hr, what):
        if hr < 0:
            raise OSError(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    FOS_PICKFOLDERS, FOS_FORCEFILESYSTEM = 0x20, 0x40
    SIGDN_FILESYSPATH = 0x80058000
    ERROR_CANCELLED_HR = 0x800704C7 - 0x100000000  # HRESULT_FROM_WIN32(ERROR_CANCELLED)

    # STA for this request thread; S_FALSE (already initialised) is fine.
    initialised = ole32.CoInitializeEx(None, 0x2) >= 0
    dialog = ctypes.c_void_p()
    owner = None
    try:
        check(ole32.CoCreateInstance(
            ctypes.byref(guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")),  # FileOpenDialog
            None, 1, ctypes.byref(guid("D57C7288-D4AD-4768-BE02-9D969532D960")),  # IFileOpenDialog
            ctypes.byref(dialog)), "CoCreateInstance")

        # IFileDialog vtable: 3 Show, 9 SetOptions, 10 GetOptions,
        # 12 SetFolder, 17 SetTitle, 20 GetResult; 2 is Release.
        options = wintypes.DWORD()
        check(method(dialog, 10, ctypes.POINTER(wintypes.DWORD))(ctypes.byref(options)), "GetOptions")
        check(method(dialog, 9, wintypes.DWORD)(options.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM),
              "SetOptions")
        method(dialog, 17, wintypes.LPCWSTR)(FOLDER_DIALOG_TITLE)

        start = ctypes.c_void_p()
        if os.path.isdir(initial) and shell32.SHCreateItemFromParsingName(
                initial, None, ctypes.byref(guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")),  # IShellItem
                ctypes.byref(start)) >= 0:
            method(dialog, 12, ctypes.c_void_p)(start)
            method(start, 2)()

        # An invisible topmost owner keeps the dialog in front of the browser.
        owner = user32.CreateWindowExW(0x00000008 | 0x00000080, "STATIC", None, 0x80000000,  # TOPMOST|TOOLWINDOW, POPUP
                                       0, 0, 0, 0, None, None, None, None)
        hr = method(dialog, 3, wintypes.HWND)(owner)
        if hr == ERROR_CANCELLED_HR:
            return None
        check(hr, "Show")

        item = ctypes.c_void_p()
        check(method(dialog, 20, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(item)), "GetResult")
        try:
            # IShellItem vtable: 5 GetDisplayName.
            name = ctypes.c_void_p()
            check(method(item, 5, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                SIGDN_FILESYSPATH, ctypes.byref(name)), "GetDisplayName")
            path = ctypes.wstring_at(name.value)
            ole32.CoTaskMemFree(name)
        finally:
            method(item, 2)()
        return os.path.normpath(path) if path else None
    finally:
        if owner:
            user32.DestroyWindow(owner)
        if dialog:
            method(dialog, 2)()
        if initialised:
            ole32.CoUninitialize()


@app.post("/api/choose-folder")
def api_choose_folder():
    """Ask the user where to save, via a native folder picker on this machine."""
    if not FOLDER_PICKER_LOCK.acquire(blocking=False):
        return jsonify({"error": "A folder window is already open. Check your taskbar."}), 409
    try:
        path = _ask_for_folder(_last_download_dir())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not open the folder window: {exc}",
                        "fallback": _last_download_dir()}), 500
    finally:
        FOLDER_PICKER_LOCK.release()

    if not path:
        return jsonify({"cancelled": True})
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        return jsonify({"error": f"Can't save to {path}: {exc}"}), 400

    settings = _load_settings()
    settings["download_dir"] = path
    _save_settings(settings)

    folder_id = uuid.uuid4().hex
    with CHOSEN_FOLDERS_LOCK:
        CHOSEN_FOLDERS[folder_id] = path
    return jsonify({"folder_id": folder_id, "path": path})


def _run_extract_job(job_id: str, videos: list, audio_format: str, output_dir: str) -> None:
    """Worker thread: download and convert each requested video into output_dir.

    output_dir is OUTPUT_DIR, or a playlist's subfolder of it. Tracks already
    in output_dir, or in the (shared) download history, are skipped.
    """

    # The current download's temp file, so a stop can clean it up.
    partial = {"path": None}

    def progress_hook(d):
        if d.get("status") == "downloading":
            partial["path"] = d.get("tmpfilename") or partial["path"]
            _check_control(job_id)  # outside JOBS_LOCK: it may block or raise
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                job["current_progress"] = (downloaded / total) if total else 0.0
                job["status"] = "downloading"
                size = (f"{downloaded / 2**20:.1f} of {total / 2**20:.1f} MB" if total
                        else f"{downloaded / 2**20:.1f} MB")
                job["phase"] = {"step": None, "of": PREP_STEPS, "text": f"Downloading the audio: {size}"}
                info = d.get("info_dict") or {}
                if info.get("title"):
                    job["current_title"] = info["title"]
            elif d.get("status") == "finished":
                job["current_progress"] = 1.0
                job["status"] = "converting"
                job["phase"] = {"step": None, "of": PREP_STEPS, "text": "Download complete, processing the audio"}

    def postprocessor_hook(d):
        # Capture the final audio file path. Multiple postprocessors run per video
        # (FFmpegExtractAudio, MoveFiles, ...) so we dedupe by path. Counting completion
        # is done in the main loop, not here.
        if d.get("status") != "finished":
            return
        if d.get("postprocessor") in PRE_DOWNLOAD_PPS:
            return
        info = d.get("info_dict") or {}
        path = info.get("filepath") or info.get("_filename")
        if not path:
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if audio_format == "best":
            if ext not in BEST_AUDIO_EXTS:
                return
        elif ext != audio_format:
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            if path not in job["files"]:
                job["files"].append(path)

    embed_artwork = audio_format not in NO_ARTWORK_FORMATS

    # Order matters: extract the audio, write tags into it, then embed the
    # thumbnail (which also deletes the loose image file afterwards).
    postprocessors = [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": audio_format,
        # Hardcoded to '0' (best quality). MP3 = ~V0 VBR; ignored for FLAC/WAV.
        "preferredquality": "0",
    }, {
        # Title, artist (falls back to the uploader), date, description, URL.
        "key": "FFmpegMetadata",
        "add_metadata": True,
        "add_chapters": False,
    }]
    if embed_artwork:
        # YouTube serves WebP thumbnails; JPEG is what every tag format and
        # DJ software (Mixxx included) reads reliably.
        postprocessors.insert(0, {
            "key": "FFmpegThumbnailsConvertor",
            "format": "jpg",
            "when": "before_dl",
        })
        postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    def build_opts() -> dict:
        # Rebuilt per download so a mid-job switch to "no cookies" takes effect.
        return _with_ffmpeg(_maybe_with_cookies({
            "format": "bestaudio/best",
            "postprocessors": postprocessors,
            # '%' in a folder name would be read as a template field; escape it.
            "outtmpl": os.path.join(output_dir.replace("%", "%%"), "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,  # suppress yt-dlp's stdout progress bar; we use progress_hook
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "writethumbnail": embed_artwork,
            "noplaylist": True,  # one URL per call; never expand to a playlist
            "logger": _PhaseLogger(job_id),  # turns yt-dlp's messages into phases
        }))

    hit_youtube = False
    exts = _format_exts(audio_format)
    # Read once per batch: ids of the tracks (of this format) already in the
    # target folder, from their tags, so renamed files are recognized too.
    _update_job(job_id, status="preparing", current_title=(videos[0].get("title") if videos else "") or "")
    _set_phase(job_id, "Checking your folder for tracks you already have", 1)
    folder_ids = _tagged_ids_in(output_dir, exts)

    def download(url: str, video: dict, key: str | None, video_id: str | None) -> bool:
        """Download one track. Returns False if it was skipped as already had.

        May also raise _AlreadyHave from ExistingFileGuardPP.
        """
        nonlocal hit_youtube
        with yt_dlp.YoutubeDL(build_opts()) as ydl:
            if _already_have(ydl, video, key, audio_format, video_id, folder_ids):
                return False
            # Added here rather than in opts: yt-dlp only builds its own
            # postprocessors from opts. Each lands at the end of its stage.
            ydl.add_post_processor(ExistingFileGuardPP(ydl, exts, folder_ids), when="pre_process")
            ydl.add_post_processor(CleanTitlePP(ydl), when="pre_process")
            ydl.add_post_processor(MusicTagsPP(ydl), when="pre_process")
            if embed_artwork:
                ydl.add_post_processor(SquareCoverPP(ydl), when="before_dl")
            # Between consecutive downloads, pause for a randomized 3-7 seconds.
            # Never before the first one, and never for tracks we skip.
            if hit_youtube:
                _set_phase(job_id, "Waiting a few seconds before the next download, to go easy on YouTube")
                _sleep_with_control(job_id, random.uniform(*INTER_TRACK_SLEEP_RANGE))
            hit_youtube = True
            ydl.download([url])
            return True

    for i, video in enumerate(videos):
        try:
            _check_control(job_id)  # waits here while paused between tracks
        except _StopRequested:
            break

        url = video.get("url") or (
            f"https://www.youtube.com/watch?v={video['id']}" if video.get("id") else None
        )
        if not url:
            with JOBS_LOCK:
                JOBS[job_id]["errors"].append("Missing URL for one of the selected videos.")
            _set_item(job_id, i, "failed")
            continue

        video_id = _video_id_for(url, video)
        key = _history_key(video_id, audio_format) if video_id else None

        _update_job(job_id, current_progress=0.0, current_title=video.get("title") or url, status="preparing")
        _set_phase(job_id, "Checking the download history")
        _set_item(job_id, i, "active")

        with JOBS_LOCK:
            files_before = len(JOBS[job_id]["files"])
            errors_before = len(JOBS[job_id]["errors"])
        partial["path"] = None
        fetched = True  # False once we learn the track is already in the folder

        try:
            try:
                fetched = download(url, video, key, video_id)
            except DownloadError as exc:
                # YouTube sometimes rejects signed-in requests outright. Retry
                # once without cookies (and stay cookie-free for the session).
                if _is_reload_error(str(exc)) and _disable_cookies("download"):
                    fetched = download(url, video, key, video_id)
                else:
                    raise
        except _AlreadyHave as have:
            # Caught by the last check, with the real title: nothing fetched.
            if have.record and key:
                _add_to_history(key)
            fetched = False
        except _StopRequested:
            # Cancelled mid-download: not a failure, just unfinished.
            _remove_partial_files(partial["path"])
            break
        except DownloadError as exc:
            with JOBS_LOCK:
                JOBS[job_id]["errors"].append(
                    f"{video.get('title') or url}: {_humanize_yt_dlp_error(str(exc))}"
                )
        except Exception as exc:  # noqa: BLE001
            with JOBS_LOCK:
                JOBS[job_id]["errors"].append(f"{video.get('title') or url}: {exc}")

        if not fetched:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["skipped"] += 1
                    job["items"][i]["status"] = "have"
            continue

        # ignoreerrors=True swallows failures inside yt-dlp; detect success by whether
        # the postprocessor produced a new file for this video.
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                continue
            if len(job["files"]) > files_before:
                job["completed"] += 1
                job["items"][i]["status"] = "done"
                if key:
                    _add_to_history(key)
                if video_id:
                    folder_ids.add(video_id)
            elif len(job["errors"]) == errors_before:
                job["errors"].append(f"{video.get('title') or url}: extraction failed")
            if job["items"][i]["status"] != "done":
                job["items"][i]["status"] = "failed"

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            if job.get("stop_requested"):
                job["stopped"] = True
                for item in job["items"]:
                    if item["status"] in ("queued", "active"):
                        item["status"] = "stopped"
            job["stop_requested"] = job["pause_requested"] = False
            job["status"] = "done"
            job["finished_at"] = time.time()
            # Final bar reflects how much actually succeeded, not "100%" on failure.
            # Tracks we already had count as success.
            ok = job["completed"] + job["skipped"]
            job["current_progress"] = (ok / job["total"]) if job["total"] else 0.0
            job["current_title"] = ""


@app.post("/api/extract")
def api_extract():
    payload = request.get_json(silent=True) or {}
    videos = payload.get("videos") or []
    audio_format = (payload.get("format") or "best").lower()

    if not isinstance(videos, list) or not videos:
        return jsonify({"error": "No videos provided"}), 400
    if audio_format not in ALLOWED_FORMATS:
        return jsonify({"error": f"Unsupported format: {audio_format}"}), 400

    # The folder the user just picked in the dialog, or (if the dialog couldn't
    # open) the one they used last time.
    folder_id = payload.get("folder_id")
    if folder_id:
        with CHOSEN_FOLDERS_LOCK:
            output_dir = CHOSEN_FOLDERS.pop(str(folder_id), None)
        if output_dir is None:
            return jsonify({"error": "That folder choice has expired. Click Extract again."}), 400
    else:
        output_dir = _last_download_dir()

    # Tracks picked from a playlist go into a subfolder named after it.
    playlist = payload.get("playlist")
    if isinstance(playlist, str) and playlist.strip():
        output_dir = _playlist_folder(playlist.strip(), output_dir)
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        return jsonify({"error": f"Could not create folder {output_dir}: {exc}"}), 500

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "total": len(videos),
            "completed": 0,
            "skipped": 0,
            "pause_requested": False,
            "stop_requested": False,
            "stopped": False,
            "current_progress": 0.0,
            "current_title": "",
            "files": [],
            "errors": [],
            "items": [{"title": v.get("title") or v.get("url") or "(untitled)", "status": "queued"} for v in videos],
            "format": audio_format,
            "output_dir": output_dir,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_extract_job,
        args=(job_id, videos, audio_format, output_dir),
        daemon=True,
        name=f"extract-{job_id[:8]}",
    )
    thread.start()

    return jsonify({"job_id": job_id, "total": len(videos), "output_dir": output_dir})


@app.post("/api/job/<job_id>/control")
def api_job_control(job_id: str):
    """Pause, resume or stop a running batch: {"action": "pause"|"resume"|"stop"}."""
    action = (request.get_json(silent=True) or {}).get("action")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return jsonify({"error": "Job not found"}), 404
        if job["status"] in ("done", "error"):
            return jsonify({"error": "This batch has already finished"}), 409
        if action == "pause":
            job["pause_requested"] = True
        elif action == "resume":
            job["pause_requested"] = False
        elif action == "stop":
            job["stop_requested"] = True
            job["pause_requested"] = False  # a paused worker wakes up and exits
        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400
        return jsonify(_job_snapshot(job))


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

    Optional payload {"job_id": ...} opens that job's folder (a playlist
    subfolder); otherwise, or once the job has expired, the last folder the user
    picked. The client never sends a path, so this can't be used to open
    arbitrary folders. The endpoint is just a privileged shim because the
    browser can't open a local file:// URL from an http:// origin.
    """
    payload = request.get_json(silent=True) or {}
    folder = _last_download_dir()
    with JOBS_LOCK:
        job = JOBS.get(str(payload.get("job_id") or ""))
        if job is not None:
            folder = job.get("output_dir") or folder
    if not os.path.isdir(folder):
        return jsonify({"error": "Output folder does not exist"}), 404
    try:
        _open_in_file_manager(folder)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not open folder: {exc}"}), 500
    return jsonify({"opened": folder})


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

    sys.stderr.write(f"yt-dlp: {yt_dlp.version.__version__}\n")
    sys.stderr.write(
        f"ffmpeg: {'bundled (bin/)' if FFMPEG_LOCATION else 'system PATH'}\n"
    )
    sys.stderr.write(
        f"JS runtime: {'bundled deno (bin/)' if _bundled('deno' + _EXE_SUFFIX) else 'system PATH'}\n"
    )
    if _ejs_available():
        sys.stderr.write("JS challenge solver: yt-dlp-ejs installed\n")
    else:
        sys.stderr.write(
            "WARNING: yt-dlp-ejs is not installed, so YouTube downloads will likely fail.\n"
            '  Fix: python -m pip install -U "yt-dlp[default]"\n'
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
