@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Start structure viz web UI via miniconda3 ...
call env\run_with_conda.bat 网页结构可视化.py
if errorlevel 1 echo [FAILED] See messages above. Is miniconda3 installed?
pause
