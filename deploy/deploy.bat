@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not installed. Install and start Docker Desktop, then run this file again.
  pause
  exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
  echo Docker Compose is unavailable. Update Docker Desktop and try again.
  pause
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not running. Start it and try again.
  pause
  exit /b 1
)

if exist "%~dp0source\Dockerfile" (
  for %%I in ("%~dp0source") do set "SONG_OF_THESEUS_SOURCE=%%~fI"
) else if exist "%~dp0..\Dockerfile" (
  for %%I in ("%~dp0..") do set "SONG_OF_THESEUS_SOURCE=%%~fI"
) else (
  echo The Song of Theseus source folder is missing. Extract the full deployment bundle and try again.
  pause
  exit /b 1
)

for %%I in ("%~dp0data") do set "SONG_OF_THESEUS_DATA=%%~fI"
echo Building Song of Theseus from the included source. The first build may take several minutes.
docker compose -f "%~dp0compose.yaml" build
if errorlevel 1 (
  echo Song of Theseus image build failed.
  pause
  exit /b 1
)

docker compose -f "%~dp0compose.yaml" up -d
if errorlevel 1 (
  echo Container startup failed. Check NVIDIA GPU support in Docker Desktop.
  pause
  exit /b 1
)

echo Song of Theseus is starting at http://localhost:23253
start "" "http://localhost:23253"
