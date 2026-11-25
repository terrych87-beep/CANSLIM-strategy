@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Sync SSE 1m/5m/30m/1d/1w and IF runpoints via miniconda3 ...
call env\run_with_conda.bat scripts\run_live_sync.py
if errorlevel 1 echo [FAILED] See messages above. Is miniconda3 installed?
pause
