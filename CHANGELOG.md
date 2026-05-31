# Changelog

All notable changes to this project will be documented in this file.

## [2.9.0] - 2026-05-30

### Added
- Automatic binary provisioning: ffmpeg and Deno downloaded and
  SHA-256-verified on first launch via fetch-binaries.ps1
- bin/ directory for bundled binaries (gitignored)
- SETUP.md with maintainer docs for version pinning and checksums
- CHANGELOG.md
- Optional Inno Setup installer script (installer.iss)

### Changed
- app.py: binary references now use bin/ paths with PATH fallback
- YT-Audio-Extractor.bat: replaces manual dependency checks with
  PowerShell fetch script invocation
- README.md: setup reduced from 4 manual steps to 2
- Version bumped to v2.9 in index.html

### Removed
- Manual ffmpeg PATH setup requirement
- Manual Deno/Node.js install requirement

## [2.8.0] - 2026-05-12

Initial public release.
