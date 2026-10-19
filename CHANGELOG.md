# Changelog

All notable changes to this project will be documented in this file.

## [3.5.0] - 2026-10-19
### Added
- Audio format options BEST and OPUS. BEST is the new default: it keeps
  YouTube's original audio stream (usually Opus, sometimes AAC) with no
  re-encoding, the highest quality available.
- Expandable queue in the Progress panel: click it to see every track in
  the batch with its status (working, queued, done, failed)
- Launcher updates yt-dlp on every launch and prints the installed version
- Startup log shows the yt-dlp version and warns if yt-dlp-ejs is missing
### Changed
- requirements.txt now installs `yt-dlp[default]`, which includes the
  yt-dlp-ejs JavaScript challenge solver YouTube requires
- Searching while a batch is running no longer hides or disconnects the
  Progress panel; the Extract button is disabled until the batch finishes
- The progress bar ends at the share of tracks that succeeded instead of
  showing 100% on a failed batch
- Error messages no longer show raw color codes or the yt-dlp prefix
- Version bumped to v3.5 in index.html and installer.iss
### Fixed
- "The page needs to be reloaded" errors: the app retries without browser
  cookies when YouTube rejects a signed-in request, and stays cookie-free
  for the rest of the session
- A failed URL search now shows the real error instead of "No results"
## [3.0.0] - 2026-06-01

### Changed
- Cookie passthrough is now browser-agnostic: a new `detect_browser()`
  probes Chrome, Firefox, Edge, Brave, Opera, Chromium, and Vivaldi (in
  that order) at startup and uses the first whose cookies yt-dlp can read,
  stored in a module-level `DETECTED_BROWSER`. Replaces the Chrome-only
  `CHROME_COOKIES_AVAILABLE` flag.
- Startup log now reports `Cookie source: <browser>` or
  `No browser cookies available — running without authentication`
- README: removed Chrome-required language; documents auto-detection
  across all supported browsers and the running-browser cookie-lock caveat
- Version bumped to v3.0 in index.html

## [2.9.0] - 2026-05-31

### Added
- Automatic binary provisioning: ffmpeg and Deno downloaded and
  SHA-256-verified on first launch via fetch-binaries.ps1
- Each bundled binary's download line states its purpose (what ffmpeg
  and Deno are for), shown in both the console and the progress bar
- bin/ directory for bundled binaries (gitignored)
- SETUP.md with maintainer docs for version pinning and checksums
- CHANGELOG.md
- Optional Inno Setup installer script (installer.iss)
- Platform: Windows badge and an upfront note that the one-click
  launcher is Windows-only, pointing macOS/Linux users to the manual path

### Changed
- app.py: binary references now use bin/ paths with PATH fallback
- YT-Audio-Extractor.bat: replaces manual dependency checks with
  PowerShell fetch script invocation
- README.md: setup reduced from 4 manual steps to 2; hero screenshot
  moved below the intro; refreshed launcher screenshot; emoji fixes
- Version bumped to v2.9 in index.html

### Removed
- Manual ffmpeg PATH setup requirement
- Manual Deno/Node.js install requirement

## [2.8.0] - 2026-05-12

Initial public release.
