@echo off
REM ============================================================
REM   YT Audio Extractor - one-time desktop shortcut installer
REM
REM   Creates a labeled, custom-icon shortcut on your Desktop that
REM   launches YT-Audio-Extractor.bat from this folder. Run once
REM   after cloning; you don't need to run it again unless you
REM   move this folder.
REM ============================================================

setlocal
cd /d "%~dp0"

set "TARGET=%~dp0YT-Audio-Extractor.bat"
set "ICON=%~dp0YT-Audio-Extractor.ico"
set "WORKDIR=%~dp0"
set "LINKNAME=YT Audio Extractor.lnk"
set "DESKTOP=%USERPROFILE%\Desktop"
set "LINKPATH=%DESKTOP%\%LINKNAME%"

echo Creating desktop shortcut...
echo   target : %TARGET%
echo   icon   : %ICON%
echo   on     : %LINKPATH%
echo.

if not exist "%TARGET%" (
    echo [X] YT-Audio-Extractor.bat not found next to this installer.
    pause
    exit /b 1
)
if not exist "%ICON%" (
    echo [!] YT-Audio-Extractor.ico not found.
    echo     Shortcut will be created without a custom icon.
    echo     Run:  python generate_icon.py    to create it.
    echo.
)

REM Use the COM API via PowerShell. This is the standard Windows way
REM to create a .lnk; no third-party tooling required.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%LINKPATH%');" ^
    "$s.TargetPath = '%TARGET%';" ^
    "$s.WorkingDirectory = '%WORKDIR%';" ^
    "$s.Description = 'YT Audio Extractor - local audio downloader';" ^
    "if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%' };" ^
    "$s.Save()"

if errorlevel 1 (
    echo [X] Failed to create shortcut.
    pause
    exit /b 1
)

echo [OK] Shortcut created: %LINKPATH%
echo.
echo Double-click "YT Audio Extractor" on your Desktop to launch the app.
echo.
pause
