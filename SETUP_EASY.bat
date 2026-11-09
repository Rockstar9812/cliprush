@echo off
setlocal EnableExtensions
title ClipRush - one-click setup
echo.
echo  ===============================================
echo   ClipRush one-click setup. Nothing to type.
echo  ===============================================
echo.

rem ---- 1. Copy ClipRush to C:\ClipRush (a fixed home that survives clearing Downloads)
set "SRC=%~dp0"
set "DST=C:\ClipRush"
if /i not "%SRC%"=="%DST%\" (
  echo Copying ClipRush to %DST% ...
  robocopy "%SRC%." "%DST%" /E /NFL /NDL /NJH /NJS /NP /XD .git __pycache__ >nul
  for %%F in ("%SRC%..\*.txt" "%SRC%*.txt") do copy /y "%%~F" "%DST%\" >nul 2>nul
)
cd /d "%DST%"

rem ---- 2. Python 3.12 (ClipRush's packages have ready-made installs for 3.11-3.13; newer ones like 3.15 do not)
set "PY="
for %%V in (3.12 3.13 3.11) do if not defined PY call :findpy %%V
if not defined PY (
  echo Installing Python 3.12 - your other Python versions are left untouched ...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  call :findpy 3.12
)
if not defined PY (
  py install 3.12 >nul 2>nul
  call :findpy 3.12
)
if not defined PY (
  echo.
  echo Could not install Python 3.12 automatically.
  echo Install it from https://www.python.org/downloads/release/python-31210/ then run this file again.
  pause
  exit /b 1
)
echo Using Python: %PY%

rem ---- 3. FFmpeg (for cutting clips)
where ffmpeg >nul 2>nul || (
  echo Installing FFmpeg ...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
)

rem ---- 4. Packages, one at a time so one problem can't block the rest
echo Installing packages (1-2 minutes) ...
"%PY%" -m pip install -q --upgrade pip >nul 2>nul
for %%R in (requests PyYAML cryptography youtube-transcript-api yt-dlp qrcode) do (
  echo    - %%R
  "%PY%" -m pip install -q -U %%R
)
"%PY%" -c "import requests, yaml, cryptography, youtube_transcript_api, yt_dlp" 2>nul || (
  echo.
  echo Some packages did not install. Take a photo of this window and send it to Claude.
  "%PY%" -c "import requests, yaml, cryptography, youtube_transcript_api, yt_dlp"
  pause
  exit /b 1
)
echo Packages OK.

rem ---- 5. Read your keys from your notes, check them, unlock the app
"%PY%" laptop\easy_setup.py

rem ---- 6. Background runs every 30 minutes while you're signed in (no admin rights needed)
> "laptop\run_laptop.bat" echo @echo off
>> "laptop\run_laptop.bat" echo cd /d "%DST%"
>> "laptop\run_laptop.bat" echo "%PY%" -m brain laptop ^>^> "laptop\last_run.log" 2^>^&1
schtasks /delete /f /tn "ClipRush" >nul 2>nul
schtasks /delete /f /tn "ClipRush at sign-in" >nul 2>nul
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\ClipRush.vbs" echo CreateObject("WScript.Shell").Run "wscript.exe ""%DST%\laptop\loop.vbs""", 0, False
> "%USERPROFILE%\Desktop\ClipRush - Find clips now.bat" echo @call "%DST%\laptop\Find clips now.bat"

rem ---- 7. First run right now (visible), then the silent 30-minute loop takes over
echo.
echo Finding clips now (first run takes 3-10 minutes) ...
"%PY%" -m brain laptop --open
start "" wscript.exe "%DST%\laptop\loop.vbs"
echo.
echo  ALL DONE.
echo   - Clips: %USERPROFILE%\ClipRush\(today's date)
echo   - App: open in your browser, already unlocked
echo   - Phone: use the phone setup page that opened
echo   - Any time: double-click "ClipRush - Find clips now" on your Desktop
pause
exit /b 0

rem ---------------------------------------------------------------- helpers
:findpy
set "V=%~1"
set "VN=%V:.=%"
if exist "%LOCALAPPDATA%\Programs\Python\Python%VN%\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%VN%\python.exe" & goto :eof
if exist "%LOCALAPPDATA%\Python\pythoncore-%V%-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-%V%-64\python.exe" & goto :eof
if exist "%ProgramFiles%\Python%VN%\python.exe" set "PY=%ProgramFiles%\Python%VN%\python.exe" & goto :eof
for /f "delims=" %%P in ('py -%V% -c "import sys;print(sys.executable)" 2^>nul') do if exist "%%P" set "PY=%%P"
goto :eof
