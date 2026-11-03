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

export SONG_OF_THESEUS_DATA="${SONG_OF_THESEUS_DATA:-$(pwd)/data}"
docker compose -f compose.yaml pull
docker compose -f compose.yaml up -d
echo "Song of Theseus is starting at http://localhost:${YINGSTUDIO_PORT:-23253}"
