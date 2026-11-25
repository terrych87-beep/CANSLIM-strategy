@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Start backtest web UI via miniconda3 ...
call env\run_with_conda.bat 网页运行调试策略.py
if errorlevel 1 echo [FAILED] See messages above. Is miniconda3 installed?
pause
