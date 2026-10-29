@echo off
cd /d "%~dp0"
for %%I in ("%~dp0..\YingMusic-Singer-Plus") do set "PLUS_ROOT=%%~fI"
set "YINGMUSIC_PLUS_ROOT=%PLUS_ROOT%"
set "YINGSTUDIO_HOST=0.0.0.0"
set "YINGSTUDIO_PORT=23253"
set "HF_HOME=%PLUS_ROOT%\pretrained_models\hf_home"
set "HF_HUB_CACHE=%HF_HOME%\hub"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "ESPEAK_DIR=%PLUS_ROOT%\pretrained_models\espeak-ng-local\eSpeak NG"
set "PHONEMIZER_ESPEAK_LIBRARY=%ESPEAK_DIR%\libespeak-ng.dll"
set "PHONEMIZER_ESPEAK_DATA_PATH=%ESPEAK_DIR%\espeak-ng-data"
set "PATH=%ESPEAK_DIR%;%PATH%"
set "PYTHONPATH=%PLUS_ROOT%;%PLUS_ROOT%\src;%CD%"
if not exist ".venv\Scripts\python.exe" (
  echo Missing the shared web/audio .venv. See LOCAL_WINDOWS.md for setup instructions.
  exit /b 1
)
if not exist "%PLUS_ROOT%\.venv\Scripts\python.exe" (
  echo YingMusic-Singer-Plus runtime is missing at "%PLUS_ROOT%\.venv".
  exit /b 1
)
if not exist "frontend\dist\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo Node.js and npm are needed to build the Song of Theseus web interface.
    exit /b 1
  )
  if not exist "frontend\node_modules" (
    pushd frontend
    call npm install
    if errorlevel 1 exit /b 1
    popd
  )
  pushd frontend
  call npm run build
  if errorlevel 1 exit /b 1
  popd
)
"%PLUS_ROOT%\.venv\Scripts\python.exe" app.py
