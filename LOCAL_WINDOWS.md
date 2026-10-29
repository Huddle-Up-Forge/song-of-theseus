# Song of Theseus on Windows

This local workbench uses React + Vite for the interface and FastAPI for the local API. A persistent SQLite queue runs htdemucs, line previews, and full-song generation on one background GPU worker. The browser can keep editing and following progress while those jobs run. Singing generation loads the separately checked out `../YingMusic-Singer-Plus` runtime and checkpoints; model code and weights are not bundled in this repository.

## Start

Double-click `run_local.bat`, then open [http://127.0.0.1:23253](http://127.0.0.1:23253). The server listens on port `23253` on all network adapters so other devices on the same LAN can open `http://<this-PC-LAN-IP>:23253`. The home page lists saved rewrite and MIDI workspaces; open one to continue, or start a new workspace. Lyric drafts are saved to the local project metadata as you edit. The first launch builds the frontend if `frontend/dist` is missing.

## Workflow

1. Upload a WAV, MP3, FLAC, or OGG and provide a timestamped LRC file or paste LRC text. If you do not have an LRC, enable local Whisper large-v3 lyric recognition; it runs on the separated vocal stem and creates editable timestamped draft lines.
2. The preparation job separates the source into vocals and accompaniment, then recognizes lyrics when no LRC was provided. The page stays responsive and shows the background job's current stage.
3. Edit lyrics line by line. Each row has original-singing playback and an asynchronous re-sing preview. Previews enter a high-priority queue so they can run between full-song line jobs.
4. Generate the complete song. Each changed line is a separately scheduled item; untouched vocals and the original accompaniment are preserved. The final WAV is available for playback and download.

For MIDI lyric filling, open **MIDI 填词**, upload a Standard MIDI file, choose the melody track and a singer, then enter lyrics for each automatically divided phrase. Sections stay below 38 seconds and are generated sequentially with the same singer reference before being joined into a WAV. MIDI lyrics are manually supplied; the model maps the text to the note sequence.

Job status is stored in `outputs/yingstudio.sqlite3`, so queued work and project metadata survive a page refresh. A single model worker avoids concurrent CUDA memory spikes. The diffusion model supports 8–64 steps in the interface; higher step counts take longer.

The first automatic-recognition run downloads the Whisper large-v3 checkpoint into `ckpts/faster-whisper`. Music transcription can mishear or hallucinate words, especially on short or unclear clips, so review the generated lines before re-singing. Plus checkpoints are cached locally under `../YingMusic-Singer-Plus/pretrained_models/hf_home`. Demucs downloads its checkpoint into the normal PyTorch cache when first used.

## Recreate the environment

Use Python 3.12 for this project environment. Install a CUDA-enabled PyTorch wheel that matches the installed NVIDIA driver before the repository requirements. The launcher also requires the official Plus checkout and its own `.venv` at `../YingMusic-Singer-Plus`; follow the upstream setup instructions there and keep its checkpoint cache in place:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Install Node.js LTS once. If `frontend/dist` is missing, `run_local.bat` installs the frontend dependencies and builds it. The launcher starts the Python API with the Plus checkout's `.venv`; this repository's `.venv` is also checked as an environment setup prerequisite. If adapting the environments, ensure FastAPI, Faster-Whisper, Demucs, and the Plus inference dependencies are available to the Python interpreter used by the launcher.
