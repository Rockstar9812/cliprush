@echo off
cd /d "%~dp0\.."
rem One laptop cycle: finish episodes the cloud could not read, study X, then cut clips.
python -m brain laptop >> "laptop\last_run.log" 2>&1
rem Keep the log small
for %%A in ("laptop\last_run.log") do if %%~zA GTR 2000000 del "laptop\last_run.log"
