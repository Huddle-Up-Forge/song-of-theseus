"""Song of Theseus entry point. The UI is React and the API is FastAPI."""

from __future__ import annotations

# Keep the old helper names available for local one-off scripts in outputs/.
from workflow import (  # noqa: F401
    CHECKPOINT_DIR,
    DEVICE,
    DEMUCS_SAMPLE_RATE,
    OUTPUT_ROOT,
    _format_time,
    _generate_vocal_segment,
    _get_singer,
    _parse_lrc,
)
from server import run_server


if __name__ == "__main__":
    run_server()
