"""Audio and model operations for the Song of Theseus workbench.

This module deliberately contains no web-framework code. The API scheduler calls
these functions from its single background worker so CUDA inference never runs
inside a request handler.
"""

from __future__ import annotations

import json
import gc
import os
import re
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
PLUS_ROOT = Path(
    os.environ.get("YINGMUSIC_PLUS_ROOT", ROOT.parent / "YingMusic-Singer-Plus")
).resolve()
PLUS_SITE_PACKAGES = PLUS_ROOT / ".venv" / "Lib" / "site-packages"
PLUS_HF_HOME = PLUS_ROOT / "pretrained_models" / "hf_home"
PLUS_HF_CACHE = PLUS_HF_HOME / "hub"
PLUS_ESPEAK_DIR = PLUS_ROOT / "pretrained_models" / "espeak-ng-local" / "eSpeak NG"
PLUS_MODEL_ID = "ASLP-lab/YingMusic-Singer-Plus"
SINGING_MODEL_NAME = "YingMusic-Singer-Plus"

# The local Plus runtime has its own pinned inference dependencies. Keep those
# ahead of the legacy environment when the app is launched from another Python.
if PLUS_ROOT.is_dir():
    if PLUS_SITE_PACKAGES.is_dir() and str(PLUS_SITE_PACKAGES) not in sys.path:
        sys.path.insert(0, str(PLUS_SITE_PACKAGES))
    if str(PLUS_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUS_ROOT))
    os.environ.setdefault("HF_HOME", str(PLUS_HF_HOME))
    os.environ.setdefault("HF_HUB_CACHE", str(PLUS_HF_CACHE))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np
import soundfile as sf
import torch
import torchaudio

sys.path.insert(0, str(ROOT / "src"))

from demucs.apply import apply_model as apply_demucs
from demucs.pretrained import get_model as get_demucs_model


OUTPUT_ROOT = ROOT / "outputs" / "sessions"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
CHECKPOINT_DIR = ROOT / "ckpts"
DEMUCS_SAMPLE_RATE = 44_100
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d+))?\]")
OFFSET_RE = re.compile(r"\[offset:([+-]?\d+)\]", re.IGNORECASE)

_MODEL_LOCK = threading.Lock()
_singer: Any | None = None
_singer_error: str | None = None
_plus_dll_directory: Any | None = None
_demucs_model: torch.nn.Module | None = None


class WorkflowError(ValueError):
    """An input or model error that can be shown safely in the local UI."""


def _format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    remainder = max(0.0, seconds - 60 * minutes)
    return f"{minutes:02d}:{remainder:05.2f}"


def _parse_lrc(text: str, duration: float) -> list[dict[str, Any]]:
    offset_match = OFFSET_RE.search(text)
    offset_seconds = int(offset_match.group(1)) / 1000 if offset_match else 0.0
    stamped_lines: list[dict[str, Any]] = []

    for raw_line in text.replace("\ufeff", "").splitlines():
        matches = list(TIMESTAMP_RE.finditer(raw_line))
        if not matches:
            continue
        lyric = TIMESTAMP_RE.sub("", raw_line).strip()
        if not lyric:
            continue
        for match in matches:
            minutes, seconds, fraction = match.groups()
            timestamp = int(minutes) * 60 + int(seconds)
            if fraction:
                timestamp += float(f"0.{fraction}")
            timestamp = min(max(0.0, timestamp + offset_seconds), duration)
            stamped_lines.append({"start": timestamp, "text": lyric})

    stamped_lines.sort(key=lambda row: row["start"])
    merged: list[dict[str, Any]] = []
    for row in stamped_lines:
        if merged and abs(merged[-1]["start"] - row["start"]) < 0.02:
            merged[-1]["text"] = f'{merged[-1]["text"]} {row["text"]}'.strip()
        else:
            merged.append(row.copy())

    segments: list[dict[str, Any]] = []
    for index, row in enumerate(merged):
        end = merged[index + 1]["start"] if index + 1 < len(merged) else duration
        end = min(max(end, row["start"]), duration)
        if end - row["start"] < 0.15:
            continue
        segments.append(
            {
                "start": float(row["start"]),
                "end": float(end),
                "original": row["text"].strip(),
                "edited": row["text"].strip(),
            }
        )
    return segments


def _decode_lrc(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise WorkflowError("无法识别歌词文件编码，请另存为 UTF-8 后重试。")


def _transcription_text(words: list[tuple[float, float, str]], duration: float) -> str:
    """Turn Whisper word timestamps into editable, line-sized LRC entries."""
    rows: list[tuple[float, str]] = []
    current: list[tuple[float, float, str]] = []
    punctuation = "，。！？；：,.!?;:"

    def flush() -> None:
        if not current:
            return
        text = "".join(item[2] for item in current).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            start = min(max(0.0, current[0][0]), max(0.0, duration - 0.01))
            rows.append((start, text))
        current.clear()

    for start, end, raw_text in words:
        token = str(raw_text or "").strip()
        if not token or not np.isfinite(start):
            continue
        token = re.sub(r"\s+", "", token) if re.search(r"[\u3400-\u9fff]", token) else token
        if not token:
            continue
        current_text = "".join(item[2] for item in current)
        if current and (
            start - current[-1][1] >= 0.62
            or len(current_text) >= 16
            or start - current[0][0] >= 6.0
        ):
            flush()
        current.append((float(start), float(max(start, end)), token))
        if token[-1] in punctuation:
            flush()
    flush()

    # Some decoders omit word timestamps for a segment. Retain those phrases
    # as timed lines so the user still gets a useful draft to correct.
    if not rows:
        return ""
    return "\n".join(f"[{_format_time(start)}]{text}" for start, text in rows)


def _transcribe_lyrics(
    vocal_path: Path,
    duration: float,
    progress: Callable[[float, str], None],
) -> tuple[str, str]:
    """Run local Chinese Whisper ASR over the separated vocal stem."""
    model = None
    dll_directory = None
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WorkflowError("自动歌词识别组件未安装，请在项目虚拟环境安装 faster-whisper。") from exc

    try:
        if os.name == "nt":
            dll_path = Path(torch.__file__).resolve().parent / "lib"
            if dll_path.is_dir():
                dll_directory = os.add_dll_directory(str(dll_path))
        progress(0.77, "加载本地 Whisper large-v3 歌词识别模型")
        model = WhisperModel(
            "large-v3",
            device=DEVICE,
            compute_type="float16" if DEVICE == "cuda" else "int8",
            download_root=str(CHECKPOINT_DIR / "faster-whisper"),
        )
        progress(0.79, "正在识别人声中的中文歌词")
        # Decode with SoundFile and pass a 16 kHz mono array directly. This
        # avoids PyAV's file decoder, whose API varies across Windows builds.
        audio, sample_rate = sf.read(
            str(vocal_path),
            dtype="float32",
            always_2d=True,
        )
        mono = audio.mean(axis=1)
        if sample_rate != 16_000:
            from math import gcd
            from scipy.signal import resample_poly

            common = gcd(int(sample_rate), 16_000)
            mono = resample_poly(
                mono,
                16_000 // common,
                int(sample_rate) // common,
            ).astype(np.float32, copy=False)
        mono = np.ascontiguousarray(mono, dtype=np.float32)

        segments, info = model.transcribe(
            mono,
            language="zh",
            task="transcribe",
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 360},
            word_timestamps=True,
            initial_prompt="以下是中文歌曲歌词。请按音频原文识别，不要翻译；保留游戏术语、人名及英文词。",
        )
        timed_words: list[tuple[float, float, str]] = []
        for segment in segments:
            fraction = min(1.0, max(0.0, segment.end / max(1.0, duration)))
            progress(0.80 + 0.17 * fraction, f"识别歌词 {min(duration, segment.end):.0f}/{duration:.0f} 秒")
            segment_words = segment.words or []
            if not segment_words and segment.text.strip():
                timed_words.append((float(segment.start), float(segment.end), segment.text))
            for word in segment_words:
                if word.start is not None and word.end is not None and word.word:
                    timed_words.append((float(word.start), float(word.end), word.word))
        lrc_text = _transcription_text(timed_words, duration)
        if not lrc_text:
            raise WorkflowError("没有从人声中识别到歌词。请补传 LRC，或使用更清晰的人声录音。")
        return lrc_text, f"whisper-{info.language or 'zh'}-large-v3"
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError(f"本地歌词识别失败：{str(exc).strip() or type(exc).__name__}") from exc
    finally:
        if model is not None:
            del model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if dll_directory is not None:
            dll_directory.close()


def _load_audio_for_demucs(path: str) -> tuple[torch.Tensor, float]:
    try:
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise WorkflowError(
            "无法解码此音频。请使用 WAV、FLAC、OGG 或 MP3 文件。"
        ) from exc

    if audio.shape[1] > 2:
        audio = audio[:, :2]
    elif audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)

    waveform = torch.from_numpy(np.ascontiguousarray(audio.T)).float()
    if sample_rate != DEMUCS_SAMPLE_RATE:
        waveform = torchaudio.transforms.Resample(sample_rate, DEMUCS_SAMPLE_RATE)(waveform)
    duration = waveform.shape[-1] / DEMUCS_SAMPLE_RATE
    if duration < 2.0:
        raise WorkflowError("音频太短；请提供至少 2 秒的原曲。")
    if duration > 600:
        raise WorkflowError("当前本地流程最多处理 10 分钟音频，请先裁剪原曲。")
    return waveform.contiguous(), duration


def _separate_stems(
    mix: torch.Tensor, progress: Callable[[float, str], None] | None = None
) -> tuple[torch.Tensor, torch.Tensor]:
    """Run one htdemucs pass, keeping the separator off GPU after the job."""
    global _demucs_model
    if _demucs_model is None:
        _demucs_model = get_demucs_model("htdemucs").eval()

    if progress:
        progress(0.10, "将 htdemucs 放到 GPU，准备分离人声")
    _demucs_model.to(DEVICE)
    progress_module = None
    original_tqdm = None

    if progress:
        # Demucs reports completion once per overlapping audio chunk. Bridge
        # its iterator to job progress instead of leaving the old UI frozen at
        # a fixed percentage for the whole separation call.
        import demucs.apply as demucs_apply_module

        progress_module = demucs_apply_module.tqdm
        original_tqdm = progress_module.tqdm

        def report_chunks(iterable, *args, **kwargs):
            try:
                total = len(iterable)
            except TypeError:
                total = 0
            for index, item in enumerate(iterable, start=1):
                yield item
                if total:
                    fraction = index / total
                    progress(0.12 + 0.62 * fraction, f"htdemucs 正在分离（{index}/{total} 段）")

        progress_module.tqdm = report_chunks

    try:
        with torch.inference_mode():
            estimates = apply_demucs(
                _demucs_model,
                mix.unsqueeze(0),
                shifts=0,
                split=True,
                overlap=0.25,
                progress=bool(progress),
                device=DEVICE,
            )[0].cpu()
    finally:
        if progress_module is not None and original_tqdm is not None:
            progress_module.tqdm = original_tqdm
        _demucs_model.to("cpu")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    source_names = list(_demucs_model.sources)
    vocal = estimates[source_names.index("vocals")]
    accompaniment = sum(
        estimates[index] for index, name in enumerate(source_names) if name != "vocals"
    )
    return vocal.contiguous(), accompaniment.contiguous()


def _configure_plus_runtime() -> None:
    """Set up the colocated Plus code, espeak DLL, and WAV I/O backend."""
    global _plus_dll_directory
    if not PLUS_ROOT.is_dir():
        raise WorkflowError(f"找不到 YingMusic-Singer-Plus：{PLUS_ROOT}")

    if os.name == "nt":
        espeak_library = PLUS_ESPEAK_DIR / "libespeak-ng.dll"
        espeak_data = PLUS_ESPEAK_DIR / "espeak-ng-data"
        if not espeak_library.is_file() or not espeak_data.is_dir():
            raise WorkflowError("Plus 所需的本地 eSpeak NG 运行库缺失。")
        os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = str(espeak_library)
        os.environ["PHONEMIZER_ESPEAK_DATA_PATH"] = str(espeak_data)
        if _plus_dll_directory is None:
            _plus_dll_directory = os.add_dll_directory(str(PLUS_ESPEAK_DIR))
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        EspeakWrapper.set_library(str(espeak_library))
        EspeakWrapper.set_data_path(str(espeak_data))

    if not getattr(torchaudio.load, "_yingmusic_soundfile_backend", False):
        def load_with_soundfile(path: str | os.PathLike[str], *args: Any, **kwargs: Any):
            audio, sample_rate = sf.read(
                str(path), dtype="float32", always_2d=True
            )
            return torch.from_numpy(np.ascontiguousarray(audio.T)), int(sample_rate)

        load_with_soundfile._yingmusic_soundfile_backend = True  # type: ignore[attr-defined]
        torchaudio.load = load_with_soundfile  # type: ignore[assignment]


def _get_singer() -> Any:
    """Load the Plus checkpoint once and keep it resident for queued jobs."""
    global _singer, _singer_error
    if _singer is None:
        with _MODEL_LOCK:
            if _singer is None:
                try:
                    _configure_plus_runtime()
                    from src.YingMusicSinger.infer.YingMusicSinger import YingMusicSinger

                    _singer = YingMusicSinger.from_pretrained(
                        PLUS_MODEL_ID,
                        cache_dir=str(PLUS_HF_CACHE),
                        local_files_only=True,
                        model_cfg_path=str(
                            PLUS_ROOT / "src" / "YingMusicSinger" / "config" / "YingMusic_Singer.yaml"
                        ),
                        vae_config_path=str(
                            PLUS_ROOT
                            / "src"
                            / "YingMusicSinger"
                            / "config"
                            / "stable_audio_2_0_vae_20hz_official.json"
                        ),
                    ).to(DEVICE).eval()
                    print(f"{SINGING_MODEL_NAME} loaded on {DEVICE}.", flush=True)
                except Exception as exc:
                    _singer_error = str(exc)
                    raise WorkflowError(f"{SINGING_MODEL_NAME} 加载失败：{_singer_error}") from exc
    return _singer


def _run_plus_inference(
    project: dict[str, Any],
    melody_audio_path: str | Path,
    target_text: str,
    steps: int,
    seed: int,
) -> np.ndarray:
    """Run Plus inference and return float32 stereo audio at 44.1 kHz."""
    singer = _get_singer()
    waveform, sample_rate = singer(
        ref_audio_path=str(project["prompt_path"]),
        melody_audio_path=str(melody_audio_path),
        ref_text=str(project["prompt_text"]),
        target_text=target_text.strip(),
        lrc_align_mode="sentence_level",
        sil_len_to_end=0.5,
        t_shift=0.5,
        nfe_step=int(steps),
        cfg_strength=3.0,
        seed=int(seed),
    )
    if torch.cuda.is_available():
        torch.cuda.synchronize()

    audio = waveform.detach().to(torch.float32).cpu()
    if audio.ndim == 1:
        audio = audio.unsqueeze(0)
    if audio.shape[0] == 1:
        audio = audio.repeat(2, 1)
    elif audio.shape[0] > 2:
        audio = audio[:2]
    if int(sample_rate) != DEMUCS_SAMPLE_RATE:
        audio = torchaudio.transforms.Resample(
            int(sample_rate), DEMUCS_SAMPLE_RATE
        )(audio)
    return audio.numpy().T.astype(np.float32, copy=False)


def prepare_session(
    session_dir: Path,
    project_id: str,
    original_path: Path,
    lrc_text: str | None,
    progress: Callable[[float, str], None],
    singer_reference_path: Path | None = None,
    singer_reference_text: str | None = None,
    singer_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    progress(0.02, "读取原曲和 LRC")
    try:
        info = sf.info(str(original_path))
    except (OSError, RuntimeError) as exc:
        raise WorkflowError("无法读取上传的音频。请使用 WAV、FLAC、OGG 或 MP3。") from exc
    duration = float(info.duration)
    if duration < 2.0:
        raise WorkflowError("音频太短；请提供至少 2 秒的原曲。")
    if duration > 600:
        raise WorkflowError("当前本地流程最多处理 10 分钟音频，请先裁剪原曲。")

    segments = _parse_lrc(lrc_text, duration) if lrc_text and lrc_text.strip() else []
    if lrc_text and lrc_text.strip() and not segments:
        raise WorkflowError("没有找到有效的 LRC 时间戳和歌词，例如 [00:12.30]第一句歌词。")

    progress(0.08, "解码音频并统一到 44.1 kHz")
    mix, duration = _load_audio_for_demucs(str(original_path))
    progress(0.10, "开始人声/伴奏分离")
    vocals, accompaniment = _separate_stems(mix, progress)

    vocal_path = session_dir / "vocals.wav"
    accompaniment_path = session_dir / "accompaniment.wav"
    sf.write(vocal_path, vocals.numpy().T, DEMUCS_SAMPLE_RATE, subtype="PCM_16")
    sf.write(
        accompaniment_path,
        accompaniment.numpy().T,
        DEMUCS_SAMPLE_RATE,
        subtype="PCM_16",
    )

    lyrics_source = "lrc"
    if not segments:
        lrc_text, lyrics_source = _transcribe_lyrics(
            vocal_path,
            duration,
            progress,
        )
        segments = _parse_lrc(lrc_text, duration)
        if not segments:
            raise WorkflowError("歌词识别没有生成有效的时间戳。请补充 LRC 后重试。")

    prompt_start = max(0.0, segments[0]["start"] - 0.15)
    prompt_end = min(duration, prompt_start + 7.0)
    first_sample = int(prompt_start * DEMUCS_SAMPLE_RATE)
    last_sample = max(first_sample + 1, int(prompt_end * DEMUCS_SAMPLE_RATE))
    prompt_audio = vocals[:, first_sample:last_sample]
    prompt_path = session_dir / "voice_prompt.wav"
    sf.write(prompt_path, prompt_audio.numpy().T, DEMUCS_SAMPLE_RATE, subtype="PCM_16")
    prompt_lines = [
        segment["original"]
        for segment in segments
        if segment["start"] < prompt_end and segment["end"] > prompt_start
    ]
    project = {
        "id": project_id,
        "session_dir": str(session_dir),
        "original_path": str(original_path),
        "vocals_path": str(vocal_path),
        "accompaniment_path": str(accompaniment_path),
        "prompt_path": str(prompt_path),
        "prompt_text": " ".join(prompt_lines) or segments[0]["original"],
        "source_prompt_path": str(prompt_path),
        "source_prompt_text": " ".join(prompt_lines) or segments[0]["original"],
        "singer_profile": None,
        "sample_rate": DEMUCS_SAMPLE_RATE,
        "duration": duration,
        "status": "ready",
        "segments": segments,
        "lyrics_source": lyrics_source,
        "created_at": None,
        "last_generation": None,
    }
    if singer_reference_path and singer_reference_text and singer_profile:
        profile_prompt_path = session_dir / f"voice_profile_{singer_profile['id']}{singer_reference_path.suffix.lower()}"
        if singer_reference_path.resolve() != profile_prompt_path.resolve():
            shutil.copy2(singer_reference_path, profile_prompt_path)
        project["prompt_path"] = str(profile_prompt_path)
        project["prompt_text"] = singer_reference_text.strip()
        project["singer_profile"] = {
            "id": singer_profile["id"],
            "name": singer_profile["name"],
            "updated_at": singer_profile["updated_at"],
        }
    _write_project(project)
    progress(1.0, f"分离完成，读取到 {len(segments)} 句歌词")
    return project


def _write_project(project: dict[str, Any]) -> None:
    path = Path(project["session_dir"]) / "project.json"
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


MIDI_MAX_SECTION_SECONDS = 38.0
MIDI_SAMPLE_RATE = 44_100


def _midi_section_notes(instrument: Any, start: float, end: float) -> list[dict[str, Any]]:
    """Return clipped, section-relative note events for browser audition."""
    events = []
    for note in instrument.notes:
        clipped_start = max(float(start), float(note.start))
        clipped_end = min(float(end), float(note.end))
        if clipped_end <= clipped_start:
            continue
        events.append({
            "start": round(clipped_start - float(start), 4),
            "end": round(clipped_end - float(start), 4),
            "pitch": int(note.pitch),
            "velocity": int(note.velocity),
        })
    return events


def midi_note_counts_for_ranges(
    path: Path,
    track_index: int,
    ranges: list[tuple[float, float]],
) -> list[int]:
    """Count selected-track notes intersecting each requested time range."""
    try:
        import pretty_midi

        midi = pretty_midi.PrettyMIDI(str(path))
        instrument = midi.instruments[track_index]
    except (IndexError, ValueError, OSError) as exc:
        raise WorkflowError("所选 MIDI 音轨不存在或文件格式无效。") from exc
    if instrument.is_drum:
        raise WorkflowError("请选择包含旋律音符的非鼓组音轨。")
    return [len(_midi_section_notes(instrument, start, end)) for start, end in ranges]


def inspect_midi(path: Path) -> dict[str, Any]:
    """Read MIDI metadata and propose contiguous, beat-aligned lyric sections."""
    try:
        import pretty_midi

        midi = pretty_midi.PrettyMIDI(str(path))
    except Exception as exc:
        raise WorkflowError("无法读取此 MIDI。请确认它是有效的 .mid 或 .midi 文件。") from exc

    tracks: list[dict[str, Any]] = []
    for index, instrument in enumerate(midi.instruments):
        notes = instrument.notes
        name = instrument.name.strip() or pretty_midi.program_to_instrument_name(instrument.program)
        tracks.append({
            "index": index,
            "name": name,
            "program": int(instrument.program),
            "is_drum": bool(instrument.is_drum),
            "note_count": len(notes),
            "duration": max((float(note.end - note.start) for note in notes), default=0.0),
        })

    candidates = [track for track in tracks if not track["is_drum"] and track["note_count"]]
    if not candidates:
        raise WorkflowError("MIDI 中没有可用的旋律音符（鼓组音轨不会作为旋律）。")
    melody_terms = ("lead", "melody", "vocal", "voice", "sing", "主旋律", "主唱", "旋律")
    default_track = max(
        candidates,
        key=lambda track: (
            any(term in track["name"].casefold() for term in melody_terms),
            track["note_count"],
            track["duration"],
        ),
    )
    duration = max(float(midi.get_end_time()), max(note.end for item in candidates for note in midi.instruments[item["index"]].notes))
    if duration < 1.0:
        raise WorkflowError("MIDI 时长过短，请提供至少 1 秒的旋律。")
    if duration > 600.0:
        raise WorkflowError("目前 MIDI 工程最多支持 10 分钟，请先裁剪文件。")

    beat_times = [float(value) for value in midi.get_beats() if 0 < value < duration]
    beat_times.insert(0, 0.0)
    beat_times = sorted(set(beat_times))
    boundaries = [0.0]
    current = 0.0
    beat_index = 0
    while duration - current > MIDI_MAX_SECTION_SECONDS:
        while beat_index + 1 < len(beat_times) and beat_times[beat_index] < current - 0.02:
            beat_index += 1
        choices = []
        for beats in (64, 48, 32, 16, 8, 4, 1):
            candidate_index = beat_index + beats
            if candidate_index < len(beat_times):
                candidate = beat_times[candidate_index]
                if current + 0.5 < candidate <= current + MIDI_MAX_SECTION_SECONDS:
                    choices.append(candidate)
        if choices:
            next_boundary = max(choices)
        else:
            next_boundary = min(duration, current + MIDI_MAX_SECTION_SECONDS)
        if next_boundary <= current + 0.25:
            next_boundary = min(duration, current + MIDI_MAX_SECTION_SECONDS)
        boundaries.append(next_boundary)
        current = next_boundary
        beat_index = max(0, int(np.searchsorted(beat_times, current, side="left")))
    boundaries.append(duration)

    def section_rows(track_index: int) -> list[dict[str, Any]]:
        if track_index < 0 or track_index >= len(midi.instruments):
            raise WorkflowError("所选 MIDI 音轨不存在。")
        instrument = midi.instruments[track_index]
        if instrument.is_drum or not instrument.notes:
            raise WorkflowError("请选择包含旋律音符的非鼓组音轨。")
        rows = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            note_events = _midi_section_notes(instrument, start, end)
            rows.append({
                "index": index,
                "start": round(float(start), 3),
                "end": round(float(end), 3),
                "original": "",
                "edited": "",
                "has_notes": bool(note_events),
                "note_count": len(note_events),
                "notes": note_events,
            })
        return rows

    return {
        "duration": round(duration, 3),
        "tracks": tracks,
        "selected_track_index": int(default_track["index"]),
        "segments": section_rows(int(default_track["index"])),
    }


def midi_sections_for_track(path: Path, track_index: int) -> list[dict[str, Any]]:
    """Return section metadata using a caller-selected melody instrument."""
    inspected = inspect_midi(path)
    try:
        import pretty_midi

        midi = pretty_midi.PrettyMIDI(str(path))
        instrument = midi.instruments[track_index]
    except (IndexError, ValueError, OSError) as exc:
        raise WorkflowError("所选 MIDI 音轨不存在或文件格式无效。") from exc
    if instrument.is_drum or not instrument.notes:
        raise WorkflowError("请选择包含旋律音符的非鼓组音轨。")
    rows = []
    for index, (start, end) in enumerate(zip(
        [0.0] + [float(item["end"]) for item in inspected["segments"][:-1]],
        [float(item["end"]) for item in inspected["segments"]],
    )):
        note_events = _midi_section_notes(instrument, start, end)
        rows.append({
            "index": index,
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "original": "",
            "edited": "",
            "has_notes": bool(note_events),
            "note_count": len(note_events),
            "notes": note_events,
        })
    return rows


def _write_midi_section(
    project: dict[str, Any],
    index: int,
    output_path: Path,
    pitch_shift: int = 0,
) -> None:
    """Write one selected-track clip with note times relative to its section."""
    import pretty_midi

    segment = project["segments"][index]
    source = pretty_midi.PrettyMIDI(project["midi_path"])
    track_index = int(project["selected_track_index"])
    if track_index >= len(source.instruments):
        raise WorkflowError("MIDI 旋律音轨已失效，请重新选择音轨。")
    original = source.instruments[track_index]
    start = float(segment["start"])
    end = float(segment["end"])
    clip = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    instrument = pretty_midi.Instrument(program=original.program, is_drum=False, name=original.name)
    for note in original.notes:
        clipped_start = max(start, float(note.start))
        clipped_end = min(end, float(note.end))
        if clipped_end > clipped_start:
            instrument.notes.append(pretty_midi.Note(
                velocity=int(note.velocity),
                pitch=min(127, max(0, int(note.pitch) + int(pitch_shift))),
                start=clipped_start - start,
                end=clipped_end - start,
            ))
    if not instrument.notes:
        raise WorkflowError(f"第 {index + 1} 段没有旋律音符，请改选音轨。")
    clip.instruments.append(instrument)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clip.write(str(output_path))


def generate_midi_segment(
    project: dict[str, Any],
    index: int,
    lyrics: str,
    steps: int,
    pitch_shift: int,
    output_dir: Path,
    progress: Callable[[float, str], None],
) -> Path:
    if not lyrics.strip():
        raise WorkflowError(f"第 {index + 1} 段歌词不能为空。")
    if steps < 8 or steps > 64 or steps % 4:
        raise WorkflowError("推理步数必须为 8 到 64 之间、且为 4 的倍数。")
    segment = project["segments"][index]
    duration = float(segment["end"] - segment["start"])
    if duration <= 0 or duration > 44.5:
        raise WorkflowError(f"第 {index + 1} 段必须在 0 到 44.5 秒之间。")

    output_dir.mkdir(parents=True, exist_ok=True)
    midi_path = output_dir / f"melody_{index + 1:03d}.mid"
    _write_midi_section(project, index, midi_path, pitch_shift=pitch_shift)

    # Plus accepts a melody audio guide rather than a MIDI path. Render the
    # selected, pitch-shifted track as a clean sine guide; no accompaniment is
    # introduced, and note starts/durations remain those of the original MIDI.
    import pretty_midi

    midi_clip = pretty_midi.PrettyMIDI(str(midi_path))
    guide = midi_clip.synthesize(fs=MIDI_SAMPLE_RATE, wave=np.sin).astype(np.float32)
    target_length = max(1, round(duration * MIDI_SAMPLE_RATE))
    if len(guide) > target_length:
        guide = guide[:target_length]
    elif len(guide) < target_length:
        guide = np.pad(guide, (0, target_length - len(guide)))
    guide_peak = float(np.max(np.abs(guide))) if len(guide) else 0.0
    if guide_peak > 1e-6:
        guide = guide * (0.7 / guide_peak)
    guide_path = output_dir / f"melody_guide_{index + 1:03d}.wav"
    sf.write(
        guide_path,
        np.repeat(guide[:, None], 2, axis=1),
        MIDI_SAMPLE_RATE,
        subtype="PCM_16",
    )

    progress(0.12, f"第 {index + 1} 段：Plus 使用 MIDI 音高引导生成（{steps} 步）")
    result = _run_plus_inference(
        project,
        guide_path,
        lyrics,
        steps,
        seed=20_250 + index,
    )
    if len(result) > target_length:
        result = result[:target_length]
    elif len(result) < target_length:
        result = np.pad(result, ((0, target_length - len(result)), (0, 0)))
    fade = min(round(0.012 * MIDI_SAMPLE_RATE), target_length // 4)
    if fade > 1:
        result[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
        result[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]

    output_path = output_dir / f"section_{index + 1:03d}.npy"
    temporary = output_path.with_suffix(".npy.tmp")
    with temporary.open("wb") as stream:
        np.save(stream, result, allow_pickle=False)
    temporary.replace(output_path)
    return output_path


def generate_midi_line_preview(
    project: dict[str, Any],
    start: float,
    end: float,
    lyrics: str,
    steps: int,
    pitch_shift: int,
    output_dir: Path,
    progress: Callable[[float, str], None],
) -> Path:
    """Generate one LRC-sized vocal preview against the selected MIDI notes."""
    if not 0 <= start < end <= float(project["duration"]):
        raise WorkflowError("歌词时间范围必须落在 MIDI 时长内。")
    if end - start > 44.5:
        raise WorkflowError("单句 MIDI 试听不能超过 44.5 秒，请在 LRC 中拆分或缩短这句。")
    line_project = {
        **project,
        "segments": [{"start": float(start), "end": float(end), "has_notes": True}],
    }
    waveform_path = generate_midi_segment(
        line_project, 0, lyrics, steps, pitch_shift, output_dir, progress,
    )
    waveform = np.load(waveform_path, allow_pickle=False)
    preview_path = output_dir / "preview.wav"
    sf.write(preview_path, np.clip(waveform, -1.0, 1.0), MIDI_SAMPLE_RATE, subtype="PCM_16")
    return preview_path


def finalize_midi_lyric_song(
    project: dict[str, Any],
    lines: list[dict[str, Any]],
    run_dir: Path,
) -> Path:
    """Place sentence-level MIDI vocal renders back on the original timeline."""
    target_length = max(1, round(float(project["duration"]) * MIDI_SAMPLE_RATE))
    song = np.zeros((target_length, 2), dtype=np.float32)
    for index, line in enumerate(lines):
        path = run_dir / f"section_{index + 1:03d}.npy"
        if not path.is_file():
            continue
        start = max(0, round(float(line["start"]) * MIDI_SAMPLE_RATE))
        end = min(target_length, round(float(line["end"]) * MIDI_SAMPLE_RATE))
        if end <= start:
            continue
        chunk = np.load(path, allow_pickle=False).astype(np.float32, copy=False)
        expected = end - start
        if len(chunk) > expected:
            chunk = chunk[:expected]
        elif len(chunk) < expected:
            chunk = np.pad(chunk, ((0, expected - len(chunk)), (0, 0)))
        song[start:end] += chunk
    output_path = run_dir / "yingmusic_midi_song.wav"
    sf.write(output_path, np.clip(song, -1.0, 1.0), MIDI_SAMPLE_RATE, subtype="PCM_16")
    return output_path


def finalize_midi_song(project: dict[str, Any], run_dir: Path) -> Path:
    chunks: list[np.ndarray] = []
    for index, segment in enumerate(project["segments"]):
        length = max(1, round((segment["end"] - segment["start"]) * MIDI_SAMPLE_RATE))
        if segment.get("has_notes"):
            chunk = np.load(run_dir / f"section_{index + 1:03d}.npy", allow_pickle=False)
        else:
            chunk = np.zeros((length, 2), dtype=np.float32)
        if len(chunk) > length:
            chunk = chunk[:length]
        elif len(chunk) < length:
            chunk = np.pad(chunk, ((0, length - len(chunk)), (0, 0)))
        chunks.append(chunk.astype(np.float32, copy=False))
    if not chunks:
        raise WorkflowError("没有可拼接的 MIDI 片段。")
    audio = np.concatenate(chunks, axis=0)
    output_path = run_dir / "yingmusic_midi_song.wav"
    sf.write(output_path, np.clip(audio, -1.0, 1.0), MIDI_SAMPLE_RATE, subtype="PCM_16")
    return output_path


def _generate_vocal_segment(
    project: dict[str, Any],
    index: int,
    lyrics: str,
    steps: int,
    progress: Callable[[float, str], None] | None = None,
    *,
    start_override: float | None = None,
    end_override: float | None = None,
    seed_offset: int = 0,
    melody_path_override: Path | None = None,
    pitch_shift: int = 0,
) -> np.ndarray:
    if not lyrics.strip():
        raise WorkflowError("修改后的歌词不能为空。")
    if index < 0 or index >= len(project["segments"]):
        raise WorkflowError("请选择有效的歌词行。")
    if steps < 8 or steps > 64 or steps % 4:
        raise WorkflowError("推理步数必须为 8 到 64 之间、且为 4 的倍数。")
    if pitch_shift < -12 or pitch_shift > 12:
        raise WorkflowError("旋律移调必须在 -12 到 +12 个半音之间。")

    segment = project["segments"][index]
    segment_start = float(segment["start"] if start_override is None else start_override)
    segment_end = float(segment["end"] if end_override is None else end_override)
    start_sample = int(segment_start * DEMUCS_SAMPLE_RATE)
    end_sample = int(segment_end * DEMUCS_SAMPLE_RATE)
    target_length = end_sample - start_sample
    if target_length <= 0:
        raise WorkflowError(f"第 {index + 1} 句的时间范围无效，请检查 LRC 时间戳。")
    if target_length / DEMUCS_SAMPLE_RATE > 44.5:
        raise WorkflowError(f"第 {index + 1} 句超过 45 秒；请在 LRC 中拆分这句。")

    melody_path = melody_path_override or Path(project["session_dir"]) / f"melody_line_{index + 1:04d}.wav"
    melody_path.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(project["vocals_path"]) as vocals_file:
        vocals_file.seek(start_sample)
        melody_segment = vocals_file.read(target_length, dtype="float32", always_2d=True)
    if melody_segment.shape[0] < int(0.3 * DEMUCS_SAMPLE_RATE):
        raise WorkflowError(f"第 {index + 1} 句太短，请检查 LRC 时间戳。")
    if pitch_shift:
        guide = torch.from_numpy(melody_segment.T.copy())
        guide = torchaudio.functional.pitch_shift(
            guide,
            sample_rate=DEMUCS_SAMPLE_RATE,
            n_steps=int(pitch_shift),
        )
        if guide.shape[-1] < target_length:
            guide = torch.nn.functional.pad(guide, (0, target_length - guide.shape[-1]))
        melody_segment = guide[..., :target_length].numpy().T.astype(np.float32, copy=False)
    sf.write(melody_path, melody_segment, DEMUCS_SAMPLE_RATE, subtype="PCM_16")

    if progress:
        progress(0.10, f"第 {index + 1} 句：准备 Plus 旋律和音色参考")
        progress(0.15, f"第 {index + 1} 句：Plus GPU 歌声扩散采样（{steps} 步）")
    result = _run_plus_inference(
        project,
        melody_path,
        lyrics,
        steps,
        seed=2025 + index + seed_offset,
    )
    if len(result) > target_length:
        result = result[:target_length]
    elif len(result) < target_length:
        result = np.pad(result, ((0, target_length - len(result)), (0, 0)))
    return result.astype(np.float32, copy=False)


def generate_aligned_preview(
    project: dict[str, Any],
    index: int,
    chunks: list[dict[str, Any]],
    steps: int,
    progress: Callable[[float, str], None],
    pitch_shift: int = 0,
) -> Path:
    """Generate a preview from user-aligned lyric chunks within one source line."""
    if index < 0 or index >= len(project["segments"]):
        raise WorkflowError("请选择有效的歌词行。")
    if not chunks:
        raise WorkflowError("至少需要一个歌词音节分组。")
    if steps < 8 or steps > 64 or steps % 4:
        raise WorkflowError("推理步数必须为 8 到 64 之间、且为 4 的倍数。")

    segment = project["segments"][index]
    segment_start = float(segment["start"])
    segment_end = float(segment["end"])
    target_length = int(round((segment_end - segment_start) * DEMUCS_SAMPLE_RATE))
    voice = np.zeros((target_length, 2), dtype=np.float32)
    work_dir = Path(project["session_dir"]) / "previews" / f"aligned_work_line_{index + 1:04d}"
    work_dir.mkdir(parents=True, exist_ok=True)

    for chunk_index, chunk in enumerate(chunks):
        start = float(chunk["start"])
        end = float(chunk["end"])
        lyrics = str(chunk["lyrics"]).strip()
        if not lyrics or start < segment_start - 0.02 or end > segment_end + 0.02 or end <= start:
            raise WorkflowError(f"第 {chunk_index + 1} 个音节分组的歌词或时间范围无效。")
        if end - start < 0.3:
            raise WorkflowError(f"第 {chunk_index + 1} 个音节分组短于 0.3 秒。")

        chunk_start_sample = int(round(start * DEMUCS_SAMPLE_RATE))
        chunk_end_sample = int(round(end * DEMUCS_SAMPLE_RATE))
        output_offset = chunk_start_sample - int(round(segment_start * DEMUCS_SAMPLE_RATE))
        if output_offset < 0 or output_offset >= target_length:
            raise WorkflowError(f"第 {chunk_index + 1} 个音节分组超出句子范围。")

        def on_progress(fraction: float, label: str, *, current: int = chunk_index) -> None:
            progress((current + max(0.0, min(1.0, fraction))) / len(chunks), f"分组 {current + 1}/{len(chunks)} · {label}")

        generated = _generate_vocal_segment(
            project,
            index,
            lyrics,
            steps,
            on_progress,
            start_override=start,
            end_override=end,
            seed_offset=chunk_index * 997,
            melody_path_override=work_dir / f"chunk_{chunk_index + 1:02d}.wav",
            pitch_shift=pitch_shift,
        )
        copy_length = min(len(generated), target_length - output_offset)
        voice[output_offset : output_offset + copy_length] += generated[:copy_length]

    with sf.SoundFile(project["accompaniment_path"]) as music_file:
        accompaniment_start = int(round(segment_start * music_file.samplerate))
        music_file.seek(accompaniment_start)
        accompaniment = music_file.read(target_length, dtype="float32", always_2d=True)
    preview = np.clip(accompaniment[: len(voice)] + voice[: len(accompaniment)], -1.0, 1.0)
    output = Path(project["session_dir"]) / "previews" / f"generated_line_{index + 1:04d}_aligned.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, preview, DEMUCS_SAMPLE_RATE, subtype="PCM_16")
    return output


def write_original_preview(project: dict[str, Any], index: int) -> Path:
    if index < 0 or index >= len(project["segments"]):
        raise WorkflowError("所选歌词行不存在。")
    segment = project["segments"][index]
    original_path = project.get("original_path")
    if not original_path:
        raise WorkflowError("原曲音频文件不存在。")
    with sf.SoundFile(original_path) as original_file:
        start = int(segment["start"] * original_file.samplerate)
        end = int(segment["end"] * original_file.samplerate)
        original_file.seek(start)
        preview = original_file.read(end - start, dtype="float32", always_2d=True)
    output = Path(project["session_dir"]) / "previews" / f"original_line_{index + 1:04d}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, preview, DEMUCS_SAMPLE_RATE, subtype="PCM_16")
    return output


def generate_preview(
    project: dict[str, Any],
    index: int,
    lyrics: str,
    steps: int,
    progress: Callable[[float, str], None],
    pitch_shift: int = 0,
) -> Path:
    generated = _generate_vocal_segment(
        project, index, lyrics, steps, progress, pitch_shift=pitch_shift
    )
    segment = project["segments"][index]
    start = int(segment["start"] * DEMUCS_SAMPLE_RATE)
    end = int(segment["end"] * DEMUCS_SAMPLE_RATE)
    with sf.SoundFile(project["accompaniment_path"]) as music_file:
        music_file.seek(start)
        accompaniment = music_file.read(end - start, dtype="float32", always_2d=True)
    preview = np.clip(accompaniment[: len(generated)] + generated[: len(accompaniment)], -1.0, 1.0)
    output = Path(project["session_dir"]) / "previews" / f"generated_line_{index + 1:04d}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, preview, DEMUCS_SAMPLE_RATE, subtype="PCM_16")
    return output


def generate_line_chunk(
    project: dict[str, Any],
    index: int,
    lyrics: str,
    steps: int,
    output_dir: Path,
    progress: Callable[[float, str], None],
    pitch_shift: int = 0,
) -> Path:
    generated = _generate_vocal_segment(
        project, index, lyrics, steps, progress, pitch_shift=pitch_shift
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"line_{index + 1:04d}.npy"
    temporary = output.with_suffix(".npy.tmp")
    with temporary.open("wb") as stream:
        np.save(stream, generated.astype(np.float32, copy=False), allow_pickle=False)
    temporary.replace(output)
    return output


def finalize_song(project: dict[str, Any], run_dir: Path, changed: list[dict[str, Any]]) -> Path:
    with sf.SoundFile(project["vocals_path"]) as vocals_file:
        vocals = vocals_file.read(dtype="float32", always_2d=True)
    with sf.SoundFile(project["accompaniment_path"]) as accompaniment_file:
        accompaniment = accompaniment_file.read(dtype="float32", always_2d=True)

    for item in changed:
        index = int(item["index"])
        segment = project["segments"][index]
        generated = np.load(run_dir / f"line_{index + 1:04d}.npy", allow_pickle=False)
        start = int(segment["start"] * DEMUCS_SAMPLE_RATE)
        end = min(int(segment["end"] * DEMUCS_SAMPLE_RATE), len(vocals))
        length = end - start
        new_voice = generated[:length]
        edge = min(int(0.05 * DEMUCS_SAMPLE_RATE), length // 2)
        weight = np.ones(length, dtype=np.float32)
        if edge:
            weight[:edge] = np.linspace(0.0, 1.0, edge, endpoint=False, dtype=np.float32)
            weight[-edge:] = np.linspace(1.0, 0.0, edge, endpoint=True, dtype=np.float32)
        old_voice = vocals[start:end]
        vocals[start:end] = old_voice * (1.0 - weight[:, None]) + new_voice[:length] * weight[:, None]

    final_mix = np.clip(vocals + accompaniment[: len(vocals)], -1.0, 1.0)
    output_path = run_dir / "yingmusic_edited_song.wav"
    sf.write(output_path, final_mix, DEMUCS_SAMPLE_RATE, subtype="PCM_16")
    return output_path

