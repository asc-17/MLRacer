@echo off
rem Start the gesture app.  run.bat --preview  = test without the car.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe main.py %*
if errorlevel 1 pause
