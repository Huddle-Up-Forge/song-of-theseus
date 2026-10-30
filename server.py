"""FastAPI service and persistent, single-GPU task scheduler for Song of Theseus."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import importlib.util
import os
import shutil
import sqlite3
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import workflow


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = workflow.OUTPUT_ROOT
DATABASE_PATH = ROOT / "outputs" / "yingstudio.sqlite3"
FRONTEND_DIST = ROOT / "frontend" / "dist"
SINGER_LIBRARY_ROOT = ROOT / "outputs" / "singers"
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
MAX_SINGER_UPLOAD_BYTES = 100 * 1024 * 1024
SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}
SUPPORTED_MIDI_EXTENSIONS = {".mid", ".midi"}
TERMINAL_STATES = {"succeeded", "failed", "canceled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _init_db() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                session_dir TEXT NOT NULL,
                status TEXT NOT NULL,
                duration REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                parent_id TEXT,
                kind TEXT NOT NULL,
                queueable INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 10,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                label TEXT NOT NULL DEFAULT '',
                error TEXT,
                result_json TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );
            CREATE INDEX IF NOT EXISTS jobs_queue_idx
                ON jobs(queueable, status, priority, created_at);
            CREATE INDEX IF NOT EXISTS jobs_project_idx
                ON jobs(project_id, created_at DESC);
            """
        )


def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"] or "{}")
    job = {
        "id": row["id"],
        "project_id": row["project_id"],
        "parent_id": row["parent_id"],
        "kind": row["kind"],
        "status": row["status"],
        "progress": float(row["progress"]),
        "label": row["label"],
        "error": row["error"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "cancel_requested": bool(row["cancel_requested"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if row["kind"] in {"preview", "midi_preview"}:
        job["line_index"] = payload.get("index")
    return job


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        job = _job_dict(row)
        if job["kind"] == "generate_song":
            counts = connection.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS done "
                "FROM jobs WHERE parent_id=? AND kind='generate_line'",
                (job_id,),
            ).fetchone()
            job["line_count"] = int(counts["total"] or 0)
            job["lines_done"] = int(counts["done"] or 0)
        return job


def _project_row(project_id: str) -> sqlite3.Row | None:
    with _connect() as connection:
        return connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()


def _project_dir(project_id: str) -> Path:
    row = _project_row(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="找不到这个项目。")
    directory = Path(row["session_dir"]).resolve()
    root = OUTPUT_ROOT.resolve()
    if not directory.is_relative_to(root):
        raise HTTPException(status_code=400, detail="项目路径无效。")
    return directory


def _singer_dir(singer_id: str) -> Path:
    directory = (SINGER_LIBRARY_ROOT / singer_id).resolve()
    if not directory.is_relative_to(SINGER_LIBRARY_ROOT.resolve()):
        raise HTTPException(status_code=400, detail="歌手资料路径无效。")
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="找不到这个歌手资料。")
    return directory


def _load_singer(singer_id: str) -> dict[str, Any]:
    directory = _singer_dir(singer_id)
    metadata_path = directory / "singer.json"
    try:
        profile = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="歌手资料读取失败。") from exc
    audio_path = (directory / profile.get("audio_file", "")).resolve()
    if not audio_path.is_relative_to(directory) or not audio_path.is_file():
        raise HTTPException(status_code=500, detail="歌手参考音频不存在。")
    profile["audio_path"] = str(audio_path)
    return profile


def _public_singer(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": profile["id"],
        "name": profile["name"],
        "reference_text": profile["reference_text"],
        "duration": profile["duration"],
        "created_at": profile["created_at"],
        "updated_at": profile["updated_at"],
        "audio_url": f"/api/singers/{profile['id']}/audio",
    }


def _list_singers() -> list[dict[str, Any]]:
    if not SINGER_LIBRARY_ROOT.is_dir():
        return []
    profiles: list[dict[str, Any]] = []
    for metadata_path in SINGER_LIBRARY_ROOT.glob("*/singer.json"):
        try:
            profile = _load_singer(metadata_path.parent.name)
            profiles.append(_public_singer(profile))
        except HTTPException:
            continue
    return sorted(profiles, key=lambda profile: (profile["name"].casefold(), profile["id"]))


async def _save_uploaded_singer_audio(audio: UploadFile, destination: Path) -> float:
    suffix = Path(audio.filename or "reference.wav").suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=415, detail="参考人声仅支持 WAV、MP3、FLAC 或 OGG。")
    try:
        data = await audio.read(MAX_SINGER_UPLOAD_BYTES + 1)
    finally:
        await audio.close()
    if not data:
        raise HTTPException(status_code=422, detail="请选择参考人声文件。")
    if len(data) > MAX_SINGER_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="参考人声文件不能超过 100 MB。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        duration = float(sf.info(str(destination)).duration)
    except (OSError, RuntimeError, sf.LibsndfileError) as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail="无法解码这段参考人声，请尝试 WAV、MP3、FLAC 或 OGG。") from exc
    if not 3.0 <= duration <= 20.0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="参考人声建议 5–7 秒，当前只接受 3–20 秒的片段。")
    return duration


def _load_project(project_id: str) -> dict[str, Any]:
    directory = _project_dir(project_id)
    metadata_path = directory / "project.json"
    if not metadata_path.is_file():
        raise HTTPException(status_code=409, detail="项目仍在初始化或初始化失败。")
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="项目元数据损坏。") from exc


def _save_project(project: dict[str, Any]) -> None:
    workflow._write_project(project)


def _create_job(
    project_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    parent_id: str | None = None,
    queueable: bool = True,
    priority: int = 10,
    label: str,
    job_id: str | None = None,
) -> str:
    identifier = job_id or uuid.uuid4().hex
    now = _now()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO jobs(id,project_id,parent_id,kind,queueable,priority,payload_json,status,progress,label,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                project_id,
                parent_id,
                kind,
                int(queueable),
                priority,
                json.dumps(payload, ensure_ascii=False),
                "queued",
                0.0,
                label,
                now,
                now,
            ),
        )
    _SCHEDULER.wake()
    return identifier


def _update_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    label: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    cancel_requested: bool | None = None,
) -> None:
    changes: list[str] = ["updated_at=?"]
    values: list[Any] = [_now()]
    for column, value in (
        ("status", status),
        ("progress", progress),
        ("label", label),
        ("error", error),
        ("result_json", json.dumps(result, ensure_ascii=False) if result is not None else None),
        ("cancel_requested", int(cancel_requested) if cancel_requested is not None else None),
    ):
        if value is not None:
            changes.append(f"{column}=?")
            values.append(value)
    values.append(job_id)
    with _connect() as connection:
        connection.execute(f"UPDATE jobs SET {','.join(changes)} WHERE id=?", values)


def _update_project_status(project_id: str, status: str) -> None:
    with _connect() as connection:
        connection.execute("UPDATE projects SET status=? WHERE id=?", (status, project_id))


class TaskScheduler:
    """Persistent FIFO-with-priority queue; only one job can touch the GPU."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False

    def start(self) -> None:
        self._recover_interrupted_work()
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="yingstudio-gpu-queue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _recover_interrupted_work(self) -> None:
        with _connect() as connection:
            running = connection.execute(
                "SELECT id FROM jobs WHERE queueable=1 AND status='running'"
            ).fetchall()
            for row in running:
                connection.execute(
                    "UPDATE jobs SET status='queued', label='服务重启后自动续跑', updated_at=? WHERE id=?",
                    (_now(), row["id"]),
                )
            # A parent generation job is allowed to remain running while its
            # line-level work items are interleaved with previews.

    def _claim(self) -> dict[str, Any] | None:
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE queueable=1 AND status='queued' "
                "ORDER BY priority ASC, created_at ASC, rowid ASC LIMIT 1"
            ).fetchone()
            if not row:
                connection.commit()
                return None
            connection.execute(
                "UPDATE jobs SET status='running',label=?,updated_at=? WHERE id=?",
                ("工作线程已领取任务", _now(), row["id"]),
            )
            connection.commit()
            task = dict(row)
            task["status"] = "running"
            task["payload"] = json.loads(task.pop("payload_json"))
            return task

    def _run(self) -> None:
        print("Song of Theseus scheduler started (one GPU worker).", flush=True)
        while True:
            if self._stopping:
                return
            task = self._claim()
            if task is None:
                with self._condition:
                    self._condition.wait(timeout=1.0)
                continue
            try:
                self._dispatch(task)
            except Exception as exc:
                traceback.print_exc()
                message = str(exc).strip() or type(exc).__name__
                _update_job(task["id"], status="failed", progress=1.0, label="任务失败", error=message)
                parent_id = task.get("parent_id")
                if parent_id:
                    self._fail_song(parent_id, message)
                elif task["kind"] == "prepare":
                    _update_project_status(task["project_id"], "failed")

    def _dispatch(self, task: dict[str, Any]) -> None:
        handlers = {
            "prepare": self._prepare,
            "midi_generate_song": self._generate_midi_song,
            "midi_preview": self._preview_midi_line,
            "preview": self._preview,
            "aligned_preview": self._preview_aligned,
            "generate_line": self._generate_line,
            "finalize": self._finalize,
        }
        handler = handlers.get(task["kind"])
        if not handler:
            raise RuntimeError(f"Unknown queued task type: {task['kind']}")
        handler(task)

    def _progress(self, task_id: str, fraction: float, label: str) -> None:
        _update_job(task_id, progress=max(0.0, min(0.99, fraction)), label=label)

    def _prepare(self, task: dict[str, Any]) -> None:
        project_id = task["project_id"]
        directory = _project_dir(project_id)
        payload = task["payload"]
        project = workflow.prepare_session(
            directory,
            project_id,
            Path(payload["original_path"]),
            payload["lrc_text"],
            lambda fraction, label: self._progress(task["id"], fraction, label),
            singer_reference_path=(
                Path(payload["singer_profile"]["audio_path"])
                if payload.get("singer_profile")
                else None
            ),
            singer_reference_text=(
                payload["singer_profile"]["reference_text"]
                if payload.get("singer_profile")
                else None
            ),
            singer_profile=payload.get("singer_profile"),
        )
        project["created_at"] = payload.get("created_at")
        _save_project(project)
        _update_project_status(project_id, "ready")
        _update_job(
            task["id"],
            status="succeeded",
            progress=1.0,
            label=(
                f"人声分离与歌词识别完成 · {len(project['segments'])} 句"
                if project.get("lyrics_source", "").startswith("whisper-")
                else f"人声分离完成 · {len(project['segments'])} 句"
            ),
            result={"line_count": len(project["segments"])},
        )

    def _generate_midi_song(self, task: dict[str, Any]) -> None:
        project_id = task["project_id"]
        project = _load_project(project_id)
        payload = task["payload"]
        if payload.get("lines"):
            self._generate_midi_lyric_song(task, project, payload)
            return
        run_dir = _project_dir(project_id) / "generations" / payload["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        lyrics = payload["lyrics"]
        total = len(project["segments"])
        generated_count = 0

        for index, (segment, text) in enumerate(zip(project["segments"], lyrics)):
            current = _get_job(task["id"])
            if current and current.get("cancel_requested"):
                _update_job(task["id"], status="canceled", progress=current["progress"], label="已在当前 MIDI 片段结束后停止")
                return
            if not segment.get("has_notes"):
                self._progress(task["id"], (index + 1) / max(1, total) * 0.9, f"第 {index + 1}/{total} 段为休止，保留静音")
                continue

            def on_progress(fraction: float, label: str, *, section_index: int = index) -> None:
                overall = (section_index + max(0.0, min(0.9, fraction))) / max(1, total) * 0.9
                self._progress(task["id"], overall, label)

            workflow.generate_midi_segment(
                project,
                index,
                text,
                int(payload["steps"]),
                int(payload["pitch_shift"]),
                run_dir,
                on_progress,
            )
            generated_count += 1
            self._progress(task["id"], (index + 1) / max(1, total) * 0.9, f"已完成 MIDI 片段 {index + 1}/{total}")

        current = _get_job(task["id"])
        if current and current.get("cancel_requested"):
            _update_job(task["id"], status="canceled", progress=current["progress"], label="已取消；尚未拼接整曲")
            return

        self._progress(task["id"], 0.94, "正在按 MIDI 时间顺序拼接歌声")
        final_path = workflow.finalize_midi_song(project, run_dir)
        project["last_generation"] = {
            "path": str(final_path),
            "steps": int(payload["steps"]),
            "pitch_shift": int(payload["pitch_shift"]),
            "changed_lines": generated_count,
            "created_at": _now(),
        }
        for index, text in enumerate(lyrics):
            project["segments"][index]["edited"] = text
        _save_project(project)
        _update_job(
            task["id"],
            status="succeeded",
            progress=1.0,
            label=f"MIDI 歌曲拼接完成 · {generated_count} 段 · {payload['steps']} 步",
            result={"asset_url": f"/api/projects/{project_id}/assets/final"},
        )

    def _generate_midi_lyric_song(
        self,
        task: dict[str, Any],
        project: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        project_id = task["project_id"]
        lines = payload["lines"]
        line_project = {**project, "segments": lines}
        run_dir = _project_dir(project_id) / "generations" / payload["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        total = len(lines)
        generated_count = 0

        for index, line in enumerate(lines):
            current = _get_job(task["id"])
            if current and current.get("cancel_requested"):
                _update_job(task["id"], status="canceled", progress=current["progress"], label="已在当前歌词句结束后停止")
                return
            if not line.get("has_notes", True):
                self._progress(task["id"], (index + 1) / max(1, total) * 0.9, f"第 {index + 1}/{total} 句没有 MIDI 音符，保留静音")
                continue

            def on_progress(fraction: float, label: str, *, line_index: int = index) -> None:
                overall = (line_index + max(0.0, min(0.9, fraction))) / max(1, total) * 0.9
                self._progress(task["id"], overall, f"第 {line_index + 1}/{total} 句 · {label}")

            workflow.generate_midi_segment(
                line_project,
                index,
                line["lyrics"],
                int(payload["steps"]),
                int(payload["pitch_shift"]),
                run_dir,
                on_progress,
            )
            generated_count += 1
            self._progress(task["id"], (index + 1) / max(1, total) * 0.9, f"已完成歌词句 {index + 1}/{total}")

        current = _get_job(task["id"])
        if current and current.get("cancel_requested"):
            _update_job(task["id"], status="canceled", progress=current["progress"], label="已取消；尚未拼接整曲")
            return

        self._progress(task["id"], 0.94, "正在按 MIDI 音符起点拼接歌声")
        final_path = workflow.finalize_midi_lyric_song(project, lines, run_dir)
        project["last_generation"] = {
            "path": str(final_path),
            "steps": int(payload["steps"]),
            "pitch_shift": int(payload["pitch_shift"]),
            "changed_lines": generated_count,
            "created_at": _now(),
        }
        project["midi_lyrics"] = [
            {key: line[key] for key in ("index", "start", "end", "lyrics")}
            for line in lines
        ]
        _save_project(project)
        _update_job(
            task["id"],
            status="succeeded",
            progress=1.0,
            label=f"LRC 逐句 MIDI 歌声拼接完成 · {generated_count} 句 · {payload['steps']} 步",
            result={"asset_url": f"/api/projects/{project_id}/assets/final"},
        )

    def _preview_midi_line(self, task: dict[str, Any]) -> None:
        payload = task["payload"]
        project = _load_project(task["project_id"])
        session_dir = _project_dir(task["project_id"])
        preview_id = payload["preview_id"]
        staging_dir = session_dir / "previews" / f"midi_work_{preview_id}"
        staging_dir.mkdir(parents=True, exist_ok=True)
        path = workflow.generate_midi_line_preview(
            project,
            float(payload["start"]),
            float(payload["end"]),
            payload["lyrics"],
            int(payload["steps"]),
            int(payload["pitch_shift"]),
            staging_dir,
            lambda fraction, label: self._progress(task["id"], fraction, label),
        )
        preview_name = f"midi_line_{preview_id}.wav"
        preview_path = session_dir / "previews" / preview_name
        shutil.move(str(path), str(preview_path))
        shutil.rmtree(staging_dir, ignore_errors=True)
        project.setdefault("midi_line_previews", {})[preview_id] = {
            "path": f"previews/{preview_name}",
            "lyrics": payload["lyrics"],
            "start": float(payload["start"]),
            "end": float(payload["end"]),
            "line_index": int(payload["index"]),
            "created_at": _now(),
        }
        _save_project(project)
        _update_job(
            task["id"],
            status="succeeded",
            progress=1.0,
            label=f"第 {int(payload['index']) + 1} 句重唱试听已就绪",
            result={
                "line_index": int(payload["index"]),
                "asset_url": f"/api/midi-projects/{task['project_id']}/previews/{preview_id}",
            },
        )

    def _preview(self, task: dict[str, Any]) -> None:
        payload = task["payload"]
        project = _load_project(task["project_id"])
        path = workflow.generate_preview(
            project,
            int(payload["index"]),
            payload["lyrics"],
            int(payload["steps"]),
            lambda fraction, label: self._progress(task["id"], fraction, label),
            int(payload.get("pitch_shift", 0)),
        )
        project.setdefault("latest_previews", {})[str(payload["index"])] = {
            "path": path.name,
            "lyrics": payload["lyrics"],
            "created_at": _now(),
        }
        _save_project(project)
        _update_job(
            task["id"],
            status="succeeded",
            progress=1.0,
            label="重唱试听已就绪",
            result={"line_index": int(payload["index"]), "asset_url": f"/api/projects/{task['project_id']}/lines/{payload['index']}/preview/generated"},
        )

    def _preview_aligned(self, task: dict[str, Any]) -> None:
        payload = task["payload"]
        project = _load_project(task["project_id"])
        path = workflow.generate_aligned_preview(
            project,
            int(payload["index"]),
            payload["chunks"],
            int(payload["steps"]),
            lambda fraction, label: self._progress(task["id"], fraction, label),
            int(payload.get("pitch_shift", 0)),
        )
        project.setdefault("latest_previews", {})[str(payload["index"])] = {
            "path": path.name,
            "lyrics": "".join(chunk["lyrics"].strip() for chunk in payload["chunks"]),
            "created_at": _now(),
        }
        _save_project(project)
        _update_job(
            task["id"],
            status="succeeded",
            progress=1.0,
            label="按歌词音节分组的试听已就绪",
            result={
                "line_index": int(payload["index"]),
                "asset_url": f"/api/projects/{task['project_id']}/lines/{payload['index']}/preview/generated",
            },
        )

    def _generate_line(self, task: dict[str, Any]) -> None:
        payload = task["payload"]
        parent_id = task["parent_id"]
        if not parent_id:
            raise RuntimeError("Song line task has no parent job.")
        parent = _get_job(parent_id)
        if not parent:
            raise RuntimeError("Song generation job disappeared.")
        if parent.get("cancel_requested") or parent.get("status") == "canceled":
            self._cancel_song(parent_id, "已按请求停止")
            _update_job(task["id"], status="canceled", label="已取消")
            return

        project = _load_project(task["project_id"])
        index = int(payload["index"])
        done = parent.get("lines_done", 0)
        total = max(1, parent.get("line_count", 1))
        _update_job(
            parent_id,
            status="running",
            label=f"正在重唱第 {index + 1} 句 · {done}/{total}",
        )

        def on_progress(fraction: float, label: str) -> None:
            self._progress(task["id"], fraction, label)
            parent_fraction = (done + max(0.0, min(0.95, fraction))) / total * 0.92
            _update_job(parent_id, status="running", progress=parent_fraction, label=label)

        run_dir = _project_dir(task["project_id"]) / "generations" / payload["run_id"]
        path = workflow.generate_line_chunk(
            project,
            index,
            payload["lyrics"],
            int(payload["steps"]),
            run_dir,
            on_progress,
            int(payload.get("pitch_shift", 0)),
        )
        _update_job(
            task["id"],
            status="succeeded",
            progress=1.0,
            label=f"第 {index + 1} 句已完成",
            result={"path": str(path)},
        )

        parent = _get_job(parent_id)
        if not parent:
            return
        done = parent["lines_done"]
        total = parent["line_count"]
        if parent.get("cancel_requested") or parent.get("status") == "canceled":
            self._cancel_song(parent_id, "当前句完成，任务已停止")
            return
        _update_job(
            parent_id,
            status="running",
            progress=min(0.92, done / max(1, total) * 0.92),
            label=f"已重唱 {done}/{total} 句",
        )
        if done == total:
            _create_job(
                task["project_id"],
                "finalize",
                {"run_id": payload["run_id"]},
                parent_id=parent_id,
                priority=12,
                label="正在混音并拼接整曲",
            )

    def _finalize(self, task: dict[str, Any]) -> None:
        parent_id = task["parent_id"]
        if not parent_id:
            raise RuntimeError("Finalize task has no song generation job.")
        parent = _get_job(parent_id)
        if not parent:
            raise RuntimeError("Song generation job disappeared before final mix.")
        if parent.get("cancel_requested"):
            self._cancel_song(parent_id, "已按请求停止")
            _update_job(task["id"], status="canceled", label="已取消")
            return

        project = _load_project(task["project_id"])
        manifest_path = _project_dir(task["project_id"]) / "generations" / task["payload"]["run_id"] / "request.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _update_job(parent_id, status="running", progress=0.95, label="正在混合人声与原伴奏")
        run_dir = manifest_path.parent
        final_path = workflow.finalize_song(project, run_dir, manifest["changed"])
        project["last_generation"] = {
            "path": str(final_path),
            "steps": manifest["steps"],
            "pitch_shift": manifest.get("pitch_shift", 0),
            "changed_lines": len(manifest["changed"]),
            "created_at": _now(),
        }
        for item in manifest["changed"]:
            project["segments"][item["index"]]["edited"] = item["lyrics"]
        _save_project(project)
        result = {"asset_url": f"/api/projects/{task['project_id']}/assets/final"}
        _update_job(task["id"], status="succeeded", progress=1.0, label="拼接完成")
        _update_job(
            parent_id,
            status="succeeded",
            progress=1.0,
            label=f"整曲完成 · {len(manifest['changed'])} 句 · {manifest['steps']} 步",
            result=result,
        )

    def _fail_song(self, parent_id: str, message: str) -> None:
        with _connect() as connection:
            connection.execute(
                "UPDATE jobs SET status='canceled',label='前序步骤失败，已停止后续句子',updated_at=? "
                "WHERE parent_id=? AND queueable=1 AND status='queued'",
                (_now(), parent_id),
            )
        _update_job(
            parent_id,
            status="failed",
            progress=1.0,
            label="整曲任务失败",
            error=message,
        )

    def _cancel_song(self, parent_id: str, label: str) -> None:
        with _connect() as connection:
            connection.execute(
                "UPDATE jobs SET status='canceled',label='已取消',updated_at=? "
                "WHERE parent_id=? AND queueable=1 AND status='queued'",
                (_now(), parent_id),
            )
        _update_job(parent_id, status="canceled", label=label, progress=min(1.0, (_get_job(parent_id) or {}).get("progress", 0.0)))


_SCHEDULER = TaskScheduler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_db()
    _SCHEDULER.start()
    yield
    _SCHEDULER.stop()


app = FastAPI(
    title="Song of Theseus Local API",
    version="1.0.0",
    lifespan=lifespan,
)


class LineEdit(BaseModel):
    index: int = Field(ge=0)
    text: str


class PreviewRequest(BaseModel):
    index: int = Field(ge=0)
    lyrics: str = Field(min_length=1, max_length=500)
    steps: int = Field(default=32, ge=8, le=64, multiple_of=4)
    pitch_shift: int = Field(default=0, ge=-12, le=12)


class AlignedPreviewChunk(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    lyrics: str = Field(min_length=1, max_length=100)


class AlignedPreviewRequest(BaseModel):
    index: int = Field(ge=0)
    chunks: list[AlignedPreviewChunk] = Field(min_length=2, max_length=16)
    steps: int = Field(default=32, ge=8, le=64, multiple_of=4)
    pitch_shift: int = Field(default=0, ge=-12, le=12)


class GenerateRequest(BaseModel):
    lines: list[LineEdit]
    steps: int = Field(default=32, ge=8, le=64, multiple_of=4)
    pitch_shift: int = Field(default=0, ge=-12, le=12)


class TimingUpdate(BaseModel):
    starts: list[float] = Field(min_length=1, max_length=1000)
    ends: list[float] | None = Field(default=None, min_length=1, max_length=1000)


class SingerSelection(BaseModel):
    singer_id: str | None = None


class MidiTrackSelection(BaseModel):
    track_index: int = Field(ge=0)


class MidiLyricLineRequest(BaseModel):
    index: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    lyrics: str = Field(min_length=1, max_length=500)


class MidiLinePreviewRequest(MidiLyricLineRequest):
    steps: int = Field(default=32, ge=8, le=64, multiple_of=4)
    pitch_shift: int = Field(default=0, ge=-12, le=12)


class MidiGenerateRequest(BaseModel):
    lyrics: list[str] | None = Field(default=None, min_length=1, max_length=100)
    lines: list[MidiLyricLineRequest] | None = Field(default=None, min_length=1, max_length=1000)
    steps: int = Field(default=32, ge=8, le=64, multiple_of=4)
    pitch_shift: int = Field(default=0, ge=-12, le=12)


class LyricsDraftUpdate(BaseModel):
    lyrics: list[str] = Field(min_length=1, max_length=1000)


class MidiLyricsDraftLine(BaseModel):
    id: str = Field(max_length=100)
    original: str = Field(max_length=500)
    edited: str = Field(max_length=500)
    startOverride: float | None = Field(default=None, ge=0)


class MidiWorkspaceDraftUpdate(BaseModel):
    edited: list[str] = Field(min_length=0, max_length=1000)
    lrc_source: str = Field(default="", max_length=500_000)
    lrc_lines: list[MidiLyricsDraftLine] = Field(default_factory=list, max_length=1000)
    use_lrc_lyrics: bool = False


@app.get("/api/health")
def health() -> dict[str, Any]:
    queued = 0
    running = 0
    if DATABASE_PATH.exists():
        with _connect() as connection:
            queued = int(connection.execute("SELECT COUNT(*) FROM jobs WHERE queueable=1 AND status='queued'").fetchone()[0])
            running = int(connection.execute("SELECT COUNT(*) FROM jobs WHERE queueable=1 AND status='running'").fetchone()[0])
    free_memory = total_memory = None
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        free_memory = round(free / 1024**3, 2)
        total_memory = round(total / 1024**3, 2)
    return {
        "online": True,
        "singing_model": workflow.SINGING_MODEL_NAME,
        "device": workflow.DEVICE,
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        "gpu_free_gib": free_memory,
        "gpu_total_gib": total_memory,
        "queued_tasks": queued,
        "running_tasks": running,
        "scheduler_workers": 1,
        "transcription": importlib.util.find_spec("faster_whisper") is not None,
    }


@app.get("/api/workspaces")
def list_workspaces() -> list[dict[str, Any]]:
    """Return resumable rewrite and MIDI projects, newest activity first."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT p.*, COALESCE((SELECT MAX(j.updated_at) FROM jobs j WHERE j.project_id=p.id), p.created_at) AS updated_at "
            "FROM projects p ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()

    workspaces = []
    output_root = OUTPUT_ROOT.resolve()
    for row in rows:
        directory = Path(row["session_dir"]).resolve()
        if not directory.is_relative_to(output_root):
            continue
        try:
            project_path = directory / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        is_midi = project.get("workflow_type") == "midi"
        source_name = (
            project.get("midi_filename", "旋律 MIDI")
            if is_midi
            else Path(project.get("original_path", "原曲音频")).name
        )
        singer = project.get("singer_profile") or {}
        last_generation = project.get("last_generation")
        file_activity = datetime.fromtimestamp(
            project_path.stat().st_mtime, timezone.utc
        ).isoformat(timespec="seconds")
        updated_at = max(str(row["updated_at"]), file_activity)
        workspaces.append(
            {
                "id": row["id"],
                "workflow_type": "midi" if is_midi else "rewrite",
                "title": source_name or ("MIDI 填词" if is_midi else "逐句改唱"),
                "status": row["status"],
                "duration": float(row["duration"]),
                "line_count": len(project.get("segments", [])),
                "created_at": row["created_at"],
                "updated_at": updated_at,
                "singer_name": singer.get("name"),
                "last_generation": (
                    {
                        "steps": last_generation.get("steps"),
                        "changed_lines": last_generation.get("changed_lines"),
                    }
                    if last_generation
                    else None
                ),
            }
        )
    return workspaces


@app.get("/api/singers")
def list_singer_profiles() -> list[dict[str, Any]]:
    return _list_singers()


@app.get("/api/singers/{singer_id}/audio")
def get_singer_audio(singer_id: str) -> FileResponse:
    profile = _load_singer(singer_id)
    return FileResponse(
        profile["audio_path"],
        media_type=mimetypes.guess_type(profile["audio_path"])[0] or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/singers")
async def create_singer_profile(
    name: str = Form(...),
    reference_text: str = Form(...),
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    name = name.strip()
    reference_text = reference_text.strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=422, detail="歌手名称请填写 1–50 个字符。")
    if not reference_text or len(reference_text) > 500:
        raise HTTPException(status_code=422, detail="参考歌词请填写 1–500 个字符。")

    singer_id = uuid.uuid4().hex
    suffix = Path(audio.filename or "reference.wav").suffix.lower()
    profile_dir = SINGER_LIBRARY_ROOT / singer_id
    audio_path = profile_dir / f"reference{suffix}"
    duration = await _save_uploaded_singer_audio(audio, audio_path)
    created_at = _now()
    profile = {
        "id": singer_id,
        "name": name,
        "reference_text": reference_text,
        "audio_file": audio_path.name,
        "duration": duration,
        "created_at": created_at,
        "updated_at": created_at,
    }
    (profile_dir / "singer.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    profile["audio_path"] = str(audio_path.resolve())
    return _public_singer(profile)


@app.put("/api/singers/{singer_id}")
async def update_singer_profile(
    singer_id: str,
    name: str = Form(...),
    reference_text: str = Form(...),
    audio: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    profile = _load_singer(singer_id)
    name = name.strip()
    reference_text = reference_text.strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=422, detail="歌手名称请填写 1–50 个字符。")
    if not reference_text or len(reference_text) > 500:
        raise HTTPException(status_code=422, detail="参考歌词请填写 1–500 个字符。")

    previous_audio = Path(profile["audio_path"])
    if audio is not None and audio.filename:
        suffix = Path(audio.filename).suffix.lower()
        updated_name = f"reference_{uuid.uuid4().hex[:8]}{suffix}"
        updated_path = previous_audio.parent / updated_name
        profile["duration"] = await _save_uploaded_singer_audio(audio, updated_path)
        profile["audio_file"] = updated_name
        profile["audio_path"] = str(updated_path.resolve())

    profile["name"] = name
    profile["reference_text"] = reference_text
    profile["updated_at"] = _now()
    metadata_path = previous_audio.parent / "singer.json"
    metadata_path.write_text(
        json.dumps({key: value for key, value in profile.items() if key != "audio_path"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    current_audio = Path(profile["audio_path"])
    if current_audio != previous_audio:
        previous_audio.unlink(missing_ok=True)
    return _public_singer(profile)


@app.delete("/api/singers/{singer_id}")
def delete_singer_profile(singer_id: str) -> dict[str, str]:
    directory = _singer_dir(singer_id)
    shutil.rmtree(directory)
    return {"id": singer_id, "status": "deleted"}


def _public_midi_project(project: dict[str, Any]) -> dict[str, Any]:
    last_generation = project.get("last_generation")
    segments = project.get("segments", [])
    if any("notes" not in section for section in segments):
        try:
            note_sections = workflow.midi_sections_for_track(
                Path(project["midi_path"]), int(project.get("selected_track_index", 0)),
            )
            segments = [
                {**section, "notes": note_sections[index].get("notes", [])}
                for index, section in enumerate(segments)
            ]
        except (KeyError, IndexError, OSError, workflow.WorkflowError):
            segments = [{**section, "notes": []} for section in segments]
    return {
        "id": project["id"],
        "status": project.get("status", "ready"),
        "workflow_type": "midi",
        "duration": project["duration"],
        "midi_filename": project.get("midi_filename", "melody.mid"),
        "tracks": project.get("tracks", []),
        "selected_track_index": project.get("selected_track_index", 0),
        "segments": segments,
        "singer_profile": project.get("singer_profile"),
        "created_at": project.get("created_at"),
        "lrc_draft": project.get("lrc_draft"),
        "last_generation": (
            {
                "steps": last_generation.get("steps"),
                "pitch_shift": last_generation.get("pitch_shift", 0),
                "changed_lines": last_generation.get("changed_lines"),
                "created_at": last_generation.get("created_at"),
            }
            if last_generation
            else None
        ),
        "jobs": list_jobs(project["id"]),
    }


@app.post("/api/midi-projects")
async def create_midi_project(
    midi_file: UploadFile = File(...),
    singer_id: str = Form(default=""),
) -> dict[str, Any]:
    filename = Path(midi_file.filename or "melody.mid").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_MIDI_EXTENSIONS:
        await midi_file.close()
        raise HTTPException(status_code=415, detail="请上传 Standard MIDI 文件（.mid 或 .midi）。")
    selected_singer = _load_singer(singer_id.strip()) if singer_id.strip() else None

    project_id = uuid.uuid4().hex
    session_dir = OUTPUT_ROOT / project_id
    session_dir.mkdir(parents=True, exist_ok=True)
    midi_path = session_dir / f"melody{suffix}"
    uploaded_bytes = 0
    try:
        with midi_path.open("wb") as target:
            while chunk := await midi_file.read(1024 * 1024):
                uploaded_bytes += len(chunk)
                if uploaded_bytes > 100 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="MIDI 文件不能超过 100 MB。")
                target.write(chunk)
    except Exception:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise
    finally:
        await midi_file.close()

    try:
        midi_info = workflow.inspect_midi(midi_path)
    except workflow.WorkflowError as exc:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if selected_singer:
        reference_path = Path(selected_singer["audio_path"])
        prompt_path = session_dir / f"voice_profile_{selected_singer['id']}{reference_path.suffix.lower()}"
        shutil.copy2(reference_path, prompt_path)
        prompt_text = selected_singer["reference_text"]
        singer_profile = {
            "id": selected_singer["id"],
            "name": selected_singer["name"],
            "updated_at": selected_singer["updated_at"],
        }
    else:
        default_reference = ROOT / "resources" / "audios" / "male.wav"
        if not default_reference.is_file():
            shutil.rmtree(session_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail="模型默认参考音频不存在。")
        prompt_path = session_dir / "voice_prompt_default.wav"
        shutil.copy2(default_reference, prompt_path)
        prompt_text = "在爱的回归线，又期待相见。"
        singer_profile = None

    created_at = _now()
    project = {
        "id": project_id,
        "session_dir": str(session_dir),
        "workflow_type": "midi",
        "midi_path": str(midi_path),
        "midi_filename": filename,
        "tracks": midi_info["tracks"],
        "selected_track_index": midi_info["selected_track_index"],
        "prompt_path": str(prompt_path),
        "prompt_text": prompt_text,
        "source_prompt_path": str(prompt_path),
        "source_prompt_text": prompt_text,
        "singer_profile": singer_profile,
        "sample_rate": workflow.MIDI_SAMPLE_RATE,
        "duration": midi_info["duration"],
        "status": "ready",
        "segments": midi_info["segments"],
        "created_at": created_at,
        "last_generation": None,
    }
    _save_project(project)
    with _connect() as connection:
        connection.execute(
            "INSERT INTO projects(id,session_dir,status,duration,created_at) VALUES(?,?,?,?,?)",
            (project_id, str(session_dir), "ready", project["duration"], created_at),
        )
    return _public_midi_project(project)


@app.get("/api/midi-projects/{project_id}")
def get_midi_project(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("workflow_type") != "midi":
        raise HTTPException(status_code=404, detail="找不到这个 MIDI 工程。")
    row = _project_row(project_id)
    project["status"] = row["status"] if row else project.get("status", "ready")
    return _public_midi_project(project)


@app.put("/api/midi-projects/{project_id}/draft")
def save_midi_workspace_draft(
    project_id: str, request: MidiWorkspaceDraftUpdate
) -> dict[str, str]:
    project = _load_project(project_id)
    if project.get("workflow_type") != "midi":
        raise HTTPException(status_code=404, detail="找不到这个 MIDI 工程。")
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="MIDI 工程准备完成后才能保存草稿。")
    segments = project.get("segments", [])
    if len(request.edited) != len(segments):
        raise HTTPException(status_code=409, detail="MIDI 歌词段数已变化，请刷新工程后重试。")
    if any(len(text) > 500 for text in request.edited):
        raise HTTPException(status_code=422, detail="单段歌词最多 500 个字符。")

    project["segments"] = [
        {**section, "edited": request.edited[index]}
        for index, section in enumerate(segments)
    ]
    project["lrc_draft"] = {
        "source": request.lrc_source,
        "lines": [line.model_dump() for line in request.lrc_lines],
        "enabled": request.use_lrc_lyrics,
    }
    _save_project(project)
    return {"status": "saved"}


@app.put("/api/midi-projects/{project_id}/track")
def select_midi_track(project_id: str, request: MidiTrackSelection) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("workflow_type") != "midi":
        raise HTTPException(status_code=404, detail="找不到这个 MIDI 工程。")
    if request.track_index >= len(project.get("tracks", [])):
        raise HTTPException(status_code=404, detail="所选 MIDI 音轨不存在。")
    track = project["tracks"][request.track_index]
    if track.get("is_drum") or not track.get("note_count"):
        raise HTTPException(status_code=422, detail="请选择包含旋律音符的非鼓组音轨。")
    with _connect() as connection:
        active = connection.execute(
            "SELECT id FROM jobs WHERE project_id=? AND kind IN ('midi_preview','midi_generate_song') AND status IN ('queued','running') LIMIT 1",
            (project_id,),
        ).fetchone()
    if active:
        raise HTTPException(status_code=409, detail="生成过程中不能更改 MIDI 音轨。")
    if request.track_index != project.get("selected_track_index"):
        try:
            segments = workflow.midi_sections_for_track(Path(project["midi_path"]), request.track_index)
        except workflow.WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project["selected_track_index"] = request.track_index
        project["segments"] = segments
        project["last_generation"] = None
        _save_project(project)
    return _public_midi_project(project)


def _normalize_midi_lyric_lines(
    project: dict[str, Any],
    requested: list[MidiLyricLineRequest],
) -> list[dict[str, Any]]:
    if not requested:
        raise HTTPException(status_code=422, detail="LRC 中没有可用歌词句。")
    duration = float(project["duration"])
    lines: list[dict[str, Any]] = []
    for position, item in enumerate(requested):
        if item.index != position:
            raise HTTPException(status_code=422, detail="歌词句必须按 LRC 中的顺序连续编号。")
        start = float(item.start)
        end = float(item.end)
        if not 0 <= start < end <= duration + 0.02:
            raise HTTPException(status_code=422, detail=f"第 {position + 1} 句的时间范围超出 MIDI 时长。")
        if end - start < 0.3:
            raise HTTPException(status_code=422, detail=f"第 {position + 1} 句的 MIDI 音符区间不足 0.3 秒，请合并相邻短句。")
        if end - start > 44.5:
            raise HTTPException(status_code=422, detail=f"第 {position + 1} 句超过 44.5 秒，请拆分此句。")
        if not item.lyrics.strip():
            raise HTTPException(status_code=422, detail=f"请填写第 {position + 1} 句歌词。")
        lines.append({
            "index": position,
            "start": start,
            "end": min(end, duration),
            "lyrics": item.lyrics.strip(),
        })

    for index, (current, following) in enumerate(zip(lines, lines[1:]), start=1):
        if following["start"] <= current["start"]:
            raise HTTPException(status_code=422, detail="分配后的 MIDI 乐句起点必须按歌词顺序递增。")
        if abs(current["end"] - following["start"]) > 0.02:
            raise HTTPException(status_code=422, detail=f"第 {index} 句的 MIDI 区间应连续接到第 {index + 1} 句。")
        current["end"] = following["start"]
    if abs(lines[-1]["end"] - duration) > 0.02:
        raise HTTPException(status_code=422, detail="最后一句的结束时间必须延伸到 MIDI 末尾。")
    lines[-1]["end"] = duration

    try:
        counts = workflow.midi_note_counts_for_ranges(
            Path(project["midi_path"]),
            int(project.get("selected_track_index", 0)),
            [(line["start"], line["end"]) for line in lines],
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for line, note_count in zip(lines, counts):
        if not note_count:
            raise HTTPException(status_code=422, detail=f"第 {line['index'] + 1} 句没有分到 MIDI 音符，请检查歌词字数或合并相邻短句。")
        line["note_count"] = int(note_count)
        line["has_notes"] = bool(note_count)
    return lines


@app.post("/api/midi-projects/{project_id}/preview")
def request_midi_line_preview(project_id: str, request: MidiLinePreviewRequest) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("workflow_type") != "midi":
        raise HTTPException(status_code=404, detail="找不到这个 MIDI 工程。")
    start, end = float(request.start), float(request.end)
    if not 0 <= start < end <= float(project["duration"]) + 0.02:
        raise HTTPException(status_code=422, detail="歌词时间范围必须落在 MIDI 时长内。")
    end = min(end, float(project["duration"]))
    if end - start < 0.3 or end - start > 44.5:
        raise HTTPException(status_code=422, detail="单句试听时长需在 0.3 到 44.5 秒之间。")
    try:
        note_count = workflow.midi_note_counts_for_ranges(
            Path(project["midi_path"]),
            int(project.get("selected_track_index", 0)),
            [(start, end)],
        )[0]
    except workflow.WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not note_count:
        raise HTTPException(status_code=422, detail="这句的 MIDI 音符区间为空，请检查歌词字数或合并相邻短句。")
    with _connect() as connection:
        active = connection.execute(
            "SELECT id FROM jobs WHERE project_id=? AND kind IN ('midi_preview','midi_generate_song') AND status IN ('queued','running') LIMIT 1",
            (project_id,),
        ).fetchone()
    if active:
        raise HTTPException(status_code=409, detail="当前已有 MIDI 试听或整曲生成任务，请完成后再提交。")
    preview_id = uuid.uuid4().hex
    job_id = _create_job(
        project_id,
        "midi_preview",
        {
            "index": request.index,
            "start": start,
            "end": end,
            "lyrics": request.lyrics.strip(),
            "steps": request.steps,
            "pitch_shift": request.pitch_shift,
            "preview_id": preview_id,
        },
        priority=0,
        label=f"等待 MIDI 第 {request.index + 1} 句歌声试听",
    )
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/midi-projects/{project_id}/previews/{preview_id}")
def midi_line_preview_asset(project_id: str, preview_id: str) -> FileResponse:
    project = _load_project(project_id)
    if project.get("workflow_type") != "midi":
        raise HTTPException(status_code=404, detail="找不到这个 MIDI 工程。")
    preview = project.get("midi_line_previews", {}).get(preview_id)
    if not preview:
        raise HTTPException(status_code=404, detail="这句还没有生成歌声试听。")
    root = Path(project["session_dir"]).resolve()
    path = (root / preview["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="试听音频不存在。")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.post("/api/midi-projects/{project_id}/generate")
def generate_midi_project(project_id: str, request: MidiGenerateRequest) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("workflow_type") != "midi":
        raise HTTPException(status_code=404, detail="找不到这个 MIDI 工程。")
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="MIDI 工程尚未就绪。")
    line_payload: list[dict[str, Any]] | None = None
    lyrics: list[str] | None = None
    if request.lines:
        line_payload = _normalize_midi_lyric_lines(project, request.lines)
        if not any(line["has_notes"] for line in line_payload):
            raise HTTPException(status_code=422, detail="这些歌词句没有分到 MIDI 音符，请检查歌词字数或分句。")
    elif request.lyrics:
        if len(request.lyrics) != len(project["segments"]):
            raise HTTPException(status_code=422, detail="歌词段落数量已变化，请刷新页面后再生成。")
        lyrics = [text.strip() for text in request.lyrics]
        for index, (segment, text) in enumerate(zip(project["segments"], lyrics), start=1):
            if segment.get("has_notes") and not text:
                raise HTTPException(status_code=422, detail=f"第 {index} 段有旋律音符，请填写歌词。")
            if len(text) > 500:
                raise HTTPException(status_code=422, detail=f"第 {index} 段歌词不能超过 500 个字符。")
        if not any(segment.get("has_notes") and text for segment, text in zip(project["segments"], lyrics)):
            raise HTTPException(status_code=422, detail="请至少填写一段歌词。")
    else:
        raise HTTPException(status_code=422, detail="请先导入 LRC 歌词或填写 MIDI 段落歌词。")

    with _connect() as connection:
        active = connection.execute(
            "SELECT id FROM jobs WHERE project_id=? AND kind IN ('midi_preview','midi_generate_song') AND status IN ('queued','running') LIMIT 1",
            (project_id,),
        ).fetchone()
    if active:
        raise HTTPException(status_code=409, detail="此 MIDI 工程已有生成任务在队列中或正在运行。")

    run_id = uuid.uuid4().hex
    project["last_generation"] = None
    if lyrics is not None:
        project["segments"] = [
            {**segment, "edited": lyrics[index]}
            for index, segment in enumerate(project["segments"])
        ]
    _save_project(project)
    if line_payload:
        line_payload = [
            {key: line[key] for key in ("index", "start", "end", "lyrics", "note_count", "has_notes")}
            for line in line_payload
        ]
    job_id = _create_job(
        project_id,
        "midi_generate_song",
        {
            "run_id": run_id,
            "lyrics": lyrics,
            "lines": line_payload,
            "steps": request.steps,
            "pitch_shift": request.pitch_shift,
        },
        priority=10,
        label=(
            f"等待 LRC 逐句 MIDI 歌声生成 · {sum(bool(item.get('has_notes')) for item in line_payload)} 句"
            if line_payload
            else f"等待 MIDI 歌声生成 · {sum(bool(item.get('has_notes')) for item in project['segments'])} 段"
        ),
    )
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects")
async def create_project(
    audio: UploadFile = File(...),
    lrc_file: UploadFile | None = File(default=None),
    pasted_lrc: str = Form(default=""),
    auto_transcribe: bool = Form(default=False),
    singer_id: str = Form(default=""),
) -> dict[str, Any]:
    suffix = Path(audio.filename or "song.wav").suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=415, detail="请上传 WAV、MP3、FLAC 或 OGG 音频。")

    if pasted_lrc.strip():
        lrc_text = pasted_lrc
    elif lrc_file is not None:
        try:
            lrc_text = workflow._decode_lrc(await lrc_file.read())
        except workflow.WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        lrc_text = ""
        if not auto_transcribe:
            raise HTTPException(status_code=422, detail="请上传 LRC、粘贴带时间戳的歌词，或启用本地自动识别。")

    selected_singer = _load_singer(singer_id.strip()) if singer_id.strip() else None

    project_id = uuid.uuid4().hex
    session_dir = OUTPUT_ROOT / project_id
    session_dir.mkdir(parents=True, exist_ok=True)
    original_path = session_dir / f"original{suffix}"
    uploaded_bytes = 0
    try:
        with original_path.open("wb") as target:
            while chunk := await audio.read(1024 * 1024):
                uploaded_bytes += len(chunk)
                if uploaded_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="单个音频文件不能超过 300 MB。")
                target.write(chunk)
    except Exception:
        original_path.unlink(missing_ok=True)
        try:
            session_dir.rmdir()
        except OSError:
            pass
        raise
    finally:
        await audio.close()

    try:
        duration = float(sf.info(str(original_path)).duration)
    except (OSError, RuntimeError) as exc:
        original_path.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail="无法读取该音频格式，请尝试转换为 WAV 或 MP3。") from exc
    if duration < 2 or duration > 600:
        original_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="音频时长必须在 2 秒到 10 分钟之间。")
    segments = workflow._parse_lrc(lrc_text, duration) if lrc_text.strip() else []
    if lrc_text.strip() and not segments:
        original_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="LRC 中没有有效的时间戳歌词。")

    singer_payload: dict[str, Any] | None = None
    if selected_singer:
        profile_audio = Path(selected_singer["audio_path"])
        copied_audio = session_dir / f"voice_profile_{selected_singer['id']}{profile_audio.suffix.lower()}"
        shutil.copy2(profile_audio, copied_audio)
        singer_payload = {
            "id": selected_singer["id"],
            "name": selected_singer["name"],
            "reference_text": selected_singer["reference_text"],
            "audio_path": str(copied_audio),
            "updated_at": selected_singer["updated_at"],
        }

    created_at = _now()
    project = {
        "id": project_id,
        "session_dir": str(session_dir),
        "original_path": str(original_path),
        "sample_rate": 44_100,
        "duration": duration,
        "status": "preparing",
        "segments": segments,
        "created_at": created_at,
        "singer_profile": (
            {
                "id": singer_payload["id"],
                "name": singer_payload["name"],
                "updated_at": singer_payload["updated_at"],
            }
            if singer_payload
            else None
        ),
        "last_generation": None,
        "latest_previews": {},
    }
    (session_dir / "project.json").write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with _connect() as connection:
        connection.execute(
            "INSERT INTO projects(id,session_dir,status,duration,created_at) VALUES(?,?,?,?,?)",
            (project_id, str(session_dir), "preparing", duration, created_at),
        )
    job_id = _create_job(
        project_id,
        "prepare",
        {
            "original_path": str(original_path),
            "lrc_text": lrc_text,
            "created_at": created_at,
            "auto_transcribe": bool(auto_transcribe and not lrc_text.strip()),
            "singer_profile": singer_payload,
        },
        priority=5,
        label="等待本地识别与人声分离" if auto_transcribe and not lrc_text.strip() else "等待人声分离任务",
    )
    return {"project_id": project_id, "job_id": job_id, "status": "preparing"}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    project_row = _project_row(project_id)
    jobs = list_jobs(project_id)
    last_generation = project.get("last_generation")
    return {
        "id": project["id"],
        "status": project_row["status"] if project_row else project["status"],
        "duration": project["duration"],
        "segments": project["segments"],
        "lyrics_source": project.get("lyrics_source", "lrc"),
        "singer_profile": project.get("singer_profile"),
        "created_at": project.get("created_at"),
        "last_generation": (
            {
                "steps": last_generation.get("steps"),
                "pitch_shift": last_generation.get("pitch_shift", 0),
                "changed_lines": last_generation.get("changed_lines"),
                "created_at": last_generation.get("created_at"),
            }
            if last_generation
            else None
        ),
        "latest_previews": {
            key: {"lyrics": value.get("lyrics", ""), "created_at": value.get("created_at")}
            for key, value in project.get("latest_previews", {}).items()
        },
        "jobs": jobs,
    }


@app.put("/api/projects/{project_id}/draft")
def save_project_lyrics_draft(
    project_id: str, request: LyricsDraftUpdate
) -> dict[str, str]:
    project = _load_project(project_id)
    if project.get("workflow_type") == "midi":
        raise HTTPException(status_code=404, detail="找不到这个改唱工程。")
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="工作区准备完成后才能保存歌词草稿。")
    segments = project.get("segments", [])
    if len(request.lyrics) != len(segments):
        raise HTTPException(status_code=409, detail="歌词行数已变化，请刷新工作区后重试。")
    if any(len(text) > 500 for text in request.lyrics):
        raise HTTPException(status_code=422, detail="单句歌词最多 500 个字符。")

    project["segments"] = [
        {**segment, "edited": request.lyrics[index]}
        for index, segment in enumerate(segments)
    ]
    _save_project(project)
    return {"status": "saved"}


@app.put("/api/projects/{project_id}/singer")
def set_project_singer(project_id: str, request: SingerSelection) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="工作区准备完成后才能更换歌手。")

    with _connect() as connection:
        busy = connection.execute(
            "SELECT kind FROM jobs WHERE project_id=? AND kind IN ('preview','generate_song','generate_line','finalize','midi_preview','midi_generate_song') AND status IN ('queued','running') LIMIT 1",
            (project_id,),
        ).fetchone()
    if busy:
        raise HTTPException(status_code=409, detail="请等当前试听或整曲任务完成后再更换歌手。")

    profile = _load_singer(request.singer_id.strip()) if request.singer_id and request.singer_id.strip() else None
    selected = project.get("singer_profile") or {}
    if profile and selected.get("id") == profile["id"] and selected.get("updated_at") == profile["updated_at"]:
        return {"singer_profile": selected, "invalidated_previews": False, "final_invalidated": False}
    if not profile and not selected:
        return {"singer_profile": None, "invalidated_previews": False, "final_invalidated": False}

    project_dir = _project_dir(project_id)
    if "source_prompt_path" not in project:
        project["source_prompt_path"] = project["prompt_path"]
        project["source_prompt_text"] = project["prompt_text"]

    if profile:
        profile_audio = Path(profile["audio_path"])
        target_path = project_dir / f"voice_profile_{profile['id']}{profile_audio.suffix.lower()}"
        shutil.copy2(profile_audio, target_path)
        project["prompt_path"] = str(target_path)
        project["prompt_text"] = profile["reference_text"]
        project["singer_profile"] = {
            "id": profile["id"],
            "name": profile["name"],
            "updated_at": profile["updated_at"],
        }
    else:
        project["prompt_path"] = project["source_prompt_path"]
        project["prompt_text"] = project["source_prompt_text"]
        project["singer_profile"] = None

    project["latest_previews"] = {}
    final_invalidated = bool(project.get("last_generation"))
    project["last_generation"] = None
    _save_project(project)
    return {
        "singer_profile": project["singer_profile"],
        "invalidated_previews": True,
        "final_invalidated": final_invalidated,
    }


@app.put("/api/projects/{project_id}/timings")
def update_project_timings(project_id: str, request: TimingUpdate) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="音轨准备完成后才能调整时间戳。")

    segments = project.get("segments", [])
    if len(request.starts) != len(segments):
        raise HTTPException(status_code=422, detail="时间戳数量必须与歌词句数一致。")
    if request.ends is not None and len(request.ends) != len(segments):
        raise HTTPException(status_code=422, detail="句尾时间数量必须与歌词句数一致。")

    starts = [float(value) for value in request.starts]
    duration = float(project["duration"])
    ends = (
        [float(value) for value in request.ends]
        if request.ends is not None
        else [*starts[1:], duration]
    )
    if any(not 0.0 <= start < duration for start in starts):
        raise HTTPException(status_code=422, detail="每句开始时间必须在音频范围内。")
    if any(right - left < 0.3 for left, right in zip(starts, starts[1:])):
        raise HTTPException(status_code=422, detail="相邻歌词起始时间必须递增，且至少间隔 0.30 秒。")
    if any(end > duration or end - start < 0.3 for start, end in zip(starts, ends)):
        raise HTTPException(status_code=422, detail="每句必须在音频范围内，并且至少保留 0.30 秒。")
    if any(end > next_start + 0.01 for end, next_start in zip(ends, starts[1:])):
        raise HTTPException(status_code=422, detail="歌词片段不能互相重叠；请缩短句尾或后移下一句。")

    changed_indices = [
        index
        for index, (segment, start, end) in enumerate(zip(segments, starts, ends))
        if abs(float(segment["start"]) - start) >= 0.005
        or abs(float(segment["end"]) - end) >= 0.005
    ]
    if not changed_indices:
        return {"starts": starts, "ends": ends, "invalidated_preview_indices": [], "final_invalidated": False}

    with _connect() as connection:
        busy = connection.execute(
            "SELECT kind FROM jobs WHERE project_id=? AND kind IN ('preview','generate_song','generate_line','finalize') AND status IN ('queued','running') LIMIT 1",
            (project_id,),
        ).fetchone()
    if busy:
        raise HTTPException(status_code=409, detail="请等当前试听或整曲任务完成后再调整时间戳。")

    invalidated_preview_indices: set[int] = set()
    for index in changed_indices:
        invalidated_preview_indices.add(index)

    previews = project.setdefault("latest_previews", {})
    for index in invalidated_preview_indices:
        previews.pop(str(index), None)
    final_invalidated = bool(project.get("last_generation"))
    if final_invalidated:
        project["last_generation"] = None

    for index, segment in enumerate(segments):
        segment["start"] = starts[index]
        segment["end"] = ends[index]
    _save_project(project)
    return {
        "starts": starts,
        "ends": ends,
        "invalidated_preview_indices": sorted(invalidated_preview_indices),
        "final_invalidated": final_invalidated,
    }


@app.get("/api/projects/{project_id}/assets/{asset}")
def project_asset(project_id: str, asset: str) -> FileResponse:
    project = _load_project(project_id)
    if project.get("workflow_type") == "midi":
        choices = {"midi": Path(project["midi_path"])}
    else:
        choices = {
            "original": Path(project["original_path"]),
            "vocals": Path(project["session_dir"]) / "vocals.wav",
            "accompaniment": Path(project["session_dir"]) / "accompaniment.wav",
        }
    if asset == "final" and project.get("last_generation"):
        choices["final"] = Path(project["last_generation"]["path"])
    path = choices.get(asset)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="音频文件尚未生成。")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{project_id}/lines/{index}/preview/original")
def original_line_preview(project_id: str, index: int) -> FileResponse:
    project = _load_project(project_id)
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="人声分离完成后才能试听原唱。")
    try:
        path = workflow.write_original_preview(project, index)
    except workflow.WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{project_id}/lines/{index}/preview/generated")
def generated_line_preview(project_id: str, index: int) -> FileResponse:
    project = _load_project(project_id)
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="人声分离完成后才能试听重唱。")
    if index < 0 or index >= len(project["segments"]):
        raise HTTPException(status_code=404, detail="所选歌词行不存在。")
    preview = project.get("latest_previews", {}).get(str(index))
    if not preview:
        raise HTTPException(status_code=404, detail="这句还没有重唱试听。")
    path = (Path(project["session_dir"]) / "previews" / preview["path"]).resolve()
    if not path.is_relative_to(Path(project["session_dir"]).resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="试听文件不存在。")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{project_id}/preview")
def request_preview(project_id: str, request: PreviewRequest) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="人声分离完成后才能试听。")
    if request.index >= len(project["segments"]):
        raise HTTPException(status_code=404, detail="所选歌词行不存在。")
    if not request.lyrics.strip():
        raise HTTPException(status_code=422, detail="重唱歌词不能为空。")
    with _connect() as connection:
        pending = connection.execute(
            "SELECT id,payload_json FROM jobs WHERE project_id=? AND kind='preview' AND status='queued' ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        if len(pending) >= 4:
            raise HTTPException(status_code=429, detail="试听队列已满；等前面的试听完成后再试。")
    job_id = _create_job(
        project_id,
        "preview",
        {
            "index": request.index,
            "lyrics": request.lyrics.strip(),
            "steps": request.steps,
            "pitch_shift": request.pitch_shift,
        },
        priority=0,
        label=f"等待试听第 {request.index + 1} 句",
    )
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/preview-aligned")
def request_aligned_preview(project_id: str, request: AlignedPreviewRequest) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="人声分离完成后才能试听。")
    if request.index >= len(project["segments"]):
        raise HTTPException(status_code=404, detail="所选歌词行不存在。")

    segment = project["segments"][request.index]
    line_start = float(segment["start"])
    line_end = float(segment["end"])
    chunks = [item.model_dump() for item in request.chunks]
    cursor = line_start
    for position, chunk in enumerate(chunks, start=1):
        start = float(chunk["start"])
        end = float(chunk["end"])
        if abs(start - cursor) > 0.02:
            raise HTTPException(status_code=422, detail=f"第 {position} 个分组未与前一段连续。")
        if end - start < 0.3 or end > line_end + 0.02:
            raise HTTPException(status_code=422, detail=f"第 {position} 个分组的时间范围无效。")
        cursor = end
    if abs(cursor - line_end) > 0.02:
        raise HTTPException(status_code=422, detail="分组必须连续覆盖整句，保留当前句子的起止边界。")

    with _connect() as connection:
        pending = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE project_id=? AND kind IN ('preview','aligned_preview') AND status IN ('queued','running')",
            (project_id,),
        ).fetchone()[0]
        if pending >= 4:
            raise HTTPException(status_code=429, detail="试听队列已满；等前面的试听完成后再试。")
    job_id = _create_job(
        project_id,
        "aligned_preview",
        {
            "index": request.index,
            "chunks": chunks,
            "steps": request.steps,
            "pitch_shift": request.pitch_shift,
        },
        priority=0,
        label=f"等待按音节分组试听第 {request.index + 1} 句",
    )
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/projects/{project_id}/generate")
def request_song_generation(project_id: str, request: GenerateRequest) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.get("status") != "ready":
        raise HTTPException(status_code=409, detail="人声分离完成后才能生成整曲。")
    if not request.lines:
        raise HTTPException(status_code=422, detail="请先提供歌词行。")
    indexes = [line.index for line in request.lines]
    if len(indexes) != len(set(indexes)):
        raise HTTPException(status_code=422, detail="歌词行索引重复。")
    if any(index >= len(project["segments"]) for index in indexes):
        raise HTTPException(status_code=422, detail="歌词行索引超出范围。")
    changed = [
        {"index": line.index, "lyrics": line.text.strip()}
        for line in request.lines
        if line.text.strip() != project["segments"][line.index]["original"].strip()
    ]
    if not changed:
        raise HTTPException(status_code=422, detail="还没有修改任何歌词。")
    if any(not item["lyrics"] for item in changed):
        raise HTTPException(status_code=422, detail="修改后的歌词不能为空。")
    with _connect() as connection:
        active = connection.execute(
            "SELECT id FROM jobs WHERE project_id=? AND kind='generate_song' AND status IN ('queued','running') LIMIT 1",
            (project_id,),
        ).fetchone()
    if active:
        raise HTTPException(status_code=409, detail="这个项目已有整曲任务在队列中或正在生成。")

    run_id = uuid.uuid4().hex
    run_dir = Path(project["session_dir"]) / "generations" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "steps": request.steps,
        "pitch_shift": request.pitch_shift,
        "changed": changed,
    }
    (run_dir / "request.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for line in request.lines:
        project["segments"][line.index]["edited"] = line.text
    _save_project(project)

    parent_id = uuid.uuid4().hex
    _create_job(
        project_id,
        "generate_song",
        manifest,
        queueable=False,
        priority=10,
        label=f"等待 GPU 队列 · 0/{len(changed)} 句",
        job_id=parent_id,
    )
    for position, item in enumerate(changed, start=1):
        _create_job(
            project_id,
            "generate_line",
            {
                "run_id": run_id,
                "index": item["index"],
                "lyrics": item["lyrics"],
                "steps": request.steps,
                "pitch_shift": request.pitch_shift,
                "position": position,
                "total": len(changed),
            },
            parent_id=parent_id,
            priority=10,
            label=f"等待第 {item['index'] + 1} 句",
        )
    return {"job_id": parent_id, "status": "queued", "changed_lines": len(changed)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    if not job or job["parent_id"]:
        raise HTTPException(status_code=404, detail="找不到任务。")
    return job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = _get_job(job_id)
    if not job or job["parent_id"]:
        raise HTTPException(status_code=404, detail="找不到任务。")

    async def stream_events():
        previous = None
        while True:
            snapshot = await asyncio.to_thread(_get_job, job_id)
            if snapshot is None:
                yield "event: error\ndata: {\"message\":\"任务已不存在\"}\n\n"
                return
            encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            if encoded != previous:
                yield f"data: {encoded}\n\n"
                previous = encoded
            else:
                yield ": keep-alive\n\n"
            if snapshot["status"] in TERMINAL_STATES:
                return
            await asyncio.sleep(0.7)

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/projects/{project_id}/jobs")
def list_jobs(project_id: str) -> list[dict[str, Any]]:
    if not _project_row(project_id):
        raise HTTPException(status_code=404, detail="找不到这个项目。")
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs WHERE project_id=? AND parent_id IS NULL ORDER BY created_at DESC LIMIT 30",
            (project_id,),
        ).fetchall()
    return [_job_dict(row) for row in rows]


@app.get("/api/queue")
def queue_snapshot() -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs WHERE parent_id IS NULL ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, created_at DESC LIMIT 12"
        ).fetchall()
    return [_job_dict(row) for row in rows]


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    if not job or job["parent_id"]:
        raise HTTPException(status_code=404, detail="找不到任务。")
    if job["status"] in TERMINAL_STATES:
        return job
    if job["kind"] == "generate_song":
        _update_job(job_id, cancel_requested=True)
        if job["status"] == "queued":
            with _connect() as connection:
                connection.execute(
                    "UPDATE jobs SET status='canceled',label='已取消',updated_at=? WHERE parent_id=? AND queueable=1 AND status='queued'",
                    (_now(), job_id),
                )
            _update_job(job_id, status="canceled", progress=1.0, label="整曲任务已取消", cancel_requested=True)
        else:
            _update_job(job_id, cancel_requested=True, label="将在当前句完成后停止")
    elif job["status"] == "queued":
        _update_job(job_id, status="canceled", progress=1.0, label="任务已取消")
    else:
        _update_job(job_id, cancel_requested=True, label="将在当前推理完成后停止")
    return _get_job(job_id) or job


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def frontend_root() -> Response:
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        return HTMLResponse(
            "<h1>Song of Theseus 前端尚未构建</h1><p>请在 frontend 目录执行 npm install 和 npm run build。</p>",
            status_code=503,
        )
    return FileResponse(index)


@app.get("/{path:path}", include_in_schema=False)
def frontend_files(path: str) -> FileResponse:
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found.")
    dist = FRONTEND_DIST.resolve()
    candidate = (dist / path).resolve()
    if candidate.is_relative_to(dist) and candidate.is_file():
        return FileResponse(candidate)
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend has not been built.")


def run_server() -> None:
    host = os.environ.get("YINGSTUDIO_HOST", "0.0.0.0")
    port = int(os.environ.get("YINGSTUDIO_PORT", "23253"))
    uvicorn.run(app, host=host, port=port, workers=1, log_level="info")

