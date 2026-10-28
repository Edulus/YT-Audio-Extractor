# Changelog

All notable changes to this project will be documented in this file.

## [3.8.0] - 2026-10-28
### Added
- Playlist folders: tracks extracted from a pasted playlist link are saved
  to a subfolder named after the playlist, e.g.
  `~/Downloads/YT-Audio/My Playlist/`. Single videos and search results
  still go to the main folder. Characters Windows doesn't allow in folder
  names are replaced with `_`.
- Link to Soundiiz under the search bar, for converting Spotify or Apple
  Music playlists to YouTube playlists that can be pasted here
### Changed
- Open Folder opens the playlist's subfolder after a playlist batch
- The search placeholder mentions playlist URLs
- The download history stays shared across all folders: a track you
  already have in one folder shows as "Already have" in a playlist batch
  and isn't downloaded again into that playlist's folder
- Version bumped to v3.8 in index.html and installer.iss

## [3.7.0] - 2026-10-19
### Changed
- The download history is now per video and format: having a track as MP3
  no longer blocks getting it as FLAC. Lines in `download-history.txt` now
  read `youtube <video id> <format>`. The app manages this file itself
  instead of using yt-dlp's download archive.
- Embedded cover art is center-cropped to a square, so it looks clean in
  music players and in Mixxx's library and deck views
- When YouTube gives no artist, a title of the form "Artist - Title" is
  split on the first " - " into the artist and title tags. Titles without
  that pattern keep the channel name as artist, with " - Topic" and "VEVO"
  removed. Uploads YouTube already tags with an artist are unchanged.
  The filename still uses the full video title.
- Version bumped to v3.7 in index.html and installer.iss
### Upgrade note
- History lines written by v3.6 (`youtube <video id>`, no format) are
  ignored. Tracks still in the folder under their original name are
  recognized and re-recorded with their format; a v3.6 track you renamed
  could be downloaded again once.

## [3.6.0] - 2026-10-19
### Added
- Tags and cover art: every extracted file gets its title, artist (the
  uploader when YouTube has no artist), date, and source URL written into
  it, plus the video thumbnail embedded as artwork. Works for BEST, OPUS,
  MP3, and FLAC. WAV gets tags but no artwork, since WAV can't hold it.
  Uses the standard tag formats that music players and DJ software such
  as Mixxx read.
- Skip tracks you already have: a track that already exists in the output
  folder shows as "Already have" in the Progress queue instead of "Failed",
  and no time is spent downloading it again
- Download history (`download-history.txt` in the output folder, a yt-dlp
  download archive): a video that was downloaded once is never fetched
  again, even if its file was renamed or moved. Tracks found in the folder
  from before v3.6 are added to the history the first time they're skipped.
### Changed
- The 3-7 s pause between tracks is skipped for tracks that were already
  downloaded, since they make no request to YouTube
- The finished-batch summary counts skipped tracks separately and no longer
  reports "No files were produced" when everything was already there
- Version bumped to v3.6 in index.html and installer.iss

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
