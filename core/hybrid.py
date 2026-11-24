"""混合检索：BM25（SQLite FTS5）+ 向量召回 → RRF 融合 → 可选 cross-encoder 重排。

定位：不绑定向量库，也不绑定 embedding 供应商。
    - 稠密候选由调用方（query_engine）从向量库取；
    - 词法索引独立存 SQLite（默认 ./hybrid_bm25.db），与向量库解耦：
      换 embedding 模型要重建向量，但词法索引不用动；改 RRF 权重也不动索引。

环境变量：
    HYBRID_ENABLED=1            总开关（0 = 退回纯向量检索，行为与旧版一致）
    HYBRID_DB=./hybrid_bm25.db  词法索引文件
    HYBRID_RRF_K=60             RRF 平滑常数
    HYBRID_VECTOR_WEIGHT=1.0    RRF 里向量排名的权重
    HYBRID_BM25_WEIGHT=1.0      RRF 里 BM25 排名的权重
    HYBRID_BM25_CANDIDATES=50   词法侧召回条数
    RERANK_ENABLED=0            重排开关
    RERANK_BASE_URL=            TEI / OpenAI 兼容的 /rerank 端点
    RERANK_API_KEY=
    RERANK_MODEL=
    RERANK_TOP_N=20             只重排前 N 条候选
    RERANK_TIMEOUT=4

中文没有分词器（不引入 jieba 这类依赖）：用 unigram + bigram 塞进 FTS5，
召回够用，代价是索引体积略大。英文/型号按词与连字符切分。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

from core.config import env

CJK = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[a-z0-9]+(?:[a-z0-9._\-/]*[a-z0-9])?")
MAX_QUERY_TOKENS = 24

_CONN: tuple[str, sqlite3.Connection] | None = None
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS bm25_docs (
    collection TEXT NOT NULL,
    id         TEXT NOT NULL,
    text       TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',
    toks       TEXT NOT NULL,
    PRIMARY KEY (collection, id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS bm25_fts
    USING fts5(toks, content='bm25_docs', content_rowid='rowid', tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS bm25_docs_ai AFTER INSERT ON bm25_docs BEGIN
    INSERT INTO bm25_fts(rowid, toks) VALUES (new.rowid, new.toks);
END;
CREATE TRIGGER IF NOT EXISTS bm25_docs_ad AFTER DELETE ON bm25_docs BEGIN
    INSERT INTO bm25_fts(bm25_fts, rowid, toks) VALUES ('delete', old.rowid, old.toks);
END;
CREATE TRIGGER IF NOT EXISTS bm25_docs_au AFTER UPDATE ON bm25_docs BEGIN
    INSERT INTO bm25_fts(bm25_fts, rowid, toks) VALUES ('delete', old.rowid, old.toks);
    INSERT INTO bm25_fts(rowid, toks) VALUES (new.rowid, new.toks);
END;
"""


# ------------------------------------------------------------------ 配置
def enabled() -> bool:
    return str(env("HYBRID_ENABLED", "1")).strip().lower() not in ("0", "false", "no", "off")


def db_path() -> str:
    return env("HYBRID_DB", "./hybrid_bm25.db")


def rrf_k() -> int:
    try:
        return max(1, int(env("HYBRID_RRF_K", "60")))
    except ValueError:
        return 60


def weights() -> tuple[float, float]:
    try:
        vector = float(env("HYBRID_VECTOR_WEIGHT", "1.0"))
    except ValueError:
        vector = 1.0
    try:
        lexical = float(env("HYBRID_BM25_WEIGHT", "1.0"))
    except ValueError:
        lexical = 1.0
    return vector, lexical


def bm25_candidates() -> int:
    try:
        return max(1, int(env("HYBRID_BM25_CANDIDATES", "50")))
    except ValueError:
        return 50


def rerank_enabled() -> bool:
    switch = str(env("RERANK_ENABLED", "0")).strip().lower()
    return switch in ("1", "true", "yes", "on") and bool(env("RERANK_BASE_URL"))


def rerank_top_n() -> int:
    try:
        return max(1, int(env("RERANK_TOP_N", "20")))
    except ValueError:
        return 20


# ------------------------------------------------------------------ 分词
def tokenize(text: str) -> list[str]:
    low = (text or "").lower()
    tokens = [w for w in WORD.findall(low) if len(w) >= 2]
    for run in CJK.findall(low):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _match_expr(query: str) -> str:
    """FTS5 查询串：每个词加引号，OR 连接（召回优先，排序交给 bm25）。"""
    seen, tokens = set(), []
    for token in tokenize(query):
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= MAX_QUERY_TOKENS:
            break
    if not tokens:
        return ""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


# ------------------------------------------------------------------ 存储
def _conn() -> sqlite3.Connection:
    global _CONN
    path = db_path()
    with _LOCK:
        if _CONN is not None and _CONN[0] == path:
            return _CONN[1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
        _CONN = (path, conn)
        return conn


def index(collection: str, rows: list[tuple]) -> int:
    """rows: [(id, text, meta)]；幂等 upsert，失败只抛给调用方决定是否忽略。"""
    payload = []
    for row in rows or []:
        rid, text, meta = row[0], row[1], row[2] if len(row) > 2 else {}
        text = text or ""
        if not str(rid) or not text.strip():
            continue
        payload.append(
            (
                collection,
                str(rid),
                text,
                json.dumps(meta or {}, ensure_ascii=False),
                " ".join(tokenize(text)),
            )
        )
    if not payload:
        return 0
    conn = _conn()
    with _LOCK:
        conn.executemany(
            "INSERT INTO bm25_docs (collection, id, text, meta, toks) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(collection, id) DO UPDATE SET text=excluded.text, "
            "meta=excluded.meta, toks=excluded.toks",
            payload,
        )
        conn.commit()
    return len(payload)


def delete(collection: str, ids: list[str]) -> int:
    ids = [str(x) for x in (ids or [])]
    if not ids:
        return 0
    conn = _conn()
    with _LOCK:
        conn.executemany(
            "DELETE FROM bm25_docs WHERE collection = ? AND id = ?",
            [(collection, rid) for rid in ids],
        )
        conn.commit()
    return len(ids)


def drop(collection: str) -> int:
    conn = _conn()
    with _LOCK:
        cur = conn.execute("DELETE FROM bm25_docs WHERE collection = ?", (collection,))
        conn.commit()
        return cur.rowcount or 0


def count(collection: str | None = None) -> int:
    conn = _conn()
    if collection is None:
        return conn.execute("SELECT COUNT(*) FROM bm25_docs").fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM bm25_docs WHERE collection = ?", (collection,)
    ).fetchone()[0]


def bm25(collection: str, query: str, limit: int = 50) -> list[dict]:
    """返回 [{"id","text","meta","score"}]，按 BM25 相关性降序。"""
    expr = _match_expr(query)
    if not expr:
        return []
    conn = _conn()
    rows = conn.execute(
        "SELECT d.id, d.text, d.meta, -bm25(bm25_fts) AS score "
        "FROM bm25_fts JOIN bm25_docs d ON d.rowid = bm25_fts.rowid "
        "WHERE bm25_fts MATCH ? AND d.collection = ? ORDER BY score DESC LIMIT ?",
        (expr, collection, max(1, limit)),
    ).fetchall()
    hits = []
    for rid, text, meta, score in rows:
        try:
            parsed = json.loads(meta or "{}")
        except json.JSONDecodeError:
            parsed = {}
        hits.append({"id": str(rid), "text": text, "meta": parsed, "score": float(score or 0)})
    return hits


# ------------------------------------------------------------------ 融合
def rrf(rankings: list[list[str]], k: int | None = None, weights: list[float] | None = None):
    """倒数排名融合：score = Σ w_i / (k + rank_i)。返回 [(id, score)] 降序。"""
    k = k or rrf_k()
    fused: dict[str, float] = {}
    for index, ranking in enumerate(rankings or []):
        weight = weights[index] if weights and index < len(weights) else 1.0
        if weight <= 0:
            continue
        for rank, rid in enumerate(ranking or [], start=1):
            fused[str(rid)] = fused.get(str(rid), 0.0) + weight / (k + rank)
    return sorted(fused.items(), key=lambda item: -item[1])


# ------------------------------------------------------------------ 重排
def rerank_scores(query: str, texts: list[str]) -> list[float] | None:
    """调 HTTP /rerank（TEI / vLLM / Jina 风格）。任何失败都返回 None（降级不改序）。"""
    base = env("RERANK_BASE_URL").rstrip("/")
    if not base or not texts:
        return None
    payload = {"query": query, "texts": [t[:4000] for t in texts], "return_text": False}
    model = env("RERANK_MODEL")
    if model:
        payload["model"] = model
    headers = {"Content-Type": "application/json"}
    key = env("RERANK_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        import httpx

        timeout = float(env("RERANK_TIMEOUT", "4") or 4)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base}/rerank", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception:  # noqa: BLE001 - 重排是加分项，不可用就跳过
        return None

    # 三种返回形态都认：llama.cpp 直接给数组，TEI/Jina 给 results，vLLM 给 data
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("results") or data.get("data")
    else:
        items = None
    if not isinstance(items, list):
        return None

    scores = [-1e9] * len(texts)
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if not isinstance(index, int) or not isinstance(score, (int, float)):
            continue
        if 0 <= index < len(scores):
            scores[index] = float(score)
    return scores


def rerank_rows(query: str, rows: list, top_n: int | None = None) -> list:
    """rows = [(doc, meta, score, order)]。只重排前 N 条，尾部保持原顺序。"""
    if not rows:
        return rows
    head = rows[: (top_n or rerank_top_n())]
    scores = rerank_scores(query, [str(row[0] or "") for row in head])
    if scores is None:
        return rows
    order = sorted(range(len(head)), key=lambda i: -scores[i])
    reordered = [head[i] for i in order]
    tail = rows[len(head) :]
    out = [(row[0], row[1], row[2], rank) for rank, row in enumerate(reordered + tail)]
    return out


# ------------------------------------------------------------------ 状态
def status() -> dict:
    vector_weight, lexical_weight = weights()
    info = {
        "enabled": enabled(),
        "db": db_path(),
        "rrf_k": rrf_k(),
        "weights": {"vector": vector_weight, "bm25": lexical_weight},
        "rerank": {
            "enabled": rerank_enabled(),
            "base_url": env("RERANK_BASE_URL"),
            "model": env("RERANK_MODEL"),
            "top_n": rerank_top_n(),
        },
    }
    try:
        info["docs"] = count()
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001 - health 不应因为索引损坏 500
        info["ok"] = False
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def health() -> dict:
    return status()
