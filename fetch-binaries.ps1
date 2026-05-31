<#
  fetch-binaries.ps1  —  Windows-only

  Downloads and verifies the pinned ffmpeg + Deno binaries into ./bin so the
  app needs no manual install and no PATH editing. Invoked by
  YT-Audio-Extractor.bat before launch. Safe to re-run: anything already
  present in bin/ is left untouched.

  ───────────────────────────────────────────────────────────────────────────
  VERSION PINNING — read before bumping a version
  ───────────────────────────────────────────────────────────────────────────
  Each binary pins a Version, a download Url, and a Sha256. Bumping a version
  is DELIBERATE: change all three together. A mismatched checksum aborts the
  launch loudly rather than running a possibly-corrupt or tampered binary.

  To update a checksum: download the pinned Url once, run:

      certutil -hashfile <downloaded-file> SHA256

  and paste the 64-character hex value (lowercased) into the matching Sha256
  field below.
#>

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ─────────────────────────── Pinned binaries ────────────────────────────────
# NOTE: ffmpeg uses the gyan.dev .zip build (not .7z) so Windows can extract it
# natively via Expand-Archive — no 7-Zip required.
$Binaries = @(
    [pscustomobject]@{
        Name    = 'ffmpeg'
        Version = '8.1.1'
        Purpose = 'converts the downloaded audio into MP3'
        Url     = 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.1-essentials_build.zip'
        Sha256  = '6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541'
        # Executables to pull out of the archive (found recursively by name).
        Files   = @('ffmpeg.exe', 'ffprobe.exe')
    },
    [pscustomobject]@{
        Name    = 'deno'
        Version = '2.3.3'
        Purpose = 'runs the yt-dlp engine that fetches audio from YouTube'
        Url     = 'https://github.com/denoland/deno/releases/download/v2.3.3/deno-x86_64-pc-windows-msvc.zip'
        Sha256  = 'a40f0e4c6535834b83faaf8b09e47867ff4cbb7b12d0554bbc58e7355e9cf460'
        Files   = @('deno.exe')
    }
)
# ─────────────────────────────────────────────────────────────────────────────

$BinDir = Join-Path $PSScriptRoot 'bin'
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

function Save-FileWithProgress {
    # Downloads $Url to $Dest showing a Write-Progress bar (percent + MB).
    #
    # Uses System.Net.WebClient, which talks HTTP directly with no Internet
    # Explorer dependency, so it preserves the -UseBasicParsing behavior the
    # old Invoke-WebRequest call relied on. DownloadFileAsync drives the
    # DownloadProgressChanged event for the bar; the DownloadFileCompleted
    # event carries any network error so we can fail loudly.
    param([string]$Url, [string]$Dest, [string]$Label)

    $wc = New-Object System.Net.WebClient
    $wc.Headers.Add('User-Agent', 'YT-Audio-Extractor')

    $progId = 'YtaDownloadProgress'
    $doneId = 'YtaDownloadDone'

    $null = Register-ObjectEvent -InputObject $wc -EventName DownloadProgressChanged `
        -SourceIdentifier $progId -MessageData $Label -Action {
            $e = $Event.SourceEventArgs
            $recvMB  = [math]::Round($e.BytesReceived / 1MB, 1)
            $totalMB = [math]::Round($e.TotalBytesToReceive / 1MB, 1)
            Write-Progress -Id 1 -Activity "Downloading $($Event.MessageData)" `
                -Status "$recvMB MB / $totalMB MB ($($e.ProgressPercentage)%)" `
                -PercentComplete $e.ProgressPercentage
        }
    $null = Register-ObjectEvent -InputObject $wc -EventName DownloadFileCompleted `
        -SourceIdentifier $doneId

    try {
        $wc.DownloadFileAsync([Uri]$Url, $Dest)
        # Block until the download finishes; the progress event keeps firing.
        $evt = Wait-Event -SourceIdentifier $doneId
        Remove-Event -SourceIdentifier $doneId
        $completed = $evt.SourceEventArgs
        if ($completed.Error)     { throw $completed.Error }
        if ($completed.Cancelled) { throw "Download was cancelled." }
    }
    finally {
        Unregister-Event -SourceIdentifier $progId -ErrorAction SilentlyContinue
        Unregister-Event -SourceIdentifier $doneId -ErrorAction SilentlyContinue
        $wc.Dispose()
        Write-Progress -Id 1 -Activity "Downloading $Label" -Completed
    }
}

function Get-Binary {
    param([pscustomobject]$Spec)

    # The first file is the existence sentinel. If it's already in bin/, skip.
    $sentinel = Join-Path $BinDir $Spec.Files[0]
    if (Test-Path $sentinel) {
        Write-Host "[OK] $($Spec.Name) $($Spec.Version) already present"
        return
    }

    Write-Host "[..] Downloading $($Spec.Name) $($Spec.Version)  ($($Spec.Purpose)) ..."
    $tmpDir = Join-Path ([IO.Path]::GetTempPath()) ("yta-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    try {
        $archive = Join-Path $tmpDir ($Spec.Name + '.zip')
        Save-FileWithProgress -Url $Spec.Url -Dest $archive -Label "$($Spec.Name) $($Spec.Version) ($($Spec.Purpose))"

        # ---- checksum ----
        $actual = (Get-FileHash -Algorithm SHA256 -Path $archive).Hash
        if ($actual -ne $Spec.Sha256.ToUpper()) {
            Write-Host ""
            Write-Host "[X] $($Spec.Name) SHA-256 CHECKSUM MISMATCH" -ForegroundColor Red
            Write-Host "    expected: $($Spec.Sha256.ToUpper())"
            Write-Host "    actual:   $actual"
            Write-Host "    Refusing to use a possibly-corrupt or tampered binary." -ForegroundColor Red
            Write-Host "    Delete bin/ and re-run, or verify the pinned hash in fetch-binaries.ps1." -ForegroundColor Yellow
            throw "Checksum verification failed for $($Spec.Name)."
        }
        Write-Host "[OK] $($Spec.Name) checksum verified"

        # ---- extract ----
        $unpack = Join-Path $tmpDir 'unpack'
        Expand-Archive -Path $archive -DestinationPath $unpack -Force

        foreach ($file in $Spec.Files) {
            $found = Get-ChildItem -Path $unpack -Recurse -Filter $file -File | Select-Object -First 1
            if (-not $found) {
                throw "Expected '$file' was not found inside the $($Spec.Name) archive."
            }
            Copy-Item -Path $found.FullName -Destination (Join-Path $BinDir $file) -Force
        }
        Write-Host "[OK] $($Spec.Name) $($Spec.Version) installed into bin/"
    }
    finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Show the one-time "please wait" notice only when something actually needs
# downloading. On later launches bin/ is already populated and we stay quiet.
$missing = $Binaries | Where-Object { -not (Test-Path (Join-Path $BinDir $_.Files[0])) }
if ($missing) {
    Write-Host ""
    Write-Host "First run of YT Audio Extractor, please wait."
    Write-Host "Downloading ffmpeg (~103 MB) and Deno (~41 MB)."
    Write-Host "This can take a few minutes (approximately 2-5 minutes)."
    Write-Host ""
}

foreach ($b in $Binaries) {
    Get-Binary -Spec $b
}
