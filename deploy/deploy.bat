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

for %%I in ("%~dp0data") do set "SONG_OF_THESEUS_DATA=%%~fI"
docker compose -f "%~dp0compose.yaml" pull
if errorlevel 1 (
  echo Could not download the Song of Theseus image.
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
