"""向量库访问层：embedded / server 双模式 + 跨进程写锁 + 自动重试。

embedded（默认）：每个 worker 直连 chroma_db
    - 读：SQLite WAL，多进程并发安全
    - 写：chroma.lock 跨进程互斥，避免多个 worker 同时写 HNSW 索引
          （否则会出现 database is locked 或索引损坏）
server：CHROMA_MODE=server
    - 连 `chroma run --path ./chroma_db --port 8001` 起的服务，由服务端统一调度
    - 适合更多 worker / 更高写入频率

环境变量：
    CHROMA_MODE   embedded | server     （默认 embedded）
    CHROMA_PATH   数据目录              （默认 ./chroma_db）
    CHROMA_HOST   server 模式主机       （默认 127.0.0.1）
    CHROMA_PORT   server 模式端口       （默认 8001）
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import chromadb

from core.config import env

MODE = env("CHROMA_MODE", "embedded").lower()
PERSIST = env("CHROMA_PATH", "./chroma_db")
HOST = env("CHROMA_HOST", "127.0.0.1")
PORT = int(env("CHROMA_PORT", "8001"))
LOCK_FILE = Path("chroma.lock")

_RAW = None
_LOCK_FD: int | None = None
_COLLECTIONS: dict = {}


def raw():
    global _RAW
    if _RAW is None:
        if MODE == "server":
            _RAW = chromadb.HttpClient(host=HOST, port=PORT)
        else:
            _RAW = chromadb.PersistentClient(path=PERSIST)
    return _RAW


@contextmanager
def write_lock(timeout: float = 60.0):
    """跨进程写锁（server 模式下由服务端负责，直接放行）。"""
    global _LOCK_FD
    if MODE == "server":
        yield
        return

    fd = _LOCK_FD
    if fd is None:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
        _LOCK_FD = fd

    deadline = time.time() + timeout
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() > deadline:
                raise TimeoutError("等待向量库写锁超时（可能有进程正在大批量写入）")
            time.sleep(0.05)

    try:
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def retry(fn, *args, attempts: int = 5, **kwargs):
    """对临时的 sqlite busy / locked 做退避重试。"""
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 需要按消息判断
            text = str(exc).lower()
            transient = "locked" in text or "busy" in text or "timeout" in text
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(0.12 * (2**attempt))


class Collection:
    """集合代理：写操作自动加锁 + 重试，读操作直接透传。"""

    def __init__(self, name: str, metadata: dict | None = None):
        self._name = name
        self._metadata = metadata
        self._store = None

    # ---- 元信息 ----
    @property
    def store(self):
        if self._store is None:
            if self._metadata:
                self._store = raw().get_or_create_collection(self._name, metadata=self._metadata)
            else:
                self._store = raw().get_or_create_collection(self._name)
        return self._store

    @property
    def name(self) -> str:
        return self._name

    @property
    def metadata(self):
        return retry(lambda: self.store.metadata)

    # ---- 写 ----
    def _write(self, method: str, **kwargs):
        def run():
            return getattr(self.store, method)(**kwargs)

        if MODE == "server":
            return retry(run)
        with write_lock():
            return retry(run)

    def upsert(self, **kwargs):
        return self._write("upsert", **kwargs)

    def add(self, **kwargs):
        return self._write("add", **kwargs)

    def update(self, **kwargs):
        return self._write("update", **kwargs)

    def delete(self, **kwargs):
        return self._write("delete", **kwargs)

    def modify(self, **kwargs):
        return self._write("modify", **kwargs)

    # ---- 读 ----
    def query(self, **kwargs):
        return retry(lambda: self.store.query(**kwargs))

    def get(self, **kwargs):
        return retry(lambda: self.store.get(**kwargs))

    def count(self):
        return retry(lambda: self.store.count())


class Client:
    """保持与 chromadb Client 相同的入口，内部转向代理。"""

    def get_or_create_collection(self, name: str, metadata: dict | None = None):
        key = (name, tuple(sorted((metadata or {}).items())))
        if key not in _COLLECTIONS:
            _COLLECTIONS[key] = Collection(name, metadata)
        return _COLLECTIONS[key]

    def get_collection(self, name: str):
        return self.get_or_create_collection(name)

    def list_collections(self):
        return raw().list_collections()

    def delete_collection(self, name: str):
        with write_lock():
            return retry(lambda: raw().delete_collection(name))


_CLIENT = Client()


def client() -> Client:
    return _CLIENT


def collection(name: str, metadata: dict | None = None) -> Collection:
    return _CLIENT.get_or_create_collection(name, metadata)


def mode() -> str:
    return MODE


def health() -> dict:
    try:
        names = [item.name for item in client().list_collections()]
        return {"mode": MODE, "ok": True, "collections": len(names)}
    except Exception as exc:
        return {"mode": MODE, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
