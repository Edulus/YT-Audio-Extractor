<#
    Takes video words out of the names of audio files you already saved:
    "Song (Official Video).mp3" becomes "Song.mp3". New downloads are named
    this way by the app itself (v4.7 and later).

    It only renames. Audio, tags and cover art are not touched, and a file is
    never overwritten: if the clean name is taken, that file is left alone
    and listed at the end.

    By default it only shows what it would do. Nothing changes until you add
    -Apply. Each real run saves a CSV of old and new names in the folder.

    Preview:  powershell -ExecutionPolicy Bypass -File .\Clean-Audio-Filenames.ps1 -Path "D:\Music"
    Rename:   powershell -ExecutionPolicy Bypass -File .\Clean-Audio-Filenames.ps1 -Path "D:\Music" -Apply

    -Path takes one or more folders, and subfolders are included. Without it,
    the default is Downloads\YT-Audio.

    The rules match clean_title() in app.py: change both together.
#>
param(
    [string[]]$Path = @((Join-Path $env:USERPROFILE 'Downloads\YT-Audio')),
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'

$AudioExts = '.opus', '.m4a', '.webm', '.ogg', '.aac', '.mp3', '.flac', '.wav'

$Official = 'offici(?:al|el|elle)'
$Media = 'vid[e\u00E9]o|videoclip|clip|audio|visuali[sz]er|visual'
$Filler = "$Official|music|lyrics?|full|original|hd|hq|4k|8k"
$Sep = '|\uFF5C\u2022\u00B7/_~:'
$Dash = '\-\u2013\u2014'

function New-Regex([string]$Pattern) {
    [regex]::new($Pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

$BracketRe = New-Regex ('[(\[{\uFF08\uFF3B\u3010\u300C\u300E]' +
    '([^()\[\]{}\uFF08\uFF09\uFF3B\uFF3D\u3010\u3011\u300C\u300D\u300E\u300F]*)' +
    '[)\]}\uFF09\uFF3D\u3011\u300D\u300F]')
$UnderscoredRe = New-Regex '(?<!\S)_([^_]+)_(?!\S)'
$OnlyNoiseRe = New-Regex "^(?:vid[eé]o\s+edit|(?:(?:$Filler)\s+)*(?:$Media|$Official|hd|hq|4k|8k)(?:\s+(?:$Filler|$Media))*)`$"
$OfficialMediaRe = New-Regex "\b$Official\b(?!.*\b(?:remix|mix|version|bootleg)\b).*\b(?:$Media|lyrics?)\b"
$LooseOfficialRe = New-Regex ("(?:^|\s*[$Sep]+\s*|\s+[$Dash]*\s*)$Official(?:\s+(?:music|lyrics?|hd|4k))*" +
    "(?:\s+(?:$Media))+(?:\s+(?:hd|hq|4k|8k|edit))?(?=\s*(?:`$|[$Sep$Dash(\[]))")
$LooseTailRe = New-Regex "(?:\s*[$Sep]+\s*|\s+[$Dash]+\s*)(?:$Official|music video|lyrics? video|vid[eé]o edit|visuali[sz]er)\s*`$"
$LooseEndsRe = New-Regex "^[\s$Sep$Dash]+|[\s$Sep$Dash]+`$"

$DropNoise = [System.Text.RegularExpressions.MatchEvaluator] {
    param($Match)
    $inner = $Match.Groups[1].Value.Trim()
    if ($OnlyNoiseRe.IsMatch($inner) -or $OfficialMediaRe.IsMatch($inner)) { ' ' } else { $Match.Value }
}

function Get-CleanTitle([string]$Title) {
    $cleaned = $BracketRe.Replace($Title, $DropNoise)
    $cleaned = $UnderscoredRe.Replace($cleaned, $DropNoise)
    $cleaned = $LooseOfficialRe.Replace($cleaned, '')
    $cleaned = $LooseTailRe.Replace($cleaned, '')
    if ($cleaned -ceq $Title) { return $Title }
    $cleaned = [regex]::Replace($LooseEndsRe.Replace($cleaned, ''), '\s{2,}', ' ')
    if ($cleaned) { $cleaned } else { $Title }
}

$renamed = 0
$skipped = [System.Collections.Generic.List[string]]::new()
$failed = [System.Collections.Generic.List[string]]::new()
$scanned = 0

Write-Host ''
if ($Apply) { Write-Host 'Renaming files.' } else { Write-Host 'Preview only: nothing is changed. Add -Apply to rename.' }

foreach ($folder in $Path) {
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) {
        Write-Host "[X] Folder not found: $folder"
        continue
    }
    Write-Host ''
    Write-Host "Folder: $folder"

    $log = [System.Collections.Generic.List[object]]::new()
    # Names taken so far, so a preview also spots two files that would clash.
    $taken = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $files = @(Get-ChildItem -LiteralPath $folder -File -Recurse | Where-Object { $AudioExts -contains $_.Extension.ToLowerInvariant() })

    foreach ($file in $files) {
        $scanned++
        $stem = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
        $clean = Get-CleanTitle $stem
        if ($clean -ceq $stem) { continue }

        $newName = $clean.TrimEnd('.') + $file.Extension   # no "Song..opus"
        $target = Join-Path $file.DirectoryName $newName
        if ((Test-Path -LiteralPath $target) -or -not $taken.Add($target)) {
            $skipped.Add("$($file.FullName)  (""$newName"" already exists)")
            continue
        }

        Write-Host "  $($file.Name)"
        Write-Host "    -> $newName"
        if (-not $Apply) { $renamed++; continue }
        try {
            Rename-Item -LiteralPath $file.FullName -NewName $newName
            $renamed++
            $log.Add([pscustomobject]@{ OldPath = $file.FullName; NewPath = $target })
        } catch {
            $failed.Add("$($file.FullName)  ($($_.Exception.Message))")
        }
    }

    if ($log.Count -gt 0) {
        $logFile = Join-Path $folder ("filename-cleanup-{0:yyyyMMdd-HHmmss}.csv" -f (Get-Date))
        $log | Export-Csv -LiteralPath $logFile -NoTypeInformation -Encoding UTF8
        Write-Host "  Old and new names saved in: $logFile"
    }
}

Write-Host ''
$verb = if ($Apply) { 'Renamed' } else { 'Would rename' }
Write-Host "$verb $renamed of $scanned audio files."
if ($skipped.Count -gt 0) {
    Write-Host ''
    Write-Host "Left alone, because the clean name is already taken ($($skipped.Count)):"
    $skipped | ForEach-Object { Write-Host "  $_" }
}
if ($failed.Count -gt 0) {
    Write-Host ''
    Write-Host "Could not rename ($($failed.Count)), perhaps open in another program:"
    $failed | ForEach-Object { Write-Host "  $_" }
}
Write-Host ''
