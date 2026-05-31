@echo off
REM ============================================================
REM   YT Audio Extractor - launcher
REM   Verifies the environment, installs missing Python packages,
REM   and starts the Flask app.
REM ============================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   YT Audio Extractor
echo ============================================================
echo.

REM ---------- Clean up anything left over from previous runs ----------
REM We just kill blindly — a no-op if nothing's there. Cheaper and simpler
REM than "check first, then maybe kill", and avoids the user having to
REM remember to stop the previous session before relaunching.
set "FOUND=0"
set "KILLED=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:5000" ^| findstr "LISTENING" 2^>nul') do (
    set /a FOUND+=1
    taskkill /F /PID %%a >nul 2>&1
    if not errorlevel 1 (
        echo [cleanup] Stopped previous server on port 5000 ^(PID %%a^)
        set /a KILLED+=1
    ) else (
        echo [X] Process %%a is holding port 5000 and could not be killed.
        echo     Open Task Manager ^(Ctrl+Shift+Esc^), find python.exe with PID %%a,
        echo     right-click, End task. Then re-run start.bat.
    )
)
if "!FOUND!" NEQ "!KILLED!" goto :fatal
if "!KILLED!" NEQ "0" (
    REM Brief pause so the kernel finishes releasing the socket before we rebind.
    timeout /t 2 /nobreak >nul
    echo [OK] cleaned up !KILLED! leftover server^(s^)
    echo.
)

set "ERRORS=0"

REM ---------- Python (the ONLY thing you must install yourself) ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python is not installed or not on PATH.
    echo.
    echo     Python is the ONLY prerequisite you have to install manually -
    echo     ffmpeg and the JS runtime are downloaded automatically below.
    echo.
    echo     Install Python 3.10 or newer:
    echo       https://www.python.org/downloads/
    echo     IMPORTANT: check "Add Python to PATH" during install.
    echo.
    goto :fatal
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [X] Python 3.10 or newer is required.
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo     Found: %%v
    echo     Upgrade: https://www.python.org/downloads/
    goto :fatal
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v

REM ---------- pip ----------
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [X] pip is not available.
    echo     Run: python -m ensurepip --upgrade
    goto :fatal
)
echo [OK] pip available

REM ---------- Bundled binaries (ffmpeg + Deno) ----------
REM No manual ffmpeg/Deno install and no PATH editing required: the helper
REM script downloads pinned, checksum-verified binaries into bin\ on first run.
REM Versions, URLs, and SHA-256 hashes live at the top of fetch-binaries.ps1.
if exist "%~dp0bin\ffmpeg.exe" if exist "%~dp0bin\deno.exe" goto :bins_ready

echo [..] Setting up bundled binaries ^(first run only^)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch-binaries.ps1"
if errorlevel 1 (
    echo.
    echo [X] Failed to download/verify the bundled binaries.
    echo     Check your internet connection and re-run YT-Audio-Extractor.bat.
    goto :fatal
)

:bins_ready
if not exist "%~dp0bin\ffmpeg.exe" (
    echo [X] bin\ffmpeg.exe is missing after setup.
    goto :fatal
)
if not exist "%~dp0bin\deno.exe" (
    echo [X] bin\deno.exe is missing after setup.
    goto :fatal
)
echo [OK] ffmpeg ^(bundled^)
echo [OK] Deno JS runtime ^(bundled^)

if "!ERRORS!" NEQ "0" goto :fatal

REM ---------- Python packages ----------
python -c "import flask, yt_dlp" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [!] Required Python packages are missing ^(flask, yt-dlp^).
    echo     About to run: python -m pip install -r requirements.txt
    echo.
    set "YN="
    set /p YN="Install now? [Y/n]: "
    if /i "!YN!"=="n" (
        echo Aborted by user.
        goto :end
    )
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [X] Package install failed.
        echo     Try manually:  python -m pip install -r requirements.txt
        goto :fatal
    )
    echo [OK] Packages installed
) else (
    echo [OK] Python packages installed
)

REM ---------- Launch ----------
echo.
echo ============================================================
echo   Starting...   browser opens at http://localhost:5000
echo   Keep this window open while using the app.
echo   Press Ctrl+C or close the window to quit.
echo ============================================================
echo.
python app.py
set "EXITCODE=%ERRORLEVEL%"
if "!EXITCODE!" NEQ "0" (
    echo.
    echo App exited with code !EXITCODE!.
    pause
)
goto :eof

:fatal
echo.
echo ============================================================
echo   Cannot start. Fix the issue^(s^) above and re-run start.bat.
echo ============================================================
:end
pause
