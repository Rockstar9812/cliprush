@echo off
setlocal
title ClipRush laptop setup
cd /d "%~dp0\.."
echo.
echo ===== ClipRush laptop setup (free, one time) =====
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Installing Python 3.12 ...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. CLOSE this window and double-click install_windows.bat again.
  pause
  exit /b
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Installing FFmpeg ...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
)

echo Installing Python packages ...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
python -m pip install -U yt-dlp

if not exist "laptop\cliprush.env" (
  copy "laptop\cliprush.env.example" "laptop\cliprush.env" >nul
  echo.
  echo Fill in laptop\cliprush.env - Notepad is opening. Save it, then close Notepad.
  notepad "laptop\cliprush.env"
)

echo.
echo Registering background task: every 30 minutes and at sign-in ...
schtasks /create /f /tn "ClipRush" /sc minute /mo 30 /tr "wscript.exe \"%~dp0run_hidden.vbs\"" >nul
schtasks /create /f /tn "ClipRush at sign-in" /sc onlogon /tr "wscript.exe \"%~dp0run_hidden.vbs\"" >nul

echo.
echo Running the self-test ...
python -m brain selftest --online
echo.
echo Done. ClipRush now runs by itself whenever this laptop is on.
echo Clips are saved to the folder set in config\settings.yaml - laptop.clips_folder
pause
