"""inbox 目录守护：把文件丢进 inbox/<collection>/ 自动入库。

目录约定:
    inbox/                  -> 默认库（materials）
    inbox/peek/xxx.pdf      -> 入 peek 库
    inbox/_done/            -> 处理成功的原始文件
    inbox/_failed/          -> 处理失败的文件
日志: ingest.log
"""

import os
import shutil
import threading
import time
from pathlib import Path

from core.config import env
from ingest.pipeline import ingest

INBOX = Path("inbox")
DONE = INBOX / "_done"
FAILED = INBOX / "_failed"
LOG = Path("ingest.log")

POLL = float(env("INBOX_POLL", "5"))
DEFAULT_COLLECTION = env("INBOX_COLLECTION", "catalog")
SUPPORTED = {".pdf", ".docx", ".xlsx", ".xlsm", ".html", ".htm", ".md", ".txt", ".csv", ".json"}

_started = False
_LOCK_FD: int | None = None


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _collection_of(path: Path) -> str:
    parts = path.relative_to(INBOX).parts
    return parts[0] if len(parts) > 1 else DEFAULT_COLLECTION


def _candidates():
    INBOX.mkdir(exist_ok=True)
    for path in sorted(INBOX.rglob("*")):
        if not path.is_file():
            continue
        if DONE.name in path.parts or FAILED.name in path.parts:
            continue
        if path.suffix.lower() not in SUPPORTED:
            continue
        if path.name.startswith((".", "_", "~$")):
            continue
        yield path


def _settled(path: Path, wait: float = 1.5) -> bool:
    """等文件写完（复制大文件时大小会变）。"""
    try:
        first = path.stat().st_size
    except OSError:
        return False
    time.sleep(wait)
    try:
        return path.stat().st_size == first
    except OSError:
        return False


def process_once() -> int:
    count = 0
    for path in list(_candidates()):
        if not _settled(path):
            continue
        collection = _collection_of(path)
        log(f"[{collection}] 开始 {path.name} ({path.stat().st_size} bytes)")
        ok, summary = True, ""
        try:
            for event in ingest(path.name, path.read_bytes(), collection):
                if event["type"] == "error":
                    ok, summary = False, event["message"]
                elif event["type"] == "warn":
                    log(f"  warn: {event['message']}")
                elif event["type"] == "done":
                    summary = f"+{event['added']} 条 / 跳过 {event['skipped']} / 隔离 {event['quarantined']}"
        except Exception as exc:
            ok, summary = False, f"{type(exc).__name__}: {exc}"

        target = (DONE if ok else FAILED) / collection
        target.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), target / path.name)
        except OSError:
            path.unlink(missing_ok=True)

        log(f"[{collection}] {'完成' if ok else '失败'} {path.name} :: {summary}")
        count += 1
    return count


def _loop() -> None:
    log(f"inbox 守护已启动 · 轮询 {POLL}s · 默认库 {DEFAULT_COLLECTION}")
    while True:
        try:
            process_once()
        except Exception as exc:
            log(f"守护异常: {type(exc).__name__}: {exc}")
        time.sleep(POLL)


def _acquire_lock() -> bool:
    """多进程下只允许一个进程守护 inbox。"""
    global _LOCK_FD
    if _LOCK_FD is not None:
        return True
    try:
        fd = os.open("watcher.lock", os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _LOCK_FD = fd
    return True


def start() -> bool:
    global _started
    if _started:
        return False
    if not _acquire_lock():
        log("inbox 守护已由其他进程启动，本进程跳过")
        return False
    _started = True
    threading.Thread(target=_loop, daemon=True, name="inbox-watcher").start()
    return True


if __name__ == "__main__":
    if env("INBOX_ONCE", "0") == "1":
        print(f"处理 {process_once()} 个文件")
    else:
        _loop()
