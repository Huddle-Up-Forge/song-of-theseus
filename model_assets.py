"""Download, inspect, and install inference assets for Song of Theseus."""

from __future__ import annotations

import os
import shutil
import threading
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("YINGSTUDIO_DATA_ROOT", ROOT / "outputs")).expanduser().resolve()
MODEL_ROOT = Path(os.environ.get("YINGSTUDIO_MODEL_ROOT", ROOT / "ckpts" / "models")).expanduser().resolve()
MODEL_ROOT.mkdir(parents=True, exist_ok=True)

PLUS_ROOT = Path(
    os.environ.get("YINGMUSIC_PLUS_ROOT", ROOT.parent / "YingMusic-Singer-Plus")
).expanduser().resolve()
PLUS_MODEL_DIR = MODEL_ROOT / "YingMusic-Singer-Plus"
WHISPER_MODEL_DIR = MODEL_ROOT / "faster-whisper-large-v3"
PLUS_REPO_ID = "ASLP-lab/YingMusic-Singer-Plus"
PLUS_REVISION = "9b3f444f2bccd77fbc03e32eb7c334fc96040b8d"
WHISPER_REPO_ID = "Systran/faster-whisper-large-v3"
WHISPER_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
DEMUCS_CHECKPOINT_NAME = "955717e8-8726e21a.th"
DEMUCS_DOWNLOAD_URL = (
    "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/"
    f"{DEMUCS_CHECKPOINT_NAME}"
)
PLUS_CACHE_ROOT = Path(
    os.environ.get(
        "HF_HUB_CACHE",
        PLUS_ROOT / "pretrained_models" / "hf_home" / "hub",
    )
).expanduser()
LEGACY_WHISPER_ROOT = ROOT / "ckpts" / "faster-whisper"

# Docker stores all heavyweight caches in its mounted /data directory. A local
# Windows install keeps using the normal Torch cache, including existing files.
if os.environ.get("YINGSTUDIO_DATA_ROOT") or os.environ.get("YINGSTUDIO_MODEL_ROOT"):
    os.environ.setdefault("TORCH_HOME", str(MODEL_ROOT / "torch"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

MODEL_INFO: dict[str, dict[str, Any]] = {
    "singing": {
        "name": "YingMusic-Singer-Plus",
        "description": "改词演唱与旋律保持模型",
        "size": "约 2.9 GB",
        "source_url": f"https://huggingface.co/{PLUS_REPO_ID}/tree/{PLUS_REVISION}",
        "path": str(PLUS_MODEL_DIR),
    },
    "demucs": {
        "name": "htdemucs",
        "description": "原曲人声与伴奏分离模型",
        "size": "约 80 MB",
        "source_url": "https://github.com/facebookresearch/demucs",
        "path": str(
            Path(os.environ.get("TORCH_HOME", Path.home() / ".cache" / "torch")).expanduser()
            / "hub" / "checkpoints" / DEMUCS_CHECKPOINT_NAME
        ),
    },
    "whisper": {
        "name": "Whisper large-v3",
        "description": "本地中文歌词识别模型",
        "size": "约 3.1 GB",
        "source_url": f"https://huggingface.co/{WHISPER_REPO_ID}/tree/{WHISPER_REVISION}",
        "path": str(WHISPER_MODEL_DIR),
    },
}

_LOCK = threading.RLock()
_DOWNLOADS: dict[str, dict[str, Any]] = {}
_ACTIVE_DOWNLOAD: str | None = None


def _demucs_candidates() -> list[Path]:
    candidates: list[Path] = []
    torch_home = os.environ.get("TORCH_HOME")
    if torch_home:
        candidates.append(Path(torch_home).expanduser() / "hub" / "checkpoints" / DEMUCS_CHECKPOINT_NAME)
    candidates.append(Path.home() / ".cache" / "torch" / "hub" / "checkpoints" / DEMUCS_CHECKPOINT_NAME)
    candidates.append(MODEL_ROOT / "torch" / "hub" / "checkpoints" / DEMUCS_CHECKPOINT_NAME)
    return list(dict.fromkeys(path.resolve() for path in candidates))


def _demucs_primary_path() -> Path:
    candidates = _demucs_candidates()
    return candidates[0] if candidates else MODEL_ROOT / "torch" / "hub" / "checkpoints" / DEMUCS_CHECKPOINT_NAME


def _plus_hf_cache_candidates() -> list[Path]:
    roots = [PLUS_CACHE_ROOT]
    configured = os.environ.get("HF_HUB_CACHE")
    if configured:
        roots.append(Path(configured).expanduser())
    legacy = PLUS_ROOT / "pretrained_models" / "hf_home" / "hub"
    roots.append(legacy)
    return list(dict.fromkeys(root.resolve() for root in roots))


def _plus_hf_snapshot() -> Path | None:
    for cache in _plus_hf_cache_candidates():
        snapshots = cache / "models--ASLP-lab--YingMusic-Singer-Plus" / "snapshots"
        if not snapshots.is_dir():
            continue
        for snapshot in sorted(snapshots.iterdir(), reverse=True):
            if (snapshot / "model.safetensors").is_file() and (snapshot / "config.json").is_file():
                return snapshot
    return None


def plus_model_ready() -> bool:
    direct_ready = (PLUS_MODEL_DIR / "model.safetensors").is_file() and (PLUS_MODEL_DIR / "config.json").is_file()
    return direct_ready or _plus_hf_snapshot() is not None


def whisper_model_ready() -> bool:
    required = ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")
    if all((WHISPER_MODEL_DIR / name).is_file() for name in required):
        return True
    return _whisper_hf_snapshot() is not None


def _whisper_hf_snapshot() -> Path | None:
    required = ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")
    snapshots = LEGACY_WHISPER_ROOT / "models--Systran--faster-whisper-large-v3" / "snapshots"
    if snapshots.is_dir():
        for snapshot in sorted(snapshots.iterdir(), reverse=True):
            if all((snapshot / name).is_file() for name in required):
                return snapshot
    return None


def demucs_model_ready() -> bool:
    return any(path.is_file() and path.stat().st_size > 10_000_000 for path in _demucs_candidates())


def active_plus_model() -> str:
    if (PLUS_MODEL_DIR / "model.safetensors").is_file() and (PLUS_MODEL_DIR / "config.json").is_file():
        return str(PLUS_MODEL_DIR)
    return PLUS_REPO_ID


def active_whisper_model() -> str:
    if whisper_model_ready() and all(
        (WHISPER_MODEL_DIR / name).is_file()
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")
    ):
        return str(WHISPER_MODEL_DIR)
    return "large-v3"


def _ready(asset_id: str) -> bool:
    return {
        "singing": plus_model_ready,
        "demucs": demucs_model_ready,
        "whisper": whisper_model_ready,
    }[asset_id]()


def setup_state() -> dict[str, Any]:
    models = []
    with _LOCK:
        for asset_id, info in MODEL_INFO.items():
            ready = _ready(asset_id)
            download = dict(_DOWNLOADS.get(asset_id, {}))
            transfer_status = download.get("status")
            state = transfer_status if transfer_status in {"downloading", "uploading"} else "ready" if ready else transfer_status or "missing"
            if state == "ready":
                download = {}
            active_path = info["path"]
            if asset_id == "singing":
                active_path = str(PLUS_MODEL_DIR if (PLUS_MODEL_DIR / "model.safetensors").is_file() else _plus_hf_snapshot() or PLUS_MODEL_DIR)
            elif asset_id == "demucs":
                active_path = str(next((path for path in _demucs_candidates() if path.is_file()), _demucs_primary_path()))
            elif asset_id == "whisper":
                active_path = str(WHISPER_MODEL_DIR if (WHISPER_MODEL_DIR / "model.bin").is_file() else _whisper_hf_snapshot() or WHISPER_MODEL_DIR)
            models.append(
                {
                    "id": asset_id,
                    **info,
                    "path": active_path,
                    "ready": ready,
                    "status": state,
                    "progress": float(download.get("progress", 0.0)),
                    "message": download.get("message", ""),
                    "error": download.get("error", ""),
                    "downloaded_bytes": int(download.get("downloaded_bytes", 0)),
                    "total_bytes": int(download.get("total_bytes", 0)),
                }
            )
    return {
        "ready": all(item["ready"] for item in models),
        "core_ready": all(item["ready"] for item in models if item["id"] != "whisper"),
        "data_root": str(DATA_ROOT),
        "model_root": str(MODEL_ROOT),
        "models": models,
    }


def _update(asset_id: str, **values: Any) -> None:
    with _LOCK:
        current = _DOWNLOADS.setdefault(asset_id, {})
        current.update(values)


def _download_file(asset_id: str, url: str, destination: Path, *, progress_start: float, progress_span: float, label: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "Song-of-Theseus/1.0 (model setup)"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code != 416 or not offset:
            raise RuntimeError(f"{label}下载失败：HTTP {exc.code}。") from exc
        partial.unlink(missing_ok=True)
        offset = 0
        request = urllib.request.Request(url, headers={"User-Agent": "Song-of-Theseus/1.0 (model setup)"})
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{label}连接失败：{exc.reason}") from exc
    with response:
        status = getattr(response, "status", 200)
        append = offset > 0 and status == 206
        if not append:
            offset = 0
        content_length = int(response.headers.get("Content-Length", "0") or 0)
        total_bytes = offset + content_length
        mode = "ab" if append else "wb"
        downloaded = offset
        with partial.open(mode) as target:
            while chunk := response.read(8 * 1024 * 1024):
                target.write(chunk)
                downloaded += len(chunk)
                fraction = min(1.0, downloaded / total_bytes) if total_bytes else 0.0
                _update(
                    asset_id,
                    status="downloading",
                    message=f"{label} · {downloaded / 1024**3:.2f} GB",
                    progress=progress_start + progress_span * fraction,
                    downloaded_bytes=downloaded,
                    total_bytes=total_bytes,
                )
        if total_bytes and downloaded != total_bytes:
            raise RuntimeError(f"{label}下载未完成：收到 {downloaded} / {total_bytes} 字节。再次点击可继续下载。")
    os.replace(partial, destination)


def _download_asset(asset_id: str) -> None:
    global _ACTIVE_DOWNLOAD
    try:
        if asset_id == "singing":
            files = ("config.json", "model.safetensors")
            weights = {"config.json": 0.01, "model.safetensors": 0.99}
            for filename in files:
                _download_file(
                    asset_id,
                    f"https://huggingface.co/{PLUS_REPO_ID}/resolve/{PLUS_REVISION}/{filename}?download=true",
                    PLUS_MODEL_DIR / filename,
                    progress_start=0.04 if filename == "model.safetensors" else 0.0,
                    progress_span=0.94 if filename == "model.safetensors" else 0.04,
                    label=filename,
                )
            if not plus_model_ready():
                raise RuntimeError("YingMusic-Singer-Plus 权重校验失败。")
        elif asset_id == "demucs":
            _download_file(
                asset_id,
                DEMUCS_DOWNLOAD_URL,
                _demucs_primary_path(),
                progress_start=0.02,
                progress_span=0.96,
                label="htdemucs 检查点",
            )
            if not demucs_model_ready():
                raise RuntimeError("htdemucs 检查点文件不完整。")
        elif asset_id == "whisper":
            files = (
                "config.json",
                "preprocessor_config.json",
                "tokenizer.json",
                "vocabulary.json",
                "model.bin",
            )
            weights = {
                "config.json": 0.001,
                "preprocessor_config.json": 0.001,
                "tokenizer.json": 0.002,
                "vocabulary.json": 0.001,
                "model.bin": 0.995,
            }
            progress = 0.0
            for filename in files:
                span = weights[filename]
                _download_file(
                    asset_id,
                    f"https://huggingface.co/{WHISPER_REPO_ID}/resolve/{WHISPER_REVISION}/{filename}?download=true",
                    WHISPER_MODEL_DIR / filename,
                    progress_start=progress,
                    progress_span=span,
                    label=filename,
                )
                progress += span
            if not whisper_model_ready():
                raise RuntimeError("Whisper large-v3 模型文件校验失败。")
        else:
            raise ValueError("不支持的模型类型。")
        _update(asset_id, status="ready", progress=1.0, message="权重已就绪", error="")
    except Exception as exc:
        _update(asset_id, status="failed", message="下载中断", error=str(exc))
    finally:
        with _LOCK:
            _ACTIVE_DOWNLOAD = None


def start_download(asset_id: str) -> dict[str, Any]:
    global _ACTIVE_DOWNLOAD
    if asset_id not in MODEL_INFO:
        raise ValueError("找不到这个模型。")
    with _LOCK:
        if _ready(asset_id):
            return setup_state()
        if _ACTIVE_DOWNLOAD:
            raise RuntimeError(f"{MODEL_INFO[_ACTIVE_DOWNLOAD]['name']} 正在下载，请完成后再开始下一项。")
        _ACTIVE_DOWNLOAD = asset_id
        _DOWNLOADS[asset_id] = {
            "status": "downloading",
            "progress": 0.01,
            "message": "正在连接官方模型仓库",
            "error": "",
            "downloaded_bytes": 0,
            "total_bytes": 0,
        }
        threading.Thread(target=_download_asset, args=(asset_id,), daemon=True).start()
    return setup_state()


def _extract_archive(asset_id: str, archive_path: Path, staging_dir: Path) -> list[Path]:
    allowed = {
        "singing": {"model.safetensors", "config.json"},
        "whisper": {"model.bin", "config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"},
        "demucs": {DEMUCS_CHECKPOINT_NAME},
    }[asset_id]
    extracted: list[Path] = []
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                relative = PurePosixPath(item.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("压缩包中包含无效路径。")
                if item.is_dir():
                    continue
                if item.file_size > 10 * 1024**3:
                    raise ValueError("压缩包内单个文件超过 10 GB。")
                total_uncompressed += item.file_size
                if total_uncompressed > 12 * 1024**3:
                    raise ValueError("压缩包解压后超过 12 GB。")
                if relative.name not in allowed:
                    continue
                mode = (item.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError("压缩包中不接受符号链接。")
                target = staging_dir / relative.name
                with archive.open(item) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
                extracted.append(target)
    except zipfile.BadZipFile as exc:
        raise ValueError("无法读取 ZIP 文件，请上传包含模型文件的压缩包。") from exc
    return extracted


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def install_uploaded_file(asset_id: str, filename: str, fileobj: BinaryIO) -> dict[str, Any]:
    if asset_id not in MODEL_INFO:
        raise ValueError("不支持的模型类型。")
    suffix = Path(filename or "").suffix.lower()
    if asset_id == "singing" and suffix not in {".zip", ".safetensors"}:
        raise ValueError("YingMusic-Singer-Plus 请上传 .safetensors 权重或包含 model.safetensors 的 ZIP。")
    if asset_id == "whisper" and suffix != ".zip":
        raise ValueError("Whisper large-v3 请上传包含 model.bin 和 tokenizer 文件的 ZIP。")
    if asset_id == "demucs" and suffix not in {".zip", ".th"}:
        raise ValueError(f"htdemucs 请上传 {DEMUCS_CHECKPOINT_NAME} 或包含该文件的 ZIP。")

    staging = MODEL_ROOT / ".staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    raw_path = staging / f"upload{suffix}"
    try:
        with raw_path.open("wb") as target:
            shutil.copyfileobj(fileobj, target, length=8 * 1024 * 1024)
        files = _extract_archive(asset_id, raw_path, staging) if suffix == ".zip" else [raw_path]
        by_name = {path.name: path for path in files}

        if asset_id == "singing":
            weight = by_name.get("model.safetensors")
            if weight is None and suffix == ".safetensors":
                weight = raw_path
            if weight is None or weight.stat().st_size < 1_000_000_000:
                raise ValueError("YingMusic-Singer-Plus 权重文件不完整，需要完整的 model.safetensors。")
            config = by_name.get("config.json")
            if config is None:
                config = ROOT / "model-configs" / "yingmusic-singer-plus" / "config.json"
            if not config.is_file():
                raise ValueError("模型配置文件缺失，请上传同时包含 config.json 的 ZIP 包。")
            _atomic_copy(weight, PLUS_MODEL_DIR / "model.safetensors")
            _atomic_copy(config, PLUS_MODEL_DIR / "config.json")
        elif asset_id == "whisper":
            required = ("model.bin", "config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json")
            missing = [name for name in required if name not in by_name]
            if missing:
                raise ValueError(f"Whisper 压缩包缺少文件：{', '.join(missing)}。")
            if by_name["model.bin"].stat().st_size < 1_000_000_000:
                raise ValueError("Whisper large-v3 的 model.bin 文件不完整。")
            for name in required:
                _atomic_copy(by_name[name], WHISPER_MODEL_DIR / name)
        else:
            weight = by_name.get(DEMUCS_CHECKPOINT_NAME)
            if weight is None and suffix == ".th":
                weight = raw_path
            if weight is None or weight.stat().st_size < 10_000_000:
                raise ValueError(f"{DEMUCS_CHECKPOINT_NAME} 检查点不完整。")
            _atomic_copy(weight, _demucs_primary_path())

        _update(asset_id, status="ready", progress=1.0, message="权重已安装", error="")
        return setup_state()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def upload_model(asset_id: str, filename: str, fileobj: Any) -> dict[str, Any]:
    if asset_id not in MODEL_INFO:
        raise ValueError("找不到这个模型。")
    with _LOCK:
        if _ACTIVE_DOWNLOAD:
            raise RuntimeError("请等当前模型下载完成后再上传。")
    _update(asset_id, status="uploading", progress=0.01, message="正在接收模型文件", error="")
    try:
        result = await __import__("asyncio").to_thread(install_uploaded_file, asset_id, filename, fileobj)
        return result
    except Exception as exc:
        _update(asset_id, status="failed", message="模型安装失败", error=str(exc))
        raise
