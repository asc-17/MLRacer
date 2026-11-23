@echo off
rem One-time setup (needs internet): Python packages + the MediaPipe hand model.
rem Do this BEFORE joining the car's Wi-Fi - that network has no internet.
cd /d "%~dp0"

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :fail
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail

if not exist models mkdir models
if not exist models\hand_landmarker.task (
  echo Downloading the hand model...
  .venv\Scripts\python.exe -c "import urllib.request as u; u.urlretrieve('https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task', r'models\hand_landmarker.task')" || goto :fail
)

echo.
echo Setup done. Start the app with run.bat
pause
exit /b 0

:fail
echo.
echo Setup FAILED - see the messages above.
pause
exit /b 1
