@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" manage.py stop
if errorlevel 1 goto end
".venv\Scripts\python.exe" manage.py backup
:end
pause
