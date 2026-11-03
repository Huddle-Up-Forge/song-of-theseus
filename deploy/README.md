# Song of Theseus — Docker deployment

## Windows

1. Install Docker Desktop with the WSL 2 backend and start it.
2. Enable NVIDIA GPU support for Docker Desktop.
3. Extract this folder and double-click `deploy.bat`.
4. Open [http://localhost:23253](http://localhost:23253). The first-run setup downloads model weights into `data/`.

The deployment folder can be downloaded from the [latest deployment bundle](https://github.com/Huddle-Up-Forge/song-of-theseus/releases/download/deploy-latest/song-of-theseus-deploy.zip). To stop the service, run `stop.bat`.

## Linux

Install Docker Compose and the NVIDIA Container Toolkit, then run:

```sh
./deploy.sh
```

The model files, workspaces, and singer library remain in the `data/` directory beside the Compose file. Set `YINGSTUDIO_PORT` to use a different host port.

## Model sources

The setup page downloads YingMusic-Singer-Plus and Whisper large-v3 from their pinned Hugging Face revisions and htdemucs from its upstream checkpoint host. You can also upload local files from Settings. Model weights are stored in `data/models/` and are not part of this deployment bundle.

The container includes the Song of Theseus source under AGPL-3.0-only and the pinned YingMusic-Singer-Plus inference source. Its upstream notices are included in the image and repository.

## GPU support

- [Docker Compose GPU access](https://docs.docker.com/compose/how-tos/gpu-support/)
- [NVIDIA CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/)
