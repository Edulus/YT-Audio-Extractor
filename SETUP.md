# Setup & build notes

How the one-click Windows setup works, and how to maintain it.

## The flow at a glance

A fresh Windows user installs **nothing** first. They run the small installer
(or unzip the project) and open the app; `YT-Audio-Extractor.bat` does the
rest, saying what it does at each step:

1. Kills any stale server on port 5000 (auto-recovery from a previous run).
2. Downloads pinned, checksum-verified **Python**, **ffmpeg** and **Deno**
   into `bin/` (first run only) via `fetch-binaries.ps1`. Python is a private
   copy in `bin/python/`, used only by this app.
3. Installs the Python packages from `requirements.txt` into that private
   Python (first run only), with `--no-cache-dir` so pip leaves nothing in the
   user's profile.
4. Checks for a newer yt-dlp (every launch).
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
- **Python is a private copy, not a system install.** It is python.org's
  official NuGet package (`python.<version>.nupkg`), which is a plain zip whose
  `tools/` folder is a complete Python with pip. It needs no installer, no admin
  rights and no PATH change, can't conflict with a Python the user already
  has, and is removed with the app folder. It has no tkinter, so on Windows
  `app.py` shows the folder picker through COM (`IFileOpenDialog`) instead.
- **No binaries in the installer.** The installer is about 1 MB; everything
  large is downloaded on first launch, so updating a pinned version never
  needs a new installer.
- **ffmpeg is the `.zip` gyan.dev build**, not `.7z`. Windows can extract `.zip`
  natively via PowerShell `Expand-Archive`; `.7z` would force users to install
  7-Zip, defeating the zero-setup goal.
- **Deno** ships as a single `.exe` inside a `.zip` — small and self-contained.

## Pinned versions

Versions, download URLs, and SHA-256 checksums are defined in **one place**:
the `$Binaries` array at the top of [`fetch-binaries.ps1`](fetch-binaries.ps1).

| Binary | Version | Source |
|--------|---------|--------|
| Python | 3.14.8  | nuget.org `python.3.14.8.nupkg` (published by python.org) |
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

## The installer

[`installer.iss`](installer.iss) builds `YT-Audio-Extractor-Setup-<version>.exe`
with Inno Setup. It installs per user (into `%LOCALAPPDATA%\Programs`, no
admin rights, which also keeps `bin/` writable), shows
[`installer-info.txt`](installer-info.txt) before installing so the user knows
what will happen on their PC, and creates Start Menu and desktop shortcuts with
`YT-Audio-Extractor.ico`. The uninstaller stops the app's own Python if it is
running and deletes `bin/`, so everything downloaded later is removed too.

You don't build it by hand: [`.github/workflows/installer.yml`](.github/workflows/installer.yml)
builds it on GitHub whenever a release is published and attaches it to that
release. To rebuild for an existing release, run the workflow from the
**Actions** tab with the release tag. Locally, `iscc installer.iss` writes it to
`dist/`. Keep `AppVersion` in `installer.iss` in step with the app version.
