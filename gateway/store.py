"""网关的 API Key 与用量存储（SQLite，跨进程安全）。"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

from core.config import env

DB = Path(env("GATEWAY_DB", "./gateway.db"))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_keys ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT NOT NULL,"
        "key_hash TEXT UNIQUE NOT NULL,"
        "prefix TEXT NOT NULL,"
        "rpm INTEGER NOT NULL DEFAULT 120,"
        "active INTEGER NOT NULL DEFAULT 1,"
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "key_id INTEGER NOT NULL,"
        "path TEXT NOT NULL,"
        "status INTEGER NOT NULL,"
        "tokens INTEGER NOT NULL DEFAULT 0,"
        "ms INTEGER NOT NULL DEFAULT 0,"
        "at REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_key ON usage(key_id, at)")
    conn.commit()
    return conn


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(name: str, rpm: int = 120) -> str:
    """生成并保存 key，返回明文（只返回这一次）。"""
    key = "mme-" + secrets.token_urlsafe(24)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO api_keys (name, key_hash, prefix, rpm, created_at) VALUES (?,?,?,?,?)",
            (name, _hash(key), key[:10], rpm, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return key


def find(key: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND active = 1", (_hash(key),)
        ).fetchone()
    return dict(row) if row else None


def list_keys() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, name, prefix, rpm, active, created_at FROM api_keys ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def revoke(key_or_id: str) -> bool:
    with _conn() as conn:
        if key_or_id.isdigit():
            cur = conn.execute("UPDATE api_keys SET active = 0 WHERE id = ?", (int(key_or_id),))
        else:
            cur = conn.execute(
                "UPDATE api_keys SET active = 0 WHERE key_hash = ?", (_hash(key_or_id),)
            )
    return cur.rowcount > 0


def recent(key_id: int, seconds: int = 60) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM usage WHERE key_id = ? AND at > ?",
            (key_id, time.time() - seconds),
        ).fetchone()
    return int(row[0])


def log(key_id: int, path: str, status: int, tokens: int, ms: int) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO usage (key_id, path, status, tokens, ms, at) VALUES (?,?,?,?,?,?)",
            (key_id, path, status, tokens, ms, time.time()),
        )


def report(hours: int = 24, limit: int = 50) -> dict:
    since = time.time() - hours * 3600
    with _conn() as conn:
        per_key = conn.execute(
            "SELECT k.name, k.prefix, COUNT(u.id) AS calls, "
            "COALESCE(SUM(u.tokens),0) AS tokens, COALESCE(SUM(u.status >= 400),0) AS errors "
            "FROM api_keys k LEFT JOIN usage u ON u.key_id = k.id AND u.at > ? "
            "GROUP BY k.id ORDER BY calls DESC",
            (since,),
        ).fetchall()
        rows = conn.execute(
            "SELECT u.at, k.name, u.path, u.status, u.tokens, u.ms "
            "FROM usage u JOIN api_keys k ON k.id = u.key_id "
            "ORDER BY u.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "hours": hours,
        "per_key": [dict(row) for row in per_key],
        "recent": [dict(row) for row in rows],
    }
