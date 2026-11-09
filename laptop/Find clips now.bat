@echo off
title ClipRush - Find clips now
cd /d "%~dp0\.."
set "PY=python"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
echo.
echo  ClipRush - find clips right now (this laptop reads YouTube directly).
echo.
set "URL="
set /p URL=Paste a YouTube link to clip it (or just press Enter to check all podcasts): 
echo.
if defined URL (
  "%PY%" -m brain laptop --open --video "%URL%"
) else (
  "%PY%" -m brain laptop --open
)
echo.
echo Done. Your clips folder is open. The app shows the new clips too.
pause
