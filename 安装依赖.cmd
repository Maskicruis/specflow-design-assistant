@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if errorlevel 1 (
  python -m venv .venv
) else (
  py -3.13 -m venv .venv
)
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Run the demo launcher.
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.13 and enable the Python launcher / PATH option.
pause
exit /b 1
