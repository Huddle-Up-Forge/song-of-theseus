@echo off
setlocal
cd /d "%~dp0"
for %%I in ("%~dp0data") do set "SONG_OF_THESEUS_DATA=%%~fI"
docker compose -f "%~dp0compose.yaml" down
