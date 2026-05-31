# Setup & build notes

How the (almost) one-click Windows setup works, and how to maintain it.

## The flow at a glance

A fresh Windows user installs **one** thing — Python 3.10+ — then double-clicks
`YT-Audio-Extractor.bat`. The launcher does everything else:

1. Kills any stale server on port 5000 (auto-recovery from a previous run).
2. Verifies Python ≥ 3.10 and pip. **Python is the only manual prerequisite.**
3. Downloads pinned, checksum-verified **ffmpeg** and **Deno** into `bin/`
   (first run only) via `fetch-binaries.ps1`.
4. Installs the Python packages from `requirements.txt` (first run only).
5. Launches Flask and opens `http://localhost:5000`.

No PATH editing is ever required. The app references the bundled binaries by
absolute path:

- **ffmpeg** — `app.py` passes `ffmpeg_location` pointing at `bin/ffmpeg.exe`.
- **Deno** — `app.py` prepends `bin/` to `PATH` for its own process, because
  yt-dlp discovers its JavaScript runtime by searching `PATH`. (yt-dlp still
  **requires** a JS runtime to solve YouTube's bot-detection challenges — it is
  bundled, not removed.)

If `bin/` is absent (e.g. macOS/Linux), `app.py` falls back to whatever ffmpeg
and JS runtime are on the system `PATH`, so the Unix flow is unchanged.

## Why these packaging choices

- **Python is NOT frozen** (no PyInstaller). yt-dlp updates often and frequently
  breaks when frozen; keeping it as a normal pip dependency means a simple
  `pip install -U yt-dlp` always works.
- **ffmpeg is the `.zip` gyan.dev build**, not `.7z`. Windows can extract `.zip`
  natively via PowerShell `Expand-Archive`; `.7z` would force users to install
  7-Zip, defeating the zero-setup goal.
- **Deno** ships as a single `.exe` inside a `.zip` — small and self-contained.

## Pinned versions

Versions, download URLs, and SHA-256 checksums are defined in **one place**:
the `$Binaries` array at the top of [`fetch-binaries.ps1`](fetch-binaries.ps1).

| Binary | Version | Source |
|--------|---------|--------|
| ffmpeg | 8.1.1   | gyan.dev `ffmpeg-8.1.1-essentials_build.zip` |
| Deno   | 2.3.3   | GitHub `deno-x86_64-pc-windows-msvc.zip`     |

Bumping a version is deliberate: update the `Version`, `Url`, **and** `Sha256`
together.

## Checksum verification

Every download is SHA-256 verified against the pinned `Sha256` field before it
is used. A mismatch aborts the launch loudly rather than running a
possibly-corrupt or tampered binary.

To update a checksum when bumping a version:

1. Download the pinned URL once on a networked machine.
2. Compute its hash:
   ```
   certutil -hashfile ffmpeg-8.1.1-essentials_build.zip SHA256
   ```
3. Paste the 64-character hex value into the matching `Sha256` field in
   `fetch-binaries.ps1`.

## Testing the fetch step in isolation

```powershell
# Force a clean re-download:
Remove-Item -Recurse -Force .\bin -ErrorAction SilentlyContinue
powershell -NoProfile -ExecutionPolicy Bypass -File .\fetch-binaries.ps1
```

It is safe to re-run; anything already in `bin/` is left untouched.

## Optional: Inno Setup installer

[`installer.iss`](installer.iss) builds a Windows installer that bundles the app
(and `bin/` if you've already populated it), creates Start Menu + desktop
shortcuts using `YT-Audio-Extractor.ico`, and links to python.org if Python is
absent. It does **not** install Python. Build it with the Inno Setup Compiler
(`iscc installer.iss`). The `.bat` path works standalone — the installer is just
polish.
