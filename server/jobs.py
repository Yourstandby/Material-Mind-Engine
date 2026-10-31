"""入库任务注册表：进度落盘，刷新/换标签页后仍能看到。

每个任务一个 JSON 文件（logs/ingest_jobs/<id>.json），写入用临时文件 + 原子替换，
多 worker 之间通过文件共享状态；实际抽取在后台线程里跑，不依赖浏览器连接。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

ROOT = Path("logs") / "ingest_jobs"
MAX_WARNS = 20
STALE_SECONDS = 180
LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _path(job_id: str) -> Path:
    return ROOT / f"{job_id}.json"


def _write(job: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    path = _path(job["id"])
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read(job_id: str) -> dict | None:
    try:
        return json.loads(_path(job_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _stale(job: dict) -> bool:
    return job.get("status") == "running" and _now() - float(job.get("updated_at") or 0) > STALE_SECONDS


def create(filename: str, collection: str) -> dict:
    job = {
        "id": f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "filename": filename,
        "collection": collection,
        "status": "running",
        "stage": "queued",
        "chunks": 0,
        "done": 0,
        "total": 0,
        "found": 0,
        "added": 0,
        "skipped": 0,
        "quarantined": 0,
        "duplicates": 0,
        "warns": [],
        "error": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _write(job)
    return job


def update(job_id: str, **fields) -> dict | None:
    with LOCK:
        job = _read(job_id)
        if not job:
            return None
        job.update(fields)
        job["updated_at"] = _now()
        _write(job)
        return job


def append_warn(job_id: str, message: str) -> dict | None:
    with LOCK:
        job = _read(job_id)
        if not job:
            return None
        warns = list(job.get("warns") or [])
        warns.append(str(message))
        job["warns"] = warns[-MAX_WARNS:]
        job["updated_at"] = _now()
        _write(job)
        return job


def get(job_id: str) -> dict | None:
    job = _read(job_id)
    if job and _stale(job):
        job["status"] = "error"
        job["error"] = "后台任务已中断（服务重启或长时间无进度），请重新投喂"
        job["updated_at"] = _now()
        _write(job)
    return job


def recent(limit: int = 10) -> list[dict]:
    try:
        files = sorted(ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    items = []
    for path in files[: max(1, limit)]:
        job = get(path.stem)
        if job:
            items.append(job)
    return items


def start(filename: str, data: bytes, collection: str, runner, *extra) -> dict:
    """创建任务并在后台线程跑 runner(filename, data, collection, *extra)。"""
    job = create(filename, collection)
    job_id = job["id"]

    def worker() -> None:
        try:
            finished = False
            for event in runner(filename, data, collection, *extra):
                kind = event.get("type")
                if kind == "stage":
                    update(job_id, stage=event.get("stage") or "", chunks=event.get("chunks") or 0)
                elif kind == "progress":
                    update(
                        job_id,
                        stage="extract",
                        done=event.get("done") or 0,
                        total=event.get("total") or 0,
                        found=event.get("found") or 0,
                    )
                elif kind == "record":
                    current = get(job_id) or {}
                    update(job_id, found=int(current.get("found") or 0) + 1)
                elif kind == "warn":
                    append_warn(job_id, event.get("message") or "")
                elif kind == "error":
                    update(job_id, status="error", error=event.get("message") or "入库失败")
                    finished = True
                elif kind == "done":
                    update(
                        job_id,
                        status="done",
                        stage="done",
                        found=event.get("total") or 0,
                        added=event.get("added") or 0,
                        skipped=event.get("skipped") or 0,
                        quarantined=event.get("quarantined") or 0,
                        duplicates=event.get("duplicates") or 0,
                        message=event.get("message") or "",
                    )
                    finished = True
            if not finished:
                update(job_id, status="error", error="任务结束但未收到完成事件，请重试")
        except Exception as exc:  # 线程里不能把异常抛给请求
            update(job_id, status="error", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=worker, name=f"ingest-{job_id}", daemon=True).start()

    def heartbeat() -> None:
        # 长块抽取期间也持续心跳，服务重启后能尽快判定任务已中断
        while True:
            time.sleep(15)
            current = _read(job_id)
            if not current or current.get("status") != "running":
                return
            update(job_id, heartbeat=_now())

    threading.Thread(target=heartbeat, name=f"ingest-hb-{job_id}", daemon=True).start()
    return job
