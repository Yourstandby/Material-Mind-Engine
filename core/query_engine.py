"""检索 + 生成：各前端共用的核心引擎（不含任何 UI 依赖）。

用法:
    from core.query_engine import retrieve, stream_tokens
"""
import re
from pathlib import Path

import chromadb

from core import embeddings
from core import vector_store as vs
from core.config import env
from core.llm import client as llm_client, model_name

PERSIST = env("CHROMA_PATH", "./chroma_db")
CATALOG = env("CATALOG_COLLECTION", "catalog")
TEMP_KEYWORDS = ("耐温", "温度", "temperature")
TEMP_EXPANSION = "temperature range operating temperature -20 230 °C"

# 材质只是产品的一个属性，用于筛选
MATERIALS = [
    {"code": "FFKM", "label": "FFKM", "full": "全氟醚橡胶", "hint": "宽温域 · 耐等离子体"},
    {"code": "FKM", "label": "FKM", "full": "氟橡胶", "hint": "通用氟弹性体"},
    {"code": "PEEK", "label": "PEEK", "full": "聚醚醚酮", "hint": "高温工程塑料"},
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

PROMPT = """你是材料数据助手。检索结果已按「产品」组织，材质只是产品的属性。
基于检索结果输出紧凑列表，不写段落、不写开场白、不写总结。
输出格式：每行一条证据
- 产品 | 材质 | 供应商 | 关键数据 | 测试条件 [编号]
规则：
1. 一行只写一条证据，字段用 " | " 分隔；缺失字段留空但保留分隔符
2. 必须使用检索结果中的原始数值，禁止换算、禁止推断
3. 同一产品多来源冲突时另起一行，行尾标注「※ 与 [m] 冲突」
4. 引用编号必须与检索编号一致，写在行尾
5. 完全没有相关数值时，只输出：资料不足
资料:
{context}
问题: {question}"""


def expand_query(question: str) -> str:
    if any(keyword in question for keyword in TEMP_KEYWORDS):
        return f"{question} {TEMP_EXPANSION}"
    return question


def _client():
    return vs.client()


def _llm():
    return llm_client()


def _model() -> str:
    return model_name()


def material_stats() -> list:
    """按材质统计产品库，用于前端筛选标签。"""
    try:
        store = _client().get_or_create_collection(CATALOG)
        total = store.count()
    except Exception:
        store, total = None, 0

    out = [{"name": "all", "code": "all", "label": "全部", "full": "全部产品", "hint": "跨材质检索", "count": total}]
    for item in MATERIALS:
        count = 0
        if store is not None:
            try:
                count = len(store.get(where={"material": item["code"]}).get("ids") or [])
            except Exception:
                count = 0
        out.append({**item, "name": item["code"], "count": count})
    return out


def collection_stats() -> list:
    """兼容旧接口，返回材质视图。"""
    return material_stats()


def sample_records(collection: str, limit: int = 30) -> list:
    data = _client().get_or_create_collection(collection).get(limit=limit)
    rows = []
    for doc, meta in zip(data.get("documents") or [], data.get("metadatas") or []):
        rows.append({"document": doc, **{k: v for k, v in (meta or {}).items()}})
    return rows


AGGREGATE = ("哪些", "哪个", "最高", "最低", "最大", "最小", "对比", "比较", "所有", "全部", "分别")


def _is_aggregate(question: str) -> bool:
    return any(word in question for word in AGGREGATE)


def _source_of(meta: dict) -> str:
    return str((meta or {}).get("source") or "")


def _grade_bonus(question: str, meta: dict) -> int:
    """问题里点名的牌号优先：命中则提权。"""
    q = re.sub(r"[\s\-_]+", "", question.lower())
    best = 0
    for key in ("grade", "grade_key", "product_name"):
        name = meta.get(key)
        if not name:
            continue
        raw = re.sub(r"[®™]", "", str(name)).strip()
        words = raw.split()
        candidates = {raw}
        for n in (1, 2, 3):  # 同时匹配省略品牌前缀的写法：PR 100 / 100
            if len(words) >= n:
                candidates.add(" ".join(words[-n:]))
        for cand in candidates:
            token = re.sub(r"[\s\-_]+", "", cand.lower())
            if len(token) >= 3 and token in q:
                best = max(best, 100 + len(token))
    return best


def _rerank(question: str, docs, metas):
    order = sorted(
        range(len(docs)),
        key=lambda i: (-_grade_bonus(question, metas[i] or {}), i),
    )
    return [docs[i] for i in order], [metas[i] for i in order]


def _diversify(docs, metas, top_k: int, per_source: int = 2):
    """同一来源最多保留 per_source 条；不足 top_k 时再用剩余结果补齐。"""
    picked_docs, picked_metas = [], []
    seen: dict[str, int] = {}
    rest_docs, rest_metas = [], []

    for doc, meta in zip(docs, metas):
        key = _source_of(meta)
        if seen.get(key, 0) >= per_source:
            rest_docs.append(doc)
            rest_metas.append(meta)
            continue
        seen[key] = seen.get(key, 0) + 1
        picked_docs.append(doc)
        picked_metas.append(meta)

    for doc, meta in zip(rest_docs, rest_metas):
        if len(picked_docs) >= top_k:
            break
        picked_docs.append(doc)
        picked_metas.append(meta)

    return picked_docs[:top_k], picked_metas[:top_k]


def retrieve(question: str, collection: str = CATALOG, top_k: int = 5, material: str | None = None):
    store = _client().get_or_create_collection(collection)
    query = expand_query(question)
    model = (store.metadata or {}).get("embedding_model")
    where = {"material": material} if material and material not in ("all", "") else None

    # 聚合类问题（哪些 / 最低 / 对比）需要更宽的召回
    fetch = top_k
    if _is_aggregate(question):
        fetch = max(10, top_k * 2)
    else:
        fetch = max(fetch, top_k * 2)  # 为去重留余量

    kwargs = {"n_results": fetch}
    if where:
        kwargs["where"] = where

    if model:
        if not embeddings.ready():
            raise RuntimeError("该知识库由远程 embedding 构建，请配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL")
        result = store.query(query_embeddings=[embeddings.embed_one(query)], **kwargs)
    else:
        result = store.query(query_texts=[query], **kwargs)

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    docs, metas = _rerank(question, docs, metas)
    return _diversify(docs, metas, top_k)


def format_context(docs, metas) -> str:
    lines = []
    for i, (doc, meta) in enumerate(zip(docs, metas), start=1):
        meta = meta or {}
        product = meta.get("product_name") or meta.get("grade") or meta.get("supplier") or "unknown"
        material = meta.get("material_label") or meta.get("material") or ""
        lines.append(f"[{i}] {product} | {material} | {meta.get('source') or '—'}\n{doc}")
    return "\n\n".join(lines)


def build_sources(metas) -> list:
    """结构化来源，供前端渲染卡片。"""
    items = []
    for i, meta in enumerate(metas, start=1):
        meta = meta or {}
        url = str(meta.get("source") or "")
        page = ""
        match = re.search(r"\bp\.(\d+)$", url)
        if match:
            page = match.group(1)
        items.append(
            {
                "index": i,
                "label": meta.get("product_name") or meta.get("grade") or "未标注产品",
                "product_id": meta.get("product_id") or "",
                "material": meta.get("material") or "",
                "material_label": meta.get("material_label") or "",
                "supplier": meta.get("supplier") or "",
                "confidence": meta.get("confidence"),
                "doc_id": meta.get("doc_id") or "",
                "page": page,
                "source": url,
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


def query(question: str, collection: str = CATALOG, top_k: int = 5, material: str | None = None) -> tuple:
    """一次性返回（向后兼容旧前端）。"""
    docs, metas = retrieve(question, collection, top_k, material)
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


def _resolve(name: str) -> str:
    """旧前端可能传材质名或旧库名，统一落到产品库。"""
    codes = {item["code"] for item in MATERIALS}
    legacy = {"materials", "peek", "fkm"}
    if not name or name == "all" or str(name).upper() in codes or str(name).lower() in legacy:
        return CATALOG
    return name


def source_stats(collection: str = CATALOG) -> list:
    names = [_resolve(collection)]
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
    store = _client().get_or_create_collection(_resolve(collection))
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
    store = _client().get_or_create_collection(_resolve(collection))
    ids = [
        _id
        for _id, meta in _rows(store)
        if _split_source(str((meta or {}).get("source") or ""))[0] == source
    ]
    if ids:
        store.delete(ids=ids)
    return len(ids)


def search_debug(question: str, collection: str = CATALOG, top_k: int = 8) -> list:
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
                "product_id": meta.get("product_id") or "",
                "product_name": meta.get("product_name") or "",
                "material": meta.get("material") or "",
                "supplier": meta.get("supplier") or "",
                "source": meta.get("source") or "",
                "collection": collection,
                "snippet": (doc or "")[:240],
            }
        )
    return hits


def product_stats(material: str | None = None) -> list:
    """按产品聚合：一个产品 = 供应商 + 牌号，材质是它的属性。"""
    try:
        store = _client().get_or_create_collection(CATALOG)
        data = store.get(include=["metadatas"])
    except Exception:
        return []

    groups: dict = {}
    for meta in data.get("metadatas") or []:
        meta = meta or {}
        if material and material not in ("all", "") and meta.get("material") != material:
            continue
        pid = str(meta.get("product_id") or meta.get("product_name") or "unknown")
        group = groups.setdefault(
            pid,
            {
                "product_id": pid,
                "product_name": meta.get("product_name") or "未标注产品",
                "material": meta.get("material") or "",
                "material_label": meta.get("material_label") or "",
                "supplier": meta.get("supplier") or "",
                "records": 0,
                "grades": set(),
                "sources": set(),
            },
        )
        group["records"] += 1
        if meta.get("grade"):
            group["grades"].add(str(meta["grade"]))
        base = str(meta.get("source") or "").split(" p.")[0]
        if base:
            group["sources"].add(base)

    out = []
    for group in groups.values():
        group["grades"] = sorted(group["grades"])[:4]
        group["sources"] = sorted(group["sources"])
        group["documents"] = len(group["sources"])
        out.append(group)
    return sorted(out, key=lambda g: (-g["records"], g["product_name"]))


def doc_products() -> dict:
    """doc_id → 产品信息，供资料库展示。"""
    try:
        data = _client().get_or_create_collection(CATALOG).get(include=["metadatas"])
    except Exception:
        return {}
    mapping: dict = {}
    for meta in data.get("metadatas") or []:
        meta = meta or {}
        did = meta.get("doc_id")
        if not did or did in mapping:
            continue
        mapping[did] = {
            "product_id": meta.get("product_id") or "",
            "product_name": meta.get("product_name") or "",
            "material": meta.get("material") or "",
            "material_label": meta.get("material_label") or "",
        }
    return mapping
