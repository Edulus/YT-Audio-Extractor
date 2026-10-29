<#
  fetch-binaries.ps1  —  Windows-only

  Downloads and verifies the pinned Python, ffmpeg and Deno into ./bin so the
  app needs no manual install and no PATH editing. Python is a private copy
  (python.org's no-install NuGet package) used only by this app. Invoked by
  YT-Audio-Extractor.bat before launch. Safe to re-run: anything already
  present in bin/ is left untouched. Nothing is written outside this folder
  except a temporary download folder, which is deleted afterwards.

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
# Python comes from python.org's official NuGet package, a plain zip with no
# installer; its tools/ folder is a complete Python with pip. Folder = the
# folder inside the archive to copy, DestDir = where it goes under bin/.
$Binaries = @(
    [pscustomobject]@{
        Name    = 'Python'
        Version = '3.14.8'
        Purpose = 'runs the app itself; a private copy, separate from any Python already on this PC'
        SizeMB  = 15
        Url     = 'https://api.nuget.org/v3-flatcontainer/python/3.14.8/python.3.14.8.nupkg'
        Sha256  = 'ce85f674d9a63029f709cbff7a3da1c6bc5bfcfaefdd9999f98fa0290470c454'
        Folder  = 'tools'
        DestDir = 'python'
        Files   = @('python\python.exe')
    },
    [pscustomobject]@{
        Name    = 'ffmpeg'
        Version = '8.1.1'
        Purpose = 'converts audio between formats and writes the tags and cover art'
        SizeMB  = 103
        Url     = 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.1-essentials_build.zip'
        Sha256  = '6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541'
        # Executables to pull out of the archive (found recursively by name).
        Files   = @('ffmpeg.exe', 'ffprobe.exe')
    },
    [pscustomobject]@{
        Name    = 'deno'
        Version = '2.3.3'
        Purpose = "solves YouTube's JavaScript challenges for the yt-dlp engine"
        SizeMB  = 41
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

    Write-Host ""
    Write-Host "[..] $($Spec.Name) $($Spec.Version): $($Spec.Purpose)"
    Write-Host "     Downloading about $($Spec.SizeMB) MB from $(([Uri]$Spec.Url).Host) ..."

    $tmpDir = Join-Path ([IO.Path]::GetTempPath()) ("yta-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    try {
        # Always saved as .zip: Expand-Archive refuses other extensions (.nupkg).
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
        Write-Host "[OK] Checksum matches the pinned SHA-256, so the file is genuine and complete"

        # ---- extract ----
        $unpack = Join-Path $tmpDir 'unpack'
        Expand-Archive -Path $archive -DestinationPath $unpack -Force

        if ($Spec.PSObject.Properties['Folder']) {
            # Copy a whole folder out of the archive (Python's tools/ folder).
            $source = Join-Path $unpack $Spec.Folder
            if (-not (Test-Path (Join-Path $source 'python.exe'))) {
                throw "Expected folder '$($Spec.Folder)' was not found inside the $($Spec.Name) archive."
            }
            $dest = Join-Path $BinDir $Spec.DestDir
            if (Test-Path $dest) { Remove-Item -Path $dest -Recurse -Force }
            Copy-Item -Path $source -Destination $dest -Recurse -Force
        } else {
            foreach ($file in $Spec.Files) {
                $found = Get-ChildItem -Path $unpack -Recurse -Filter $file -File | Select-Object -First 1
                if (-not $found) {
                    throw "Expected '$file' was not found inside the $($Spec.Name) archive."
                }
                Copy-Item -Path $found.FullName -Destination (Join-Path $BinDir $file) -Force
            }
        }
        $where = if ($Spec.PSObject.Properties['DestDir']) { Join-Path $BinDir $Spec.DestDir } else { $BinDir }
        Write-Host "[OK] $($Spec.Name) $($Spec.Version) unpacked into $where"
    }
    finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Show the one-time "please wait" notice only when something actually needs
# downloading. On later launches bin/ is already populated and we stay quiet.
$missing = @($Binaries | Where-Object { -not (Test-Path (Join-Path $BinDir $_.Files[0])) })
if ($missing) {
    $totalMB = ($missing | Measure-Object -Property SizeMB -Sum).Sum
    Write-Host ""
    Write-Host "     One-time setup: downloading $($missing.Count) helper program(s), about $totalMB MB in total."
    Write-Host "     They go into $BinDir and nowhere else: no installer"
    Write-Host "     runs, nothing is added to Windows, and deleting the app folder removes them."
    Write-Host "     This usually takes 1-5 minutes, depending on your connection."
}

foreach ($b in $Binaries) {
    Get-Binary -Spec $b
}
