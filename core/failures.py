"""失败案例收集与归类（SQLite，多进程安全）。

一条失败 = 一次回答没能满足用户，可能是：
    A 检索未召回   资料里有，但没进上下文
    B 召回但答错   上下文里有正确值，模型答错
    C 数据缺失     库里根本没有
    D 问题超纲     不在系统能力范围

写入时机：服务端自动捕获（拒答 / 低分 / 无引用）+ 用户点踩。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from core.config import env

DB = Path(env("FAILURES_DB", "./failures.db"))

VERDICTS = {
    "A": "检索未召回",
    "B": "召回但答错",
    "C": "数据缺失",
    "D": "问题超纲",
}


def _conn() -> sqlite3.Connection:
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
        "note TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fail_status ON failures(status)")
    conn.commit()
    return conn


def record(
    question: str,
    answer: str = "",
    username: str = "",
    sources: list[str] | None = None,
    top_score: float | None = None,
    trigger: str = "auto",
    verdict: str | None = None,
    expected: str | None = None,
) -> int:
    with _conn() as conn:
        cursor = conn.execute(
            "INSERT INTO failures (ts, username, question, answer, sources, top_score,"
            " trigger, verdict, expected) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                username,
                question,
                (answer or "")[:4000],
                json.dumps(sources or [], ensure_ascii=False),
                top_score,
                trigger,
                verdict,
                expected,
            ),
        )
        return int(cursor.lastrowid)


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
    return {
        "status": {row["status"]: row["n"] for row in by_status},
        "verdict": {row["v"]: row["n"] for row in by_verdict},
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
