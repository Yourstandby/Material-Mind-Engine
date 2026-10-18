"""检索 + 生成：各前端共用的核心引擎（不含任何 UI 依赖）。

用法:
    from core.query_engine import retrieve, stream_tokens
"""
import re
from pathlib import Path

import chromadb

from core import embeddings
from core.config import env
from core.llm import client as llm_client, model_name

PERSIST = env("CHROMA_PATH", "./chroma_db")
TEMP_KEYWORDS = ("耐温", "温度", "temperature")
TEMP_EXPANSION = "temperature range operating temperature -20 230 °C"

# 知识库注册表：顺序即前端展示顺序
LIST_META = [
    {"name": "materials", "label": "FFKM", "full": "全氟醚橡胶", "hint": "宽温域 · 耐等离子体"},
    {"name": "fkm", "label": "FKM", "full": "氟橡胶", "hint": "通用氟弹性体"},
    {"name": "peek", "label": "PEEK", "full": "聚醚醚酮", "hint": "高温工程塑料"},
]

FIELDS = [
    ("grade", "牌号"),
    ("supplier", "供应商"),
    ("composition", "组成"),
    ("temp_range", "温度范围"),
    ("cs", "压缩永久变形"),
    ("chem_resistance", "耐化学性"),
    ("test_medium", "测试介质"),
    ("test_temp", "测试温度"),
    ("test_duration", "测试时长"),
    ("confidence", "置信度"),
]

PROMPT = """你是材料数据助手。基于检索结果输出紧凑列表，不写段落、不写开场白、不写总结。
输出格式：每行一条证据
- 牌号 | 供应商 | 关键数据 | 测试条件 [编号]
规则：
1. 一行只写一条证据，字段用 " | " 分隔；缺失字段留空但保留分隔符
2. 必须使用检索结果中的原始数值，禁止换算、禁止推断
3. 同一牌号多来源冲突时另起一行，行尾标注「※ 与 [m] 冲突」
4. 引用编号必须与检索编号一致，写在行尾
5. 完全没有相关数值时，只输出：资料不足
资料:
{context}
问题: {question}"""


def expand_query(question: str) -> str:
    if any(keyword in question for keyword in TEMP_KEYWORDS):
        return f"{question} {TEMP_EXPANSION}"
    return question


def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=PERSIST)


def _llm():
    return llm_client()


def _model() -> str:
    return model_name()


def collection_stats() -> list:
    """各知识库的记录数；库不存在时计为 0，不报错。"""
    client = _client()
    out = []
    for meta in LIST_META:
        try:
            count = client.get_or_create_collection(meta["name"]).count()
        except Exception:
            count = 0
        out.append({**meta, "count": count})
    return out


def sample_records(collection: str, limit: int = 30) -> list:
    data = _client().get_or_create_collection(collection).get(limit=limit)
    rows = []
    for doc, meta in zip(data.get("documents") or [], data.get("metadatas") or []):
        rows.append({"document": doc, **{k: v for k, v in (meta or {}).items()}})
    return rows


def retrieve(question: str, collection: str = "materials", top_k: int = 5):
    store = _client().get_or_create_collection(collection)
    query = expand_query(question)
    model = (store.metadata or {}).get("embedding_model")

    if model:
        if not embeddings.ready():
            raise RuntimeError("该知识库由远程 embedding 构建，请配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL")
        result = store.query(query_embeddings=[embeddings.embed_one(query)], n_results=top_k)
    else:
        result = store.query(query_texts=[query], n_results=top_k)

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    return docs, metas


def format_context(docs, metas) -> str:
    lines = []
    for i, (doc, meta) in enumerate(zip(docs, metas), start=1):
        meta = meta or {}
        label = meta.get("grade") or meta.get("supplier") or "unknown"
        lines.append(f"[{i}] {label} | {meta.get('source') or '—'}\n{doc}")
    return "\n\n".join(lines)


def build_sources(metas) -> list:
    """结构化来源，供前端渲染卡片。"""
    items = []
    for i, meta in enumerate(metas, start=1):
        meta = meta or {}
        url = str(meta.get("source") or "")
        items.append(
            {
                "index": i,
                "label": meta.get("grade") or meta.get("supplier") or "未标注牌号",
                "supplier": meta.get("supplier") or "",
                "confidence": meta.get("confidence"),
                "url": url if url.startswith("http") else "",
                "fields": [
                    {"key": k, "label": label, "value": meta[k]}
                    for k, label in FIELDS
                    if meta.get(k) not in (None, "", [], {})
                ],
            }
        )
    return items


def stream_tokens(question: str, docs, metas):
    """逐 token 产出答案文本。"""
    stream = _llm().chat.completions.create(
        model=_model(),
        stream=True,
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(context=format_context(docs, metas), question=question),
            }
        ],
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def query(question: str, collection: str = "materials", top_k: int = 5) -> tuple:
    """一次性返回（向后兼容旧前端）。"""
    docs, metas = retrieve(question, collection, top_k)
    if not docs:
        return "未检索到相关数据", []
    answer = "".join(stream_tokens(question, docs, metas))
    sources = [
        f"[{i}] {(m or {}).get('grade') or (m or {}).get('supplier') or 'unknown'} | {(m or {}).get('source') or '—'}"
        for i, m in enumerate(metas, start=1)
    ]
    return answer, sources


# ---- 资料库管理：按来源聚合 / 查看 / 删除 / 检索调试 ----


def _rows(store) -> list:
    data = store.get(include=["metadatas"])
    return list(zip(data.get("ids") or [], data.get("metadatas") or []))


PAGE_SUFFIX = re.compile(r"\s+p\.(\d+)$")


def _split_source(value: str) -> tuple:
    """'a.pdf p.3' -> ('a.pdf', '3')"""
    text = value or "未标注来源"
    match = PAGE_SUFFIX.search(text)
    if not match:
        return text, ""
    return text[: match.start()], match.group(1)


def source_stats(collection: str = "materials") -> list:
    names = [item["name"] for item in LIST_META] if collection == "all" else [collection]
    items = []

    for name in names:
        store = _client().get_or_create_collection(name)
        groups: dict = {}
        for _id, meta in _rows(store):
            meta = meta or {}
            key, page = _split_source(str(meta.get("source") or ""))
            group = groups.setdefault(
                key, {"records": 0, "conf": 0.0, "grades": set(), "suppliers": set(), "pages": set()}
            )
            if page:
                group["pages"].add(int(page))
            group["records"] += 1
            try:
                group["conf"] += float(meta.get("confidence") or 0)
            except (TypeError, ValueError):
                pass
            if meta.get("grade_key"):
                group["grades"].add(str(meta["grade_key"]))
            if meta.get("supplier"):
                group["suppliers"].add(str(meta["supplier"]))

        for key, group in groups.items():
            items.append(
                {
                    "source": key,
                    "collection": name,
                    "records": group["records"],
                    "avg_confidence": round(group["conf"] / group["records"], 3) if group["records"] else 0,
                    "grades": sorted(group["grades"])[:3],
                    "suppliers": sorted(group["suppliers"])[:2],
                    "kind": Path(key).suffix.lstrip(".").lower() or "txt",
                    "pages": sorted(group["pages"]),
                }
            )

    return sorted(items, key=lambda x: (-x["records"], x["source"]))


def records_of(collection: str, source: str, limit: int = 200) -> list:
    store = _client().get_or_create_collection(collection)
    data = store.get(include=["metadatas", "documents"])
    rows = []
    for meta, doc in zip(data.get("metadatas") or [], data.get("documents") or []):
        meta = meta or {}
        if _split_source(str(meta.get("source") or ""))[0] != source:
            continue
        rows.append((meta, doc or ""))
        if len(rows) >= limit:
            break

    items = build_sources([meta for meta, _ in rows])
    for item, (_meta, doc) in zip(items, rows):
        item["chunk"] = doc[:800]
    return items


def delete_source(collection: str, source: str) -> int:
    store = _client().get_or_create_collection(collection)
    ids = [
        _id
        for _id, meta in _rows(store)
        if _split_source(str((meta or {}).get("source") or ""))[0] == source
    ]
    if ids:
        store.delete(ids=ids)
    return len(ids)


def search_debug(question: str, collection: str = "materials", top_k: int = 8) -> list:
    store = _client().get_or_create_collection(collection)
    text = expand_query(question)

    if embeddings.ready():
        result = store.query(query_embeddings=[embeddings.embed_one(text)], n_results=top_k)
    else:
        result = store.query(query_texts=[text], n_results=top_k)

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    hits = []
    for rank, (doc, meta, dist) in enumerate(zip(docs, metas, dists), start=1):
        meta = meta or {}
        hits.append(
            {
                "rank": rank,
                "score": round(1 / (1 + float(dist)), 4) if dist is not None else None,
                "grade": meta.get("grade_key") or meta.get("grade_raw") or "",
                "supplier": meta.get("supplier") or "",
                "source": meta.get("source") or "",
                "collection": collection,
                "snippet": (doc or "")[:240],
            }
        )
    return hits
