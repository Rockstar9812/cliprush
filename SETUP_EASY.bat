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
  rem keep your notes file reachable for the key finder
  for %%F in ("%SRC%..\*.txt" "%SRC%*.txt") do copy /y "%%~F" "%DST%\" >nul 2>nul
)
cd /d "%DST%"

rem ---- 2. Python (installs free Python 3.12 if missing, no restart needed)
set "PY="
for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P | find /i "WindowsApps" >nul
  if errorlevel 1 if not defined PY set "PY=%%P"
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  echo Installing Python 3.12 ...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not exist "%PY%" (
  echo Python could not be installed automatically. Install it from python.org, then run this again.
  pause & exit /b 1
)
echo Using Python: %PY%

rem ---- 3. FFmpeg (for cutting clips)
where ffmpeg >nul 2>nul || (
  echo Installing FFmpeg ...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
)

rem ---- 4. Packages
echo Installing packages (1-2 minutes) ...
"%PY%" -m pip install --upgrade pip >nul 2>nul
"%PY%" -m pip install -q -r requirements.txt qrcode
"%PY%" -m pip install -q -U yt-dlp

rem ---- 5. Read your keys from your notes, check them, unlock the app
"%PY%" laptop\easy_setup.py

rem ---- 6. Run silently every 30 minutes + at sign-in, and add the Desktop button
> "laptop\run_laptop.bat" echo @echo off
>> "laptop\run_laptop.bat" echo cd /d "%DST%"
>> "laptop\run_laptop.bat" echo "%PY%" -m brain laptop ^>^> "laptop\last_run.log" 2^>^&1
schtasks /create /f /tn "ClipRush" /sc minute /mo 30 /tr "wscript.exe \"%DST%\laptop\run_hidden.vbs\"" >nul
schtasks /create /f /tn "ClipRush at sign-in" /sc onlogon /tr "wscript.exe \"%DST%\laptop\run_hidden.vbs\"" >nul
> "%USERPROFILE%\Desktop\ClipRush - Find clips now.bat" echo @call "%DST%\laptop\Find clips now.bat"

rem ---- 7. First run right now
echo.
echo Finding clips now (first run takes 3-10 minutes) ...
"%PY%" -m brain laptop --open
echo.
echo  ALL DONE.
echo   - Clips: C:\Users\%USERNAME%\ClipRush\(today's date)
echo   - App: already open and unlocked in your browser
echo   - Phone: scan the QR code page that opened
echo   - Next time: double-click "ClipRush - Find clips now" on your Desktop
pause
