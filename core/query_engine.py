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
    ("material", "材质"),
    ("temp_range", "温度范围"),
    ("cs", "压缩永久变形"),
    ("chem_resistance", "耐化学性"),
    ("test_medium", "测试介质"),
    ("test_temp", "测试温度"),
    ("test_duration", "测试时长"),
    ("confidence", "置信度"),
]

PROMPT = """你是材料数据助手。检索结果已按「产品」组织，材质只是产品的属性。
基于检索结果输出紧凑列表，不写段落、不写开场白。
输出格式：每行一条证据，行首用「- 」
- 产品 | 材质 | 供应商 | 关键数据 | 测试条件 [编号]
规则：
1. 每条证据独立一行，必须以「- 」开头；字段用 " | " 分隔，缺失字段留空但保留分隔符
2. 关键数据必须带字段名，写成「压缩永久变形: 18」「耐化学性: 50」这种形式，不要只写裸数字；
   测试条件带上介质、温度、时长，温度必须带单位（如「#214 O型圈, 200°C, 70 h」）；
   耐化学性数值是「体积溶胀 %」，定性描述（优 / 良 / 差）照抄即可
3. 材质写中文（全氟醚橡胶 / 氟橡胶 / 聚醚醚酮），检索结果里是 OTHER 或未归类时写「未归类」
4. 所有数值和测试条件必须取自同一条检索结果，禁止把 A 产品的条件和 B 产品的数值拼在一起；
   禁止换算、禁止推断
5. 只有「同一产品、同一字段、同一测试条件」出现两个不同数值才算冲突，另起一行标注「※ 与 [m] 冲突」；
   不同测试介质或不同温度下的数值各自成行，不算冲突，不要标注
6. 引用编号必须与检索编号一致，写在行尾
7. 问题是「有哪些 / 列出 / 包括哪些 / 多少种」时：把检索结果里的产品逐行列出，
   同一产品的多行可合并；只要检索结果里有相关产品，就不允许回答「资料不足」
8. 问题明确要求比较或求极值（最大/最小/最高/最低/最好/最差/哪个）时：先按上面的格式列出依据，
   最后单独一行：「结论: <产品> <字段名>: <数值> [编号]」；
   问「典型值 / 常见值 / 一般是多少」时不要给结论行
9. 字段缺失就留空，不要因为缺少供应商、材质或测试条件就判定「资料不足」；
   只有检索结果与问题完全无关时，才输出：资料不足
10. 只有牌号、没有关键数据的检索结果，仅当问题问「有哪些牌号 / 型号 / 产品」时列出一行；
    其他问题不要输出这种空行
资料:
{context}
问题: {question}"""


# 常见叫法补全：用户说品牌/俗称，库里存的是归一化后的名字
ALIASES = (
    ("索尔维", "Syensqo Solvay Tecnoflon"),
    ("苏威", "Syensqo Solvay Tecnoflon"),
    ("全氟醚", "FFKM 全氟橡胶"),
    ("全氟橡胶", "FFKM"),
    ("氟橡胶", "FKM"),
)


def expand_query(question: str) -> str:
    query = question
    for alias, expansion in ALIASES:
        if alias in question:
            query = f"{query} {expansion}"
    if any(keyword in question for keyword in TEMP_KEYWORDS):
        query = f"{query} {TEMP_EXPANSION}"
    return query


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


ENUM_WORDS = ("有哪些", "列出", "包括哪些", "多少种", "都有什么", "有哪些型号", "都有哪些")

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


def _rerank_rows(question: str, rows: list):
    """rows: (doc, meta, score, order)；按牌号命中优先，其余保持原顺序。"""
    return sorted(rows, key=lambda row: (-_grade_bonus(question, row[1] or {}), row[3]))


def _diversify_rows(rows: list, top_k: int, per_source: int = 2):
    picked, rest, seen = [], [], {}
    for row in rows:
        key = _source_of(row[1])
        if seen.get(key, 0) >= per_source:
            rest.append(row)
            continue
        seen[key] = seen.get(key, 0) + 1
        picked.append(row)
    for row in rest:
        if len(picked) >= top_k:
            break
        picked.append(row)
    return picked[:top_k]


def retrieve_scored(question: str, collection: str = CATALOG, top_k: int = 5, material: str | None = None):
    """返回 (docs, metas, scores)，score = 1/(1+distance)，供失败判定使用。"""
    # 「有哪些 / 列出」这类清单问题，命中的条数往往多于 5 条，放宽召回
    if any(word in question for word in ENUM_WORDS):
        top_k = max(top_k, 12)
    store = _client().get_or_create_collection(_resolve(collection))    query = expand_query(question)
    model = (store.metadata or {}).get("embedding_model")
    where = {"material": material} if material and material not in ("all", "") else None
    if not where:
        # 问题里点名了一种材质就直接过滤，避免回答「FFKM」时混进 FKM 的数据
        # 注意：不能用子串判断，"ffkm" 里包含 "fkm"
        low = question.lower()
        named = [
            item["code"]
            for item in MATERIALS
            if re.search(rf"(?<![a-z]){item['code'].lower()}(?![a-z])", low)
        ]
        if len(named) == 1:
            where = {"material": named[0]}

    # 聚合类问题（哪些 / 最低 / 对比）需要更宽的召回
    if _is_aggregate(question):
        fetch = max(10, top_k * 2)
    else:
        fetch = max(top_k, top_k * 2)  # 为去重留余量

    kwargs = {"n_results": fetch}
    if where:
        kwargs["where"] = where

    if model or embeddings.ready():
        if not embeddings.ready():
            raise RuntimeError("该知识库由远程 embedding 构建，请配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL")
        # 集合没写 embedding_model（早期创建的空库）时也走远程 embedding，
        # 否则 Chroma 会退回内置 all-MiniLM 并尝试联网下载模型。
        result = store.query(query_embeddings=[embeddings.embed_one(query)], **kwargs)
    else:
        result = store.query(query_texts=[query], **kwargs)

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    rows = []
    for index, (doc, meta) in enumerate(zip(docs, metas)):
        distance = dists[index] if index < len(dists) else None
        score = round(1 / (1 + float(distance)), 4) if distance is not None else None
        rows.append((doc, meta, score, index))

    rows = _diversify_rows(_rerank_rows(question, rows), top_k)
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


def retrieve(question: str, collection: str = CATALOG, top_k: int = 5, material: str | None = None):
    docs, metas, _scores = retrieve_scored(question, collection, top_k, material)
    return docs, metas


def format_context(docs, metas) -> str:
    labels = dict(FIELDS)
    lines = []
    for i, (doc, meta) in enumerate(zip(docs, metas), start=1):
        meta = meta or {}
        product = meta.get("grade") or meta.get("product_name") or meta.get("supplier") or "unknown"
        material = meta.get("material_label") or meta.get("material") or ""
        # 文档里存的是英文字段名（cs / chem_resistance），换成中文再喂给模型，
        # 否则模型容易把「耐化学性 280」说成「压缩永久变形 280」
        body = []
        for raw in (doc or "").splitlines():
            key, sep, value = raw.partition(": ")
            if not sep:
                body.append(raw)
                continue
            label = labels.get(key, key)
            # 库里测试温度是纯数字（200），模型会照抄成「测试温度: 200」，补上单位
            if label == "测试温度" and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
                value = f"{value.strip()}°C"
            body.append(f"{label}: {value}")
        lines.append(f"[{i}] {product} | {material} | {meta.get('source') or '—'}\n" + "\n".join(body))
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
                "label": meta.get("grade") or meta.get("product_name") or "未标注产品",
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


def resolve_collection(name: str) -> str:
    """对外公开的归一化：材质名 / 旧库名 / 空值 → 真实集合名。"""
    return _resolve(name)


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
