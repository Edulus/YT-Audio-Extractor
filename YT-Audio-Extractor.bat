@echo off
REM ============================================================
REM   YT Audio Extractor - launcher
REM   Sets up everything the app needs inside this folder (a private
REM   Python, ffmpeg, Deno and the Python packages), keeps yt-dlp up
REM   to date, and starts the app. Each step says what it is doing.
REM ============================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "APPDIR=%~dp0"
set "PY=%~dp0bin\python\python.exe"

echo ============================================================
echo   YT Audio Extractor
echo ============================================================
echo.
echo   The programs this app needs are kept inside this folder:
echo     %APPDIR%
echo   Nothing is installed anywhere else on your PC. Besides your
echo   audio files, the app writes only two small files of its own:
echo     %APPDATA%\YT-Audio-Extractor\settings.json   ^(last folder used^)
echo     %USERPROFILE%\Downloads\YT-Audio\download-history.txt
echo.

REM ---------- Step 1: clean up a previous session ----------
REM We just kill blindly; a no-op if nothing's there. Cheaper and simpler
REM than "check first, then maybe kill", and avoids the user having to
REM remember to stop the previous session before relaunching.
echo [1/4] Checking for an earlier copy of the app that is still running...
set "FOUND=0"
set "KILLED=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:5000" ^| findstr "LISTENING" 2^>nul') do (
    set /a FOUND+=1
    taskkill /F /PID %%a >nul 2>&1
    if not errorlevel 1 (
        echo [OK] Stopped the earlier copy ^(process %%a^), so this one can start
        set /a KILLED+=1
    ) else (
        echo [X] Process %%a is using port 5000 and could not be stopped.
        echo     Open Task Manager ^(Ctrl+Shift+Esc^), find process %%a,
        echo     right-click it, End task. Then run YT Audio Extractor again.
    )
)
if "!FOUND!" NEQ "!KILLED!" goto :fatal
if "!KILLED!" NEQ "0" (
    REM Brief pause so the kernel finishes releasing the socket before we rebind.
    timeout /t 2 /nobreak >nul
) else (
    echo [OK] None running
)
echo.

REM ---------- Step 2: helper programs ----------
REM A private Python, ffmpeg and Deno are downloaded into bin\ on the first
REM run: pinned versions, each checked against its SHA-256 before use.
REM Versions, URLs and hashes live at the top of fetch-binaries.ps1.
echo [2/4] Helper programs ^(private Python, ffmpeg, Deno^)
if exist "%PY%" if exist "%~dp0bin\ffmpeg.exe" if exist "%~dp0bin\deno.exe" goto :bins_ready

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch-binaries.ps1"
if errorlevel 1 (
    echo.
    echo [X] A helper program could not be downloaded or failed its check.
    echo     Check your internet connection and run YT Audio Extractor again.
    echo     Anything already downloaded is kept; only the missing part is retried.
    goto :fatal
)

:bins_ready
for %%f in ("%PY%" "%~dp0bin\ffmpeg.exe" "%~dp0bin\deno.exe") do (
    if not exist "%%~f" (
        echo [X] %%~f is missing after setup.
        goto :fatal
    )
)
for /f "tokens=*" %%v in ('call "%PY%" --version 2^>^&1') do echo [OK] %%v ^(private copy, in bin\python^)
echo [OK] ffmpeg and Deno ^(in bin^)
echo.

REM ---------- Step 3: Python packages ----------
REM yt_dlp_ejs is the JS challenge solver YouTube now requires. It comes with
REM yt-dlp[default]; plain "pip install yt-dlp" leaves it out. Packages go into
REM the private Python only; --no-cache-dir keeps pip from leaving a download
REM cache in the user's profile.
echo [3/4] Python packages ^(Flask for the app's page, yt-dlp for YouTube^)
"%PY%" -c "import flask, yt_dlp, yt_dlp_ejs" >nul 2>&1
if errorlevel 1 (
    echo [..] Installing them into the private Python, about 15 MB from pypi.org.
    echo      This happens once and takes about a minute.
    "%PY%" -m pip install --no-cache-dir --no-warn-script-location --disable-pip-version-check -q -U -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [X] Package install failed. Check your internet connection and
        echo     run YT Audio Extractor again.
        goto :fatal
    )
    echo [OK] Packages installed
) else (
    echo [OK] Already installed
)
echo.

REM ---------- Step 4: keep yt-dlp current ----------
REM YouTube changes often and old yt-dlp versions break. Upgrading on every
REM launch takes a few seconds when already up to date. Failure here is
REM not fatal ^(e.g. offline^): we keep the installed version.
echo [4/4] Checking for a newer yt-dlp ^(YouTube changes often, so this runs every launch^)...
"%PY%" -m pip install --no-cache-dir --no-warn-script-location --disable-pip-version-check -q -U "yt-dlp[default]" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Could not check for updates ^(offline?^). Using the installed version.
)
REM "call" first: a command that starts with a quote and holds ( ) gets its
REM quotes stripped by cmd, and then never runs.
for /f "tokens=*" %%v in ('call "%PY%" -c "import yt_dlp; print(yt_dlp.version.__version__)" 2^>nul') do echo [OK] yt-dlp %%v

REM ---------- Launch ----------
echo.
echo ============================================================
echo   Starting. Your browser opens at http://localhost:5000
echo.
echo   Keep this window open while you use the app; closing it
echo   stops the app. Audio files are saved in the folder you
echo   choose each time you click Extract Audio.
echo ============================================================
echo.
"%PY%" app.py
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
echo   Cannot start. Fix the issue^(s^) above and run YT Audio Extractor again.
echo ============================================================
pause
