"""失败案例收集与归类（SQLite，多进程安全）。

一条失败 = 一次回答没能满足用户，可能是：
    A 检索未召回   资料里有，但没进上下文
    B 召回但答错   上下文里有正确值，模型答错
    C 数据缺失     库里根本没有
    D 问题超纲     不在系统能力范围

写入时机：服务端自动捕获（拒答 / 低分 / 无引用 / 未命中）+ 用户点踩 + 用户催资料。

「未命中」单独一组 trigger（no_hit / manual_no_hit）：同一问题按归一化键合并计数，
后台看到的是「谁在等什么资料」，可以直接当采购/抓取清单用。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from core.config import env

DB = Path(env("FAILURES_DB", "./failures.db"))

VERDICTS = {
    "A": "检索未召回",
    "B": "召回但答错",
    "C": "数据缺失",
    "D": "问题超纲",
}

# 未命中相关 trigger：库里没有该资料，用户明确表达了需求
MISS_TRIGGERS = ("no_hit", "manual_no_hit")


def question_key(question: str) -> str:
    """归一化问题 → 去重键。全半角、大小写、标点空白都不影响同一个问题合并。"""
    text = unicodedata.normalize("NFKC", question or "").lower()
    text = re.sub(r"[\s\W_]+", "", text)
    return text[:160]


def _loads_list(raw) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


@contextmanager
def _conn():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS failures ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "ts TEXT NOT NULL,"
        "username TEXT,"
        "question TEXT NOT NULL,"
        "answer TEXT,"
        "sources TEXT,"
        "top_score REAL,"
        "trigger TEXT NOT NULL,"
        "verdict TEXT,"
        "expected TEXT,"
        "status TEXT NOT NULL DEFAULT 'open',"
        "note TEXT,"
        "qkey TEXT,"
        "hits INTEGER NOT NULL DEFAULT 1,"
        "last_ts TEXT,"
        "users TEXT)"
    )
    # 老库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加字段
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(failures)")}
    for name, ddl in (
        ("qkey", "TEXT"),
        ("hits", "INTEGER NOT NULL DEFAULT 1"),
        ("last_ts", "TEXT"),
        ("users", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE failures ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fail_status ON failures(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fail_qkey ON failures(qkey)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fail_trigger ON failures(trigger)")
    conn.commit()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record(
    question: str,
    answer: str = "",
    username: str = "",
    sources: list[str] | None = None,
    top_score: float | None = None,
    trigger: str = "auto",
    verdict: str | None = None,
    expected: str | None = None,
    dedupe: bool = False,
) -> int:
    """写一条失败。

    dedupe=True 时按 question_key 合并到同一条未修复记录上（hits 累加、users 去重），
    避免同一个「库里没有 X」被几十个人点出几十行。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    qkey = question_key(question)
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    with _conn() as conn:
        row = None
        if dedupe:
            row = conn.execute(
                "SELECT * FROM failures WHERE qkey = ? AND status != 'fixed'"
                " ORDER BY id DESC LIMIT 1",
                (qkey,),
            ).fetchone()
        if row is None:
            cursor = conn.execute(
                "INSERT INTO failures (ts, last_ts, username, question, answer, sources,"
                " top_score, trigger, verdict, expected, qkey, hits, users)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    now,
                    username,
                    question,
                    (answer or "")[:4000],
                    sources_json,
                    top_score,
                    trigger,
                    verdict,
                    expected,
                    qkey,
                    1,
                    json.dumps([username] if username else [], ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

        users = _loads_list(row["users"])
        if not users and row["username"]:
            users = [row["username"]]
        if username and username not in users:
            users.append(username)
        conn.execute(
            "UPDATE failures SET hits = COALESCE(hits, 1) + 1, last_ts = ?, users = ?,"
            " username = COALESCE(NULLIF(?, ''), username),"
            " answer = COALESCE(NULLIF(?, ''), answer),"
            " sources = CASE WHEN ? = '[]' THEN sources ELSE ? END,"
            " top_score = COALESCE(?, top_score),"
            " qkey = COALESCE(NULLIF(?, ''), qkey),"
            " verdict = COALESCE(verdict, ?),"
            " trigger = CASE WHEN ? = 'manual_no_hit' THEN 'manual_no_hit' ELSE trigger END"
            " WHERE id = ?",
            (
                now,
                json.dumps(users, ensure_ascii=False),
                username,
                (answer or "")[:4000],
                sources_json,
                sources_json,
                top_score,
                qkey,
                verdict,
                trigger,
                row["id"],
            ),
        )
        return int(row["id"])


def record_miss(question: str, username: str = "", trigger: str = "no_hit") -> dict:
    """记录一次「库里没这个资料」，同一问题合并计数。

    返回 id / hits / users（去重后的提问人）/ is_new，前端拿 hits 显示「已有 N 人在等」。
    """
    if trigger not in MISS_TRIGGERS:
        raise ValueError(f"trigger 必须是 {MISS_TRIGGERS}")
    fid = record(
        question=question,
        username=username,
        trigger=trigger,
        verdict="C",  # 未命中必然是 C 类：库里根本没有
        dedupe=True,
    )
    with _conn() as conn:
        row = conn.execute("SELECT hits, users FROM failures WHERE id = ?", (fid,)).fetchone()
    users = _loads_list(row["users"] if row else None)
    return {
        "id": fid,
        "hits": int((row["hits"] if row else 1) or 1),
        "users": len(users),
        "is_new": len(users) <= 1,
    }


def misses(limit: int = 200, days: int = 365) -> list[dict]:
    """未命中问题清单（按归一化问题聚合），给后台当「待补资料」看板。"""
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
    placeholders = ",".join("?" for _ in MISS_TRIGGERS)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM failures WHERE trigger IN ({placeholders})"
            " AND status != 'fixed' AND COALESCE(last_ts, ts) >= ?"
            " ORDER BY COALESCE(last_ts, ts) DESC",
            (*MISS_TRIGGERS, since),
        ).fetchall()

    groups: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        key = item.get("qkey") or question_key(item.get("question") or "")
        users = _loads_list(item.get("users"))
        if not users and item.get("username"):
            users = [item["username"]]
        last = item.get("last_ts") or item.get("ts") or ""
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "qkey": key,
                "question": item.get("question") or "",
                "hits": int(item.get("hits") or 1),
                "users": users,
                "ids": [item["id"]],
                "first_ts": item.get("ts") or "",
                "last_ts": last,
                "trigger": item.get("trigger") or "",
            }
            continue
        group["hits"] += int(item.get("hits") or 1)
        group["ids"].append(item["id"])
        for name in users:
            if name not in group["users"]:
                group["users"].append(name)
        group["last_ts"] = max(group["last_ts"], last)
        group["first_ts"] = min(filter(None, [group["first_ts"], item.get("ts") or ""]), default="")
        if item.get("trigger") == "manual_no_hit":
            group["trigger"] = "manual_no_hit"

    items = sorted(groups.values(), key=lambda x: (-x["hits"], x["last_ts"]))
    return items[:limit]


def list_items(status: str = "open", limit: int = 200) -> list[dict]:
    with _conn() as conn:
        if status == "all":
            rows = conn.execute("SELECT * FROM failures ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM failures WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["sources"] = json.loads(item.get("sources") or "[]")
        except json.JSONDecodeError:
            item["sources"] = []
        items.append(item)
    return items


def classify(fid: int, verdict: str, expected: str | None = None, note: str | None = None) -> bool:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict 必须是 {list(VERDICTS)}")
    with _conn() as conn:
        cursor = conn.execute(
            "UPDATE failures SET verdict = ?, expected = COALESCE(?, expected),"
            " note = COALESCE(?, note), status = 'classified' WHERE id = ?",
            (verdict, expected, note, fid),
        )
    return cursor.rowcount > 0


def resolve(fid: int, note: str | None = None) -> bool:
    with _conn() as conn:
        cursor = conn.execute(
            "UPDATE failures SET status = 'fixed', note = COALESCE(?, note) WHERE id = ?",
            (note, fid),
        )
    return cursor.rowcount > 0


def stats() -> dict:
    with _conn() as conn:
        by_status = conn.execute(
            "SELECT status, COUNT(*) AS n FROM failures GROUP BY status"
        ).fetchall()
        by_verdict = conn.execute(
            "SELECT COALESCE(verdict, '未归类') AS v, COUNT(*) AS n FROM failures GROUP BY v"
        ).fetchall()
        placeholders = ",".join("?" for _ in MISS_TRIGGERS)
        miss_row = conn.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(hits,1)),0) AS h FROM failures"
            f" WHERE trigger IN ({placeholders}) AND status != 'fixed'",
            MISS_TRIGGERS,
        ).fetchone()
    return {
        "status": {row["status"]: row["n"] for row in by_status},
        "verdict": {row["v"]: row["n"] for row in by_verdict},
        "misses": {"questions": int(miss_row["n"] or 0), "asks": int(miss_row["h"] or 0)},
    }


def to_eval_line(fid: int) -> str | None:
    """把已归类的失败案例转成评测题目（只有 A/B 类适合）。"""
    item = next((x for x in list_items("classified", 1000) if x["id"] == fid), None)
    if not item or item.get("verdict") not in ("A", "B"):
        return None
    expected = (item.get("expected") or "").strip()
    source = ""
    sources = item.get("sources") or []
    if sources:
        source = str(sources[0]).split(" p.")[0]
    payload = {
        "q": item["question"],
        "category": "回归",
        "expect_source": source or None,
        "expect_value": expected or None,
        "material": None,
        "note": f"失败案例 #{fid}（{VERDICTS.get(item['verdict'], '')}）",
    }
    return json.dumps(payload, ensure_ascii=False)
