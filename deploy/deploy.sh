#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Engine with the NVIDIA Container Toolkit first." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start the Docker service and try again." >&2
  exit 1
fi

if [ -f "$(pwd)/source/Dockerfile" ]; then
  export SONG_OF_THESEUS_SOURCE="$(pwd)/source"
elif [ -f "$(pwd)/../Dockerfile" ]; then
  export SONG_OF_THESEUS_SOURCE="$(cd .. && pwd)"
else
  echo "The Song of Theseus source folder is missing. Extract the full deployment bundle and try again." >&2
  exit 1
fi

export SONG_OF_THESEUS_DATA="${SONG_OF_THESEUS_DATA:-$(pwd)/data}"
echo "Building Song of Theseus from the included source. The first build may take several minutes."
docker compose -f compose.yaml build
docker compose -f compose.yaml up -d
echo "Song of Theseus is starting at http://localhost:${YINGSTUDIO_PORT:-23253}"
