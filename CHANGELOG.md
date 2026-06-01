# Changelog

All notable changes to this project will be documented in this file.

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
