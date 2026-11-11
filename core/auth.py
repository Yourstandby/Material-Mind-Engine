"""内部账号体系：注册 / 登录 / 改密 / 重置 / 限流 / 会话。

多进程安全：
- 账号、会话版本、登录失败计数全部存 SQLite（WAL 模式），uvicorn --workers N 共享
- 签名密钥用 O_EXCL 原子创建，避免多进程首启时各生成一份
- watcher 另有文件锁，保证只有一个进程在守护 inbox
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
import time
from pathlib import Path

from core.config import env

DB = Path("auth.db")
SECRET_FILE = Path("auth.secret")
COOKIE = "mme_session"
MAX_AGE = 7 * 24 * 3600
USERNAME_OK = re.compile(r"^[\w\u4e00-\u9fa5.-]{3,32}$")

MAX_FAILS = 5
LOCK_WINDOW = 300

# 允许匿名调用的读接口：只有 GET/HEAD 能匿名，写接口永远要登录。
# 这里只决定「能不能进」，能读到哪些资料由 core/access.py 按级别过滤。
READONLY_PATHS = (
    "/api/chat",
    "/api/search",
    "/api/bootstrap",
    "/api/products",
    "/api/applications",
    "/api/library",
    "/api/sources",
    "/api/sources/records",
    "/api/records",
    "/api/folders",
    "/api/files",
)
# 带路径参数的读接口（/api/products/{id}、/api/files/{doc_id}）
READONLY_PREFIXES = ("/api/products/", "/api/files/")
READONLY_METHODS = ("GET", "HEAD")
_TRUTHY = ("1", "true", "yes", "on")


def _guest_read_ips() -> list[str]:
    return [
        item.strip()
        for item in str(env("MME_ANONYMOUS_IPS", "") or "").replace(";", ",").split(",")
        if item.strip()
    ]


def anonymous_read_enabled(client_ip: str = "") -> bool:
    """游客能否匿名读（不看路径），首页 / 与只读接口共用这套开关。

        MME_ALLOW_ANONYMOUS=1                全部来源可匿名读
        MME_ANONYMOUS_IPS=172.16.1.121,...   仅白名单 IP 可匿名读
    两边都没配 = 退回全站登录。
    """
    if str(env("MME_ALLOW_ANONYMOUS", "") or "").strip().lower() in _TRUTHY:
        return True
    allowed = _guest_read_ips()
    return bool(allowed) and str(client_ip or "").strip() in allowed


def readonly_path(path: str) -> bool:
    return path in READONLY_PATHS or any(path.startswith(p) for p in READONLY_PREFIXES)


def anonymous_allowed(path: str, client_ip: str = "", method: str = "GET") -> bool:
    """读接口的匿名放行判定：方法必须是 GET/HEAD，且路径在只读白名单里。"""
    if str(method or "GET").upper() not in READONLY_METHODS:
        return False
    if not readonly_path(path):
        return False
    return anonymous_read_enabled(client_ip)


def _secret() -> bytes:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    value = secrets.token_bytes(32)
    try:
        fd = os.open(SECRET_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:  # 另一个进程刚创建完
        return SECRET_FILE.read_bytes()
    with os.fdopen(fd, "wb") as fh:
        fh.write(value)
    return value


@contextmanager
def _conn():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT UNIQUE NOT NULL,"
        "pwhash TEXT NOT NULL,"
        "salt TEXT NOT NULL,"
        "role TEXT NOT NULL DEFAULT 'member',"
        "token_version INTEGER NOT NULL DEFAULT 1,"
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS login_fails ("
        "key TEXT NOT NULL,"
        "at REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fails_key ON login_fails(key)")
    conn.commit()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "token_version" not in columns:  # 兼容旧库
        conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 1")
        conn.commit()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def count() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def has_users() -> bool:
    return count() > 0


# ---- 登录限流（跨进程共享）----

def locked_for(key: str) -> int:
    now = time.time()
    with _conn() as conn:
        conn.execute("DELETE FROM login_fails WHERE at < ?", (now - LOCK_WINDOW,))
        rows = conn.execute("SELECT at FROM login_fails WHERE key = ?", (key,)).fetchall()
    if len(rows) >= MAX_FAILS:
        oldest = min(row["at"] for row in rows)
        return max(1, int(LOCK_WINDOW - (now - oldest)))
    return 0


def remaining(key: str) -> int:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM login_fails WHERE key = ? AND at > ?",
            (key, time.time() - LOCK_WINDOW),
        ).fetchone()
    return max(0, MAX_FAILS - int(rows[0]))


def record_failure(key: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO login_fails (key, at) VALUES (?, ?)", (key, time.time()))


def clear_failures(key: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM login_fails WHERE key = ?", (key,))


# ---- 账号 ----

def register(username: str, password: str, invite: str = "") -> dict:
    username = (username or "").strip()
    if not USERNAME_OK.match(username):
        raise ValueError("用户名需 3–32 位，可用中英文、数字、下划线、点、横线")
    if len(password or "") < 8:
        raise ValueError("密码至少 8 位")

    need_invite = count() > 0
    code = env("INVITE_CODE", "")
    if need_invite:
        if not code:
            raise ValueError("未配置 INVITE_CODE，当前不开放注册，请联系管理员")
        if not hmac.compare_digest(invite or "", code):
            raise ValueError("邀请码不正确")

    role = "member" if need_invite else "admin"
    salt = secrets.token_hex(16)
    try:
        with _conn() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, pwhash, salt, role, created_at) VALUES (?,?,?,?,?)",
                (username, _hash(password, salt), salt, role, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            uid = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")
    return {"id": uid, "username": username, "role": role}


def verify(username: str, password: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not hmac.compare_digest(row["pwhash"], _hash(password, row["salt"])):
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def change_password(uid: int, old: str, new: str) -> None:
    if len(new or "") < 8:
        raise ValueError("新密码至少 8 位")
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("账号不存在")
        if not hmac.compare_digest(row["pwhash"], _hash(old, row["salt"])):
            raise ValueError("当前密码不正确")
        _write_password(conn, uid, new)
        username = row["username"]
    clear_user_failures(username)


def reset_password(uid: int, new: str) -> None:
    """管理员或命令行重置：不需要旧密码，重置后旧会话全部失效。"""
    if len(new or "") < 8:
        raise ValueError("新密码至少 8 位")
    with _conn() as conn:
        row = conn.execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("账号不存在")
        _write_password(conn, uid, new)
        username = row["username"]
    clear_user_failures(username)


def clear_user_failures(username: str) -> None:
    """重置/修改密码后清除该账号的失败计数，避免继续被锁。"""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM login_fails WHERE key LIKE ?", (f"%:{username.lower()}",)
        )


def _write_password(conn: sqlite3.Connection, uid: int, new: str) -> None:
    salt = secrets.token_hex(16)
    conn.execute(
        "UPDATE users SET pwhash = ?, salt = ?, token_version = token_version + 1 WHERE id = ?",
        (_hash(new, salt), salt, uid),
    )


def token_version(uid: int) -> int:
    with _conn() as conn:
        row = conn.execute("SELECT token_version FROM users WHERE id = ?", (uid,)).fetchone()
    return int(row["token_version"]) if row else 0


def list_users() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def find_user(username: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


# ---- 会话 ----

def issue(uid: int, version: int | None = None) -> str:
    ver = token_version(uid) if version is None else version
    exp = int(time.time()) + MAX_AGE
    payload = f"{uid}:{exp}:{ver}"
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{signature}"


def resolve(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        raw_uid, raw_exp, raw_ver, signature = token.split(":")
        uid, exp, ver = int(raw_uid), int(raw_exp), int(raw_ver)
    except (ValueError, AttributeError):
        return None
    if exp < time.time():
        return None
    payload = f"{uid}:{exp}:{ver}"
    expect = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expect):
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, username, role, token_version FROM users WHERE id = ?", (uid,)
        ).fetchone()
    if not row or int(row["token_version"]) != ver:  # 改密/重置后旧会话失效
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}
