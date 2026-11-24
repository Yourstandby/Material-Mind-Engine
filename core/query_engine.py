"""检索 + 生成：各前端共用的核心引擎（不含任何 UI 依赖）。

用法:
    from core.query_engine import retrieve, stream_tokens
"""
import functools
import re
import time
from pathlib import Path

import chromadb

from core import catalog
from core import embeddings
from core import access
from core import applications
from core import hybrid
from core import vector_store as vs
from core.config import env
from core.llm import client as llm_client, model_name, reasoning_effort
from core import values as value_parser
from core.normalize import localize

PERSIST = env("CHROMA_PATH", "./chroma_db")
CATALOG = env("CATALOG_COLLECTION", "catalog")

# 能力型/元问题：确定性路由，不进检索也不花一次 LLM 调用。
# 先剔掉口语前缀（“那你现在是谁”里的“那”），再匹配；只在问题够短且不含技术名词时生效，
# 避免误伤「你能查 FFKM 耐温吗」这类真实提问。
META_PREFIX = re.compile(r"^(?:那么|那|所以|然后|所以呢|请问|请|嗯|哦|好的|好)")
META_PATTERN = re.compile(
    r"^(?:你|您)(?:现在|到底|究竟|还)?(?:是|叫|算)(?:谁|什么|哪个|什么名字|干什么的)?"
    r"|^(?:你|您)?(?:能|会|可以|可以帮|能帮)(?:做|作|干|查|提供|帮|给)(?:什么|哪些|我什么)"
    r"|^(?:你|您)(?:现在|到底)?(?:在)?(?:干|做)(?:什么|嘛)"
    r"|怎么(?:用|使用)|^使用说明|^帮助|^help$|^能查(?:什么|哪些)|^支持(?:什么|哪些)|^有什么功能"
)
META_SKIP = ("耐温", "密度", "硬度", "牌号", "对比", "压缩", "介质", "拉伸", "密封")
# 自我指涉：绝不允许被当成「查了哪些产品」而输出对比表格
SELF_REFERENCE = (
    "你是谁", "你叫什么", "你是什么", "你是哪个", "你现在是谁", "你会什么",
    "你能做什么", "你能作什么", "你是干什么", "你算谁",
)
CAPABILITY_TEXT = """我是 Material Mind，查的是本地材料库，不是通用问答。

**能做的三件事**

1. 查数值 —— 「FFKM 耐温多少」「PEEK 的密度」
2. 比产品 —— 「NOK 和 Solvay 的 FFKM 耐温对比」，直接出表，数值取自资料
3. 追原文 —— 「这条数据出自哪份文件」，可跳到对应页码

**边界**

只基于库内资料回答；查不到就直说「资料不足」，不猜，也不拿别家产品顶替。"""


def meta_reply(question: str) -> str | None:
    """能力型/元问题直接返回固定文案；不是这类问题就返回 None。"""
    text = re.sub(r"[\s?？。!！,.，、]+", "", question or "").lower()
    text = META_PREFIX.sub("", text)
    if not text or len(text) > 20:
        return None
    if any(word in text for word in META_SKIP):
        return None
    return CAPABILITY_TEXT if META_PATTERN.search(text) else None


def asks_self(question: str) -> bool:
    """问的是助手自身？用于拦住“答非所问”的结构化表格。"""
    text = _tidy(question)
    return any(word in text for word in SELF_REFERENCE)

TEMP_KEYWORDS = ("耐温", "温度", "temperature")
TEMP_EXPANSION = "temperature range operating temperature -20 230 °C"

# 检索与导航的主单位是产品（供应商 + 牌号），材质只作为产品的属性字段

# 问题里点名的厂商：命中后直接按 supplier 过滤，避免拿别家产品顶替
SUPPLIER_WORDS = (
    ("NOK", ("nok", "エヌオーケー")),
    ("Syensqo", ("syensqo", "solvay", "索尔维", "苏威", "tecnoflon")),
    ("Daikin Industries", ("daikin", "大金")),
    ("Chemours", ("chemours", "科慕")),
    ("Parker Hannifin", ("parker", "派克")),
    ("Greene Tweed", ("greene tweed",)),
    ("Mitsui Chemicals", ("mitsui", "三井")),
    ("NICHIAS", ("nichias", "ニチアス")),
    ("Trelleborg", ("trelleborg", "特瑞堡")),
)


def named_suppliers(question: str) -> list:
    """问题里提到的厂商（英文按词边界判断，避免 knock 里的 nok 误命中）。

    先查静态词表 SUPPLIER_WORDS，再拿库内真实出现过的供应商名（known_suppliers）
    补漏 —— 新供应商入库后不需要再来改代码。
    """
    low = (question or "").lower()
    if not low:
        return []
    names = []
    for name, keys in SUPPLIER_WORDS:
        for key in keys:
            if key.isascii():
                if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", low):
                    names.append(name)
                    break
            elif key in low:
                names.append(name)
                break
    for name in known_suppliers():
        if _mentions(low, name) and not any(_mentions(name.lower(), hit) for hit in names):
            names.append(name)
    return names


# 库内供应商词表缓存：(时间戳, 名称列表)
_SUPPLIER_CACHE: tuple[float, list[str]] = (0.0, [])
SUPPLIER_CACHE_TTL = 600.0
SUPPLIER_COMPANY_SUFFIX = ("有限公司", "株式会社", "公司", " corporation", " corp", " co.", " ltd", " inc", " gmbh", " s.r.l.", " b.v.")


@functools.lru_cache(maxsize=512)
def _mention_variants(name: str) -> tuple[str, ...]:
    """一个供应商名在问题里可能的写法：全名 + 去掉公司后缀的短名。"""
    base = name.strip().lower()
    if not base:
        return ()
    variants = [base]
    shortened = base
    for suffix in SUPPLIER_COMPANY_SUFFIX:
        if shortened.endswith(suffix):
            shortened = shortened[: -len(suffix)].strip(" ,.")
    if shortened and shortened != base and len(shortened) >= 2:
        variants.append(shortened)
    return tuple(variants)


def _mentions(text_low: str, name: str) -> bool:
    """text_low 里是否提到了这个供应商（英文按词边界，中文直接包含）。"""
    for variant in _mention_variants(name):
        if variant.isascii():
            if len(variant) < 3:
                continue
            if re.search(rf"(?<![a-z]){re.escape(variant)}(?![a-z])", text_low):
                return True
        elif variant in text_low:
            return True
    return False


def known_suppliers(limit: int = 4000, ttl: float = SUPPLIER_CACHE_TTL) -> list[str]:
    """库内实际出现过的供应商名（动态词表，进程内缓存 TTL 秒）。

    未命中看板靠它把「谁家的资料还没有」抽出来，不用维护一份手写厂商清单。
    取不到库时返回空表，不影响问答主链路。
    """
    global _SUPPLIER_CACHE
    now = time.time()
    cached_at, cached = _SUPPLIER_CACHE
    if cached and now - cached_at < ttl:
        return cached
    names: set[str] = set()
    try:
        col = vs.collection(CATALOG)
        offset = 0
        page = 1000
        while len(names) < limit and offset < limit * 2:
            batch = col.get(include=["metadatas"], limit=page, offset=offset)
            metas = batch.get("metadatas") or []
            if not metas:
                break
            for meta in metas:
                name = str((meta or {}).get("supplier") or "").strip()
                if 2 <= len(name) <= 40:
                    names.add(name)
            if len(metas) < page:
                break
            offset += page
    except Exception:  # noqa: BLE001 - 词表取不到就退回静态表，不能拖垮问答
        names = set()
    result = sorted(names)
    _SUPPLIER_CACHE = (now, result)
    return result


# 牌号形态：字母开头、含数字（CHEMRAZ 640 / Tecnoflon P 959 / PEEK-450G）
GRADE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]*(?:[-\s][A-Za-z0-9.]+){0,2}\s?\d[A-Za-z0-9\-.]*)")


def named_grades(question: str) -> list[str]:
    """问题里点名的牌号/型号（用于未命中看板反推「该补哪个产品」）。"""
    seen = []
    for match in GRADE_RE.finditer(question or ""):
        word = match.group(1).strip()
        if len(word) < 3:
            continue
        if word.lower() not in [x.lower() for x in seen]:
            seen.append(word)
    return seen


FIELDS = [
    ("grade", "牌号"),
    ("supplier", "供应商"),
    ("composition", "组成"),
    ("material", "材质"),
    ("application", "应用场景"),
    ("industries", "行业"),
    ("parts", "部件"),
    ("media", "介质"),
    ("certs", "认证"),
    ("certifications", "认证"),
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
输出格式：每行一条证据，行首用「- 」，共 5 个位置，顺序固定为
  位置1（牌号/产品） | 位置2（材质） | 位置3（供应商） | 位置4（关键数据） | 位置5（测试条件） [编号]
示例（只是格式示范，内容必须换成检索结果里的真实值）：
- Tecnoflon P 959 | 氟橡胶 | Solvay | 压缩永久变形: 18 | #214 O型圈, 200°C, 70 h [2]
规则：
0. 上面「位置1…位置5」和示例仅是格式说明，不是要输出的内容。严禁把「产品 / 材质 /
   供应商 / 关键数据 / 测试条件」这类字段名本身当成单元格内容，每个格子里必须是检索结果里的真实文字或数字；
   如果某个字段在检索结果里没有，就按规则 9 留空
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
11. 检索结果可能是日语或英语原文：一律用中文输出（牌号、型号、化学式保留原文），
    不要出现假名；拿不准的日语词按上下文翻译成中文
12. 问题里提到的品牌/厂商如果在检索结果里完全找不到，直接说「库中没有该品牌的资料」，
    不要用其他厂商的产品代替
13. 上下文里标了「（未标注）」或带「没标明产品牌号」提示的条目不是产品，不要输出成证据行——
    用户看的是产品，「未标注」刷屏比不回答更糟；这类条目置信度本来就低。
    只有当问题明确问「资料/论文/研究里怎么说」时，才引用它并注明来源；否则宁可少列几行
14. 完全相同的行只输出一次；同一产品 + 同一字段 + 同一测试条件的重复值合并成一行，
    不要同一行罗列 8 遍
15. 「供应商」「行业」「部件」不是一回事：上下文里「行业与部件」那一行只能用于判断应用场景，
    不能当供应商写进第三格；供应商无值就留空
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
    # 应用向提问补全英文/同义写法，英语资料也能命中
    low = question.lower()
    for alias, expansion in applications.QUERY_ALIASES:
        if alias in low:
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


def product_options(allowed=None) -> list:
    """前端导航/筛选用的产品清单：一个产品 = 供应商 + 牌号。"""
    items = product_stats(allowed=allowed)
    total = sum(item["records"] for item in items)
    out = [
        {
            "name": "all",
            "product_id": "all",
            "label": "全部产品",
            "full": "按产品浏览",
            "hint": "跨产品检索",
            "count": total,
        }
    ]
    for item in sorted(items, key=lambda p: p["product_name"]):
        if item["product_id"] == catalog.LITERATURE:
            out.append(
                {
                    "name": item["product_id"],
                    "product_id": item["product_id"],
                    "label": catalog.LITERATURE,
                    "full": " · ".join(
                        part for part in ("论文 / 专利 / 报告", item["material_label"]) if part
                    ),
                    "hint": "论文、专利、研究报告里无法归属到具体产品的数据",
                    "count": item["records"],
                }
            )
            continue
        detail = " · ".join(
            part
            for part in (
                item["supplier"],
                "、".join(item.get("industries") or []) or None,
                item["material_label"],
            )
            if part
        )
        out.append(
            {
                "name": item["product_id"],
                "product_id": item["product_id"],
                "label": item["product_name"],
                "full": detail or "未标注产品",
                "hint": detail,
                "count": item["records"],
            }
        )
    return out


def sample_records(collection: str, limit: int = 30, allowed=None) -> list:
    data = _client().get_or_create_collection(collection).get(limit=limit)
    rows = []
    for doc, meta in zip(data.get("documents") or [], data.get("metadatas") or []):
        if not access.meta_allowed(meta, allowed):
            continue
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
    """rows: (doc, meta, score, order)。

    排序优先级：
        1. 问题点名了牌号，该牌号排最前（_grade_bonus）；
        2. 有牌号/产品的记录排在「未标注产品」前面——没有产品名的记录往往来自
           论文或综述，把它当产品证据会让用户看到一堆「未标注产品」；
        3. 其余保持原顺序。
    “哪些/列出”这类枚举问题不降权，否则会把收不到牌号的资料也挤掉。
    """
    if _is_aggregate(question) and any(word in question for word in ENUM_WORDS):
        return sorted(rows, key=lambda row: (-_grade_bonus(question, row[1] or {}), row[3]))
    return sorted(
        rows,
        key=lambda row: (
            -_grade_bonus(question, row[1] or {}),
            _unnamed(row[1] or {}),
            # 问产品时文献排后；问资料/论文时文献排前
            _literature(row[1] or {}) if _asks_document(question) else -_literature(row[1] or {}),
            -round(_confidence(row[1] or {}) * 10),
            row[3],
        ),
    )


def _doc_type(meta: dict) -> str:
    return str((meta or {}).get("doc_type") or "")


def _title(meta: dict) -> str:
    meta = meta or {}
    return catalog.product_title(
        meta.get("supplier") or "",
        meta.get("grade") or "",
        meta.get("product_name") or "",
        _doc_type(meta),
    )


def _is_product(meta: dict) -> bool:
    title = _title(meta)
    return not catalog.is_unnamed(title) and title != catalog.LITERATURE


def _literature(meta: dict) -> int:
    return 1 if _title(meta) == catalog.LITERATURE else 0


def _unnamed(meta: dict) -> int:
    """1 = 不是产品（含论文/专利这类文献）；0 = 有牌号或具体产品。"""
    return 0 if _is_product(meta) else 1


def _confidence(meta: dict) -> float:
    """产品身份不明的记录，即使 LLM 自报高分也不能当强证据。"""
    meta = meta or {}
    try:
        value = float(meta.get("confidence") or 0)
    except (TypeError, ValueError):
        value = 0.0
    identity = meta.get("identity_confidence")
    if identity is None:
        identity = catalog.identity_confidence(
            meta.get("supplier") or "",
            meta.get("grade") or "",
            meta.get("product_name") or "",
            _doc_type(meta),
        )
    try:
        identity = float(identity)
    except (TypeError, ValueError):
        identity = 1.0
    return min(value, identity)


DOC_WORDS = ("论文", "文献", "资料", "研究", "报告", "原文", "出处")


def _asks_document(question: str) -> bool:
    return any(word in question for word in DOC_WORDS)


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


def _stage_fn(on_stage):
    """把检索阶段回调包成不会抛的打点函数；没传回调就空转。"""

    def emit(sid, label, state="done", detail="", ms=None):
        if on_stage is None:
            return
        try:
            on_stage({"id": sid, "label": label, "state": state, "detail": detail, "ms": ms})
        except Exception:  # noqa: BLE001 - 打点失败不能影响检索
            pass

    return emit


def _hybrid_merge(question, collection_name, rows, ids, suppliers=None, access_allowed=None,
                  on_stage=None):
    """稠密候选 + BM25 候选 → RRF 融合 → 可选 cross-encoder 重排。

    任何一步失败都原样返回稠密结果：词法索引损坏不应该让检索整体挂掉。
    """
    stage = _stage_fn(on_stage)
    if not hybrid.enabled():
        stage("bm25", "BM25 词法召回", "skip", "HYBRID_ENABLED=0")
        return rows
    if not rows:
        return rows
    lexical_started = time.perf_counter()
    try:
        lexical = hybrid.bm25(collection_name, question, limit=hybrid.bm25_candidates())
    except Exception:  # noqa: BLE001 - 索引不可用时退回纯向量
        stage("bm25", "BM25 词法召回", "skip", "索引不可用")
        return rows
    if suppliers:
        lexical = [
            hit for hit in lexical
            if str((hit["meta"] or {}).get("supplier") or "") in suppliers
        ]
    if access_allowed is not None:
        lexical = [hit for hit in lexical if access.meta_allowed(hit["meta"], access_allowed)]
    if not lexical:
        stage("bm25", "BM25 词法召回", "done", "0 候选",
              int((time.perf_counter() - lexical_started) * 1000))
        stage("rrf", f"RRF 融合 k={hybrid.rrf_k()}", "skip", "无词法候选")
        stage("rerank", "cross-encoder 重排", "skip", "仅向量结果")
        return rows
    stage("bm25", "BM25 词法召回", "done", f"{len(lexical)} 候选",
          int((time.perf_counter() - lexical_started) * 1000))

    pool = {rid: row[:3] for rid, row in zip(ids, rows) if rid}
    for hit in lexical:
        pool.setdefault(hit["id"], (hit["text"], hit["meta"], None))

    vector_weight, lexical_weight = hybrid.weights()
    fuse_started = time.perf_counter()
    fused = hybrid.rrf(
        [list(ids), [hit["id"] for hit in lexical]],
        k=hybrid.rrf_k(),
        weights=[vector_weight, lexical_weight],
    )
    merged = []
    for rank, (rid, _score) in enumerate(fused):
        row = pool.get(rid)
        if row is None:
            continue
        merged.append((row[0], row[1] or {}, row[2], rank))
    if not merged:
        return rows
    stage("rrf", f"RRF 融合 k={hybrid.rrf_k()}", "done", f"{len(merged)} 合并",
          int((time.perf_counter() - fuse_started) * 1000))
    if hybrid.rerank_enabled():
        rerank_started = time.perf_counter()
        before = len(merged)
        merged = hybrid.rerank_rows(question, merged)
        stage("rerank", "cross-encoder 重排", "done", f"{before} 条候选",
              int((time.perf_counter() - rerank_started) * 1000))
    else:
        stage("rerank", "cross-encoder 重排", "skip", "RERANK_BASE_URL 未配置")
    return merged


def retrieve_scored(
    question: str,
    collection: str = CATALOG,
    top_k: int = 5,
    material: str | None = None,
    product: str | None = None,
    industry: str | None = None,
    part: str | None = None,
    medium: str | None = None,
    allowed=None,
    on_stage=None,
):
    """返回 (docs, metas, scores)，score = 1/(1+distance)，供失败判定使用。

    allowed: 可读 doc_id 集合（游客分级用）；None 表示不限制。
    on_stage: 可选回调，接收阶段事件 {id,label,state,detail,ms}。
    """
    stage = _stage_fn(on_stage)
    # 「有哪些 / 列出」这类清单问题，命中的条数往往多于 5 条，放宽召回
    if any(word in question for word in ENUM_WORDS):
        top_k = max(top_k, 12)
    store = _client().get_or_create_collection(_resolve(collection))

    query = expand_query(question)
    model = (store.metadata or {}).get("embedding_model")
    vector_label = f"向量召回 · {model or '默认嵌入'}"
    vector_started = time.perf_counter()
    stage("vector", vector_label, "run")
    # 主筛选维度是产品；material 仅供离线评测按属性切片，不再作为分类导航
    if product and product not in ("all", ""):
        where = {"product_id": product}
    elif material and material not in ("all", ""):
        where = {"material": material}
    else:
        where = None

    # 聚合类问题（哪些 / 最低 / 对比）需要更宽的召回
    if _is_aggregate(question):
        fetch = max(10, top_k * 2)
    else:
        fetch = max(top_k, top_k * 2)  # 为去重留余量

    # 应用筛选在候选集上按元数据过滤（Chroma 对 "|" 拼接的多值字段没有 contains）
    app_filter = any(x and x not in ("all", "") for x in (industry, part, medium))
    if app_filter:
        fetch = max(fetch, min(60, top_k * 8))

    # 问题点名厂商（NOK / 索尔维 …）时按供应商过滤，不能用别家的数据顶替
    suppliers = named_suppliers(question)
    if suppliers:
        supplier_where = {"supplier": {"$in": suppliers}}
        where = supplier_where if not where else {"$and": [where, supplier_where]}

    # 分级：游客只召回可读文档的 chunk（向量库里没有文档级元数据，按 doc_id 白名单）
    where = access.merge_where(where, access.where(allowed))

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

    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    stage("vector", vector_label, "done", f"{len(docs)} 候选",
          int((time.perf_counter() - vector_started) * 1000))

    # 过滤在构造阶段做，行与 id 保持一一对应（融合要按 id 对齐两路候选）
    rows, kept_ids = [], []
    for index, (doc, meta) in enumerate(zip(docs, metas)):
        distance = dists[index] if index < len(dists) else None
        score = round(1 / (1 + float(distance)), 4) if distance is not None else None
        meta = meta or {}
        if allowed is not None and not access.meta_allowed(meta, allowed):
            continue
        if app_filter and not applications.matches(meta, industry or "", part or "", medium or ""):
            continue
        rows.append((doc, meta, score, index))
        kept_ids.append(str(ids[index]) if index < len(ids) else "")

    rows = _hybrid_merge(
        question,
        _resolve(collection),
        rows,
        kept_ids,
        suppliers=suppliers,
        access_allowed=allowed,
        on_stage=on_stage,
    )
    rows = _diversify_rows(_rerank_rows(question, rows), top_k)
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


def retrieve(
    question: str,
    collection: str = CATALOG,
    top_k: int = 5,
    material: str | None = None,
    product: str | None = None,
    industry: str | None = None,
    part: str | None = None,
    medium: str | None = None,
    allowed=None,
):
    docs, metas, _scores = retrieve_scored(
        question, collection, top_k, material, product, industry, part, medium, allowed
    )
    # 没有产品名的条目（多见于综述/论文）不是「产品」，不应占着证据位置。
    # 只有当问题明确在问资料/文献时才保留；否则过滤，全滤掉时再退回原结果避免答不出来。
    if docs and not _asks_document(question):
        keep = [(d, m) for d, m in zip(docs, metas) if not _unnamed(m or {})]
        if keep:
            docs = [d for d, _m in keep]
            metas = [m for _d, m in keep]
    return docs, metas


def format_context(docs, metas) -> str:
    """拼检索上下文。

    注意：这里**不能用 «|» 分隔字段**。下游 PROMPT 要求模型用 «|» 分隔输出行，
    上下文里再用同一个符号，模型就会把这里的「行业」当成「供应商」照搬，
    实测出现过供应商列显示成「化工」、产品列全是「未标注产品」的回答。
    改成一字段一行的显式形式，字段名与输出格式对齐。
    """
    labels = dict(FIELDS)
    lines = []
    for i, (doc, meta) in enumerate(zip(docs, metas), start=1):
        meta = meta or {}
        title = _title(meta)
        product = "（未标注）" if catalog.is_unnamed(title) else title
        material = meta.get("material_label") or meta.get("material") or ""
        head = [f"[{i}] 产品: {product}"]
        if title == catalog.LITERATURE:
            head.append("    注意: 这是论文/专利等文献记录，可以作为文献证据引用，但不能当成某个产品的参数")
        elif catalog.is_unnamed(title):
            head.append("    注意: 这条没标明产品牌号，不属于任何具体产品，不能当某个产品的证据")
        if material:
            head.append(f"    材质: {material}")
        if meta.get("supplier"):
            head.append(f"    供应商: {meta.get('supplier')}")
        scope = " / ".join(
            str(value) for value in (meta.get("industries"), meta.get("parts")) if value
        ).replace("|", "、")
        if scope:
            head.append(f"    行业与部件: {scope}")
        head.append(f"    来源: {meta.get('source') or '—'}")

        # 文档里存的是英文字段名（cs / chem_resistance），换成中文再喂给模型，
        # 否则模型容易把「耐化学性 280」说成「压缩永久变形 280」
        body = []
        for raw in (doc or "").splitlines():
            key, sep, value = raw.partition(": ")
            if not sep:
                body.append("    " + raw)
                continue
            label = labels.get(key, key)
            # 库里测试温度是纯数字（200），模型会照抄成「测试温度: 200」，补上单位
            if label == "测试温度" and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
                value = f"{value.strip()}°C"
            body.append(f"    {label}: {localize(value).replace('|', '、')}")
        lines.append("\n".join(head + body))
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
                "label": _title(meta),
                "product_id": meta.get("product_id") or "",
                "material": meta.get("material") or "",
                "material_label": meta.get("material_label") or "",
                "application": meta.get("application_text") or "",
                "industries": meta.get("industries") or "",
                "parts": meta.get("parts") or "",
                "media": meta.get("media") or "",
                "certs": meta.get("certs") or "",
                "supplier": meta.get("supplier") or "",
                "confidence": meta.get("confidence"),
                "identity_confidence": meta.get("identity_confidence"),
                "doc_id": meta.get("doc_id") or "",
                "page": page,
                "source": url,
                "url": url if url.startswith("http") else "",
                "fields": [
                    {
                        "key": k,
                        "label": label,
                        "value": localize(meta[k]) if isinstance(meta[k], str) else meta[k],
                    }
                    for k, label in FIELDS
                    if meta.get(k) not in (None, "", [], {})
                ],
            }
        )
    return items


def stream_tokens(question: str, docs, metas, reasoning: str | None = None):
    """逐 token 产出答案文本。

    reasoning: 思考强度（minimal/low/medium/high），None 用环境变量默认值。

    必须 finally close：前端点「停止」或断网时，这个生成器会被丢弃，
    不主动关流的话上游连接会挂到超时，网关的并发槽位被一直占着（实测只有 2 个），
    后续请求全部 429「并发超限」。
    """
    effort = (reasoning or reasoning_effort() or "").strip().lower()
    extra = {"reasoning_effort": effort} if effort else {}
    stream = _llm().chat.completions.create(
        model=_model(),
        stream=True,
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(context=format_context(docs, metas), question=question),
            }
        ],
        extra_body=extra or None,
    )
    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001 - 已经在关流了，不能再抛
            pass


def query(
    question: str,
    collection: str = CATALOG,
    top_k: int = 5,
    material: str | None = None,
    product: str | None = None,
    allowed=None,
) -> tuple:
    """一次性返回（向后兼容旧前端）。"""
    direct = answer_by_products(question, collection, product=product, allowed=allowed)
    if direct:
        sources = [
            f"[{item['index']}] {item['label']} | {item['source'] or '—'}"
            for item in direct["sources"]
        ]
        return direct["text"], sources
    docs, metas = retrieve(question, collection, top_k, material, product, allowed=allowed)
    if not docs:
        return "未检索到相关数据", []
    answer = "".join(stream_tokens(question, docs, metas))
    sources = [
        f"[{i}] {(m or {}).get('grade') or (m or {}).get('supplier') or 'unknown'} | {(m or {}).get('source') or '—'}"
        for i, m in enumerate(metas, start=1)
    ]
    return answer, sources


# ---- 目标产品解析 + 结构化对比（不经过 LLM） ----
#
# 回答前先把问题里的实体解析成库里的目标产品，优先级：牌号 > 供应商 > 材质 > 应用。
# 命中就直接按产品出对比表：数值全部来自库，可复现、可核对引用；
# 一个产品都解析不出来，才交给 retrieve + LLM 兜底。
# 否则模型会把「FFKM 材质」这类属性问题误判成「库中没有该品牌的资料」。

COLUMN_ORDER = (
    "temp_range",
    "cs",
    "chem_resistance",
    "test_medium",
    "test_temp",
    "test_duration",
    "application",
    "industry",
    "part",
    "medium",
    "certifications",
    "composition",
    "material",
)
MAX_PRODUCTS = 12
MAX_COLUMNS = 6
MAX_CELL_VALUES = 3
# 同一产品最多展示几条库内记录（每条 = 一个测试条件），超出的在标题里体现
MAX_ROWS_PER_PRODUCT = 3

# 问题提到某类数据时，把它排到表格前几列（表格宽度有限）
FIELD_HINTS = (
    (re.compile(r"耐温|温度|temperature", re.I), ("temp_range", "test_temp", "test_duration")),
    (re.compile(r"压缩|变形|compression|\bcs\b", re.I), ("cs", "test_temp", "test_duration")),
    (re.compile(r"耐化学|溶胀|化学|chem", re.I), ("chem_resistance", "test_medium", "test_temp")),
    (re.compile(r"认证|food|fda|usp|iso", re.I), ("certifications",)),
)

COMPARE_WORDS = ("对比", "比较", "哪个", "最高", "最低", "最大", "最小", "最好", "最差", "分别", "排序")
MAX_UP_WORDS = ("最高", "最大", "最好", "最强")
MAX_DOWN_WORDS = ("最低", "最小", "最差", "最弱")
# 「差距 / 差多少」问的是两个产品之间差多少；「跨度 / 范围多宽」问的是一个产品自身的区间宽度。
# 两种语义都可以叫「差距」，所以先看有几个产品再定，别一律按单产品区间算。
GAP_WORDS = ("差距", "相差", "差多少", "差几", "高多少", "低多少")
SPAN_WORDS = ("跨度", "范围多", "区间多", "范围有", "区间有")
# 卡片骨架列：问题没点名也要建，否则卡片出来是残的
SKELETON = ("temp_range", "test_temp", "test_duration")

# 出现在配方段落里的组分，不是可比较的产品
NON_PRODUCT_MARKS = (
    "carbon black", "炭黑", "peroxide", "过氧化物", "coagent", "助剂", "elastomag",
    "氧化镁", "交联剂", "促进剂", "填料", "增塑剂", "硫化剂", "physical properties",
)

REASON_LABELS = {
    "grade": "按牌号",
    "supplier": "按供应商",
    "material": "按材质",
    "application": "按应用",
    "product": "当前筛选产品",
}


def _tidy(text) -> str:
    """比对用的归一化：去空格/连字符/标点/商标符号，转小写。"""
    return re.sub(r"[\s\-_·,，。.？?、®™()（）/／+:：]+", "", str(text or "").lower())


# 材质名不是牌号：库里存在 grade 就写成「FKM」的脏数据，
# 不排除的话「FFKM 材质」会被解析成某个叫 FKM 的「产品」，问到错的表上。
MATERIAL_WORDS = {_tidy(code) for code, _keys in catalog.MATERIAL_RULES} | {
    _tidy(label) for label in catalog.LABELS.values()
} | {"other", "未归类", "ffpm"}


def _grade_tokens(item: dict) -> set:
    """一个产品可以被叫出的写法：全名、去品牌前缀、以及末尾 1~3 个词。"""
    name = catalog.clean_grade(item.get("product_name") or "")
    words = name.split()
    tokens = {name}
    for n in (1, 2, 3):
        if len(words) >= n:
            tokens.add(" ".join(words[-n:]))
    if len(words) > 1:
        tokens.add(" ".join(words[1:]))
    return {
        token
        for token in tokens
        if len(_tidy(token)) >= 3 and _tidy(token) not in MATERIAL_WORDS
    }


def _match_materials(question: str) -> list:
    """问题里点名的材质代号（FFKM / PEEK / 氟橡胶 …）。"""
    low = (question or "").lower()
    codes = []
    for code, keys in catalog.MATERIAL_RULES:
        for key in keys:
            if key.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(key.lower())}(?![a-z0-9])", low):
                    codes.append(code)
                    break
            elif key in question:
                codes.append(code)
                break
    return codes


def _valid_product_name(name) -> bool:
    """产品名得像个牌号：纯数字/日期/编号串（7/13506501261469366）不是产品，
    配方组分（炭黑、过氧化物、助剂）也不会是产品。"""
    text = _tidy(name)
    if not text or len(text) > 60:
        return False
    # _tidy 会去空格，所以标记也要归一化后再比（carbon black → carbonblack）
    if any(_tidy(mark) in text for mark in NON_PRODUCT_MARKS):
        return False
    return not re.fullmatch(r"[\d\s/\-_.:#（）()]+", text)


def _usable_products(allowed=None) -> list:
    """能当对比对象的产品：有牌号、不是文献、不是「未标注产品」。"""
    out = []
    for item in product_stats(allowed=allowed):
        pid = str(item.get("product_id") or "")
        if pid in ("", "all", catalog.UNNAMED, catalog.LITERATURE):
            continue
        if catalog.is_unnamed(item.get("product_name") or ""):
            continue
        if _tidy(item.get("product_name")) in MATERIAL_WORDS:
            continue
        if not _valid_product_name(item.get("product_name")):
            continue
        out.append(item)
    return out


def resolve_products(question: str, product: str | None = None, allowed=None) -> dict:
    """把问题解析成目标产品列表。

    返回 {"products": [...], "reason": "grade|supplier|material|application|product|none"}。
    products 为空即「库里没有目标产品」，由调用方走 LLM 兜底。
    """
    items = _usable_products(allowed=allowed)
    if not items:
        return {"products": [], "reason": "none"}

    # 前端已选中某个产品：以筛选为准，问题只决定看哪些字段
    if product and product not in ("all", ""):
        picked = [item for item in items if str(item.get("product_id") or "") == product]
        if picked:
            return {"products": picked, "reason": "product"}

    q = _tidy(question)
    suppliers = named_suppliers(question)

    # 1) 牌号：取命中的最长写法，避免 "P 100" 把 "P 1000" 也带进来
    best, hits = 0, []
    for item in items:
        length = max((len(_tidy(t)) for t in _grade_tokens(item) if _tidy(t) in q), default=0)
        if length > best:
            best, hits = length, [item]
        elif length and length == best:
            hits.append(item)
    if best:
        if suppliers:
            narrowed = [i for i in hits if i.get("supplier") in suppliers]
            hits = narrowed or hits
        return {"products": hits, "reason": "grade"}

    # 2) 供应商：问「索尔维有哪些」是一组产品，不是单个产品
    if suppliers:
        picked = [item for item in items if item.get("supplier") in suppliers]
        if picked:
            return {"products": picked, "reason": "supplier"}

    # 3) 材质：FFKM / PEEK 这类属性问题，展开成该材质下全部产品
    codes = _match_materials(question)
    if codes:
        picked = [item for item in items if item.get("material") in codes]
        if picked:
            return {"products": picked, "reason": "material"}

    # 4) 应用：行业/部件/介质命中（应用筛选栏也有对应维度）
    hits = applications.classify(question)
    wanted = {
        field: labels[0][1]
        for field, labels in (
            ("industries", hits.get("industries") or []),
            ("parts", hits.get("parts") or []),
            ("media", hits.get("media") or []),
        )
        if labels
    }
    if wanted:
        picked = [
            item
            for item in items
            if applications.matches(
                {
                    "industries": "|".join(item.get("industries") or []),
                    "parts": "|".join(item.get("parts") or []),
                    "media": "|".join(item.get("media") or []),
                },
                wanted.get("industries") or "",
                wanted.get("parts") or "",
                wanted.get("media") or "",
            )
        ]
        if picked:
            return {"products": picked, "reason": "application"}

    return {"products": [], "reason": "none"}


def _product_rows(product_ids: set, names: set) -> list:
    """按产品取全量记录（不走向量检索：对比要的是该产品的全部行）。"""
    store = _client().get_or_create_collection(CATALOG)
    data = store.get(include=["metadatas", "documents"])
    rows = []
    for meta, doc in zip(data.get("metadatas") or [], data.get("documents") or []):
        meta = meta or {}
        if str(meta.get("product_id") or "") in product_ids or _title(meta) in names:
            rows.append((meta, doc or ""))
    return rows


def _cell(value, field: str = "") -> str:
    """单表格单元：本地化 → 数值结构化 → 去竖线（否则表格列会错位）→ 压成一行。"""
    if value is None:
        return ""
    text = value_parser.display(value, field)
    text = text.replace("|", "、").replace("\r", " ")
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _dedupe_key(value: str) -> str:
    """去重键：纯「数字 + 单位」按数字归类（232 与 232°C 视为同一个值）。"""
    text = str(value or "").strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?\s*(?:[°℃]?[CF]|%|h|hrs?|min|MPa|psi|MPa)?", text, re.I):
        number = re.match(r"[-+]?\d+(?:\.\d+)?", text)
        return f"num:{number.group(0)}" if number else text
    return _tidy(text)


def _compatible(known: dict, candidate: dict) -> bool:
    """两组单元能否并成一条记录：重叠字段值全相同才并。

    没有重叠字段就没有合并依据，宁可分开；有重叠但值不同，说明是不同测试条件，
    并起来就是伪冲突。
    """
    shared = set(known) & set(candidate)
    if not shared:
        return False
    return all(_dedupe_key(known[column]) == _dedupe_key(candidate[column]) for column in shared)


def _drop_echo_cells(cells: dict) -> dict:
    """去掉「温度范围」跟「测试温度」说同一件事的重复格。

    抽取常把单值温度同时填进两列（232°C 既当范围又当测试温度），卡片上看着像两条
    独立数据。范围只该是真区间，单值重复就保留测试温度那格。
    """
    out = dict(cells)
    for column in value_parser.RANGE_FIELDS:
        if column not in out or not value_parser.single_value_range(out[column], column):
            continue
        parsed = value_parser.parse(out[column], column)
        peer = value_parser.parse(out.get("test_temp") or "", "test_temp")
        if peer and peer.get("numeric") and peer["min"] == parsed["min"]:
            out.pop(column, None)
    return out


def _fragment_bucket(meta: dict) -> tuple:
    """碎片归组键：同一份资料、同一页，才可能是同一张表被切开的碎片。

    跨页 / 跨文档的字段不并：不同资料里的两个数值本来就是独立证据，并起来就是
    把两份资料当成一份说。旧库里没有 doc_id 时退化成按 source 归组。
    """
    return (str(meta.get("doc_id") or ""), str(meta.get("source") or ""))


def _merge_fragments(metas: list, columns: list, asked=()) -> list:
    """把同一产品下互相补全的 chunk 碎片并成一条记录。

    同一份 datasheet 常被切进多个 chunk，每个只带两三个字段，逐 chunk 上表就会出现
    「一个牌号 3 条记录、每条只剩两个字段」。先按资料 + 页归组，组内再按「重叠字段
    值相同」合并：能拼完整，又不会把不同测试条件拼成伪冲突。
    """
    buckets: dict = {}
    for meta in metas:
        buckets.setdefault(_fragment_bucket(meta), []).append(meta)

    out: list = []
    seen: set = set()
    for bucket in buckets.values():
        groups: list = []
        for meta in bucket:
            cells = {}
            for column in columns:
                cell = _cell(meta.get(column), column)
                if cell:
                    cells[column] = cell
            if not cells:
                continue
            # 问耐温就只留真有温度数据的行：配方助剂/填料只带压缩变形值时不该上表
            if asked and not any(column in cells for column in asked):
                continue
            for group in groups:
                if _compatible(group["cells"], cells):
                    for column, value in cells.items():
                        group["cells"].setdefault(column, value)
                    group["metas"].append(meta)
                    break
            else:
                groups.append({"cells": cells, "metas": [meta]})

        for group in groups:
            cells = _drop_echo_cells(group["cells"])
            if not cells:
                continue
            # 跨页/跨文档的完全重复行（重复表头、跨页续表）仍然要去重
            signature = tuple(sorted(cells.items()))
            if signature in seen:
                continue
            seen.add(signature)
            out.append({"cells": cells, "metas": group["metas"]})
    return out


def _num(value, field: str = "", want_max: bool = True) -> float | None:
    """比较用的数值：区间取上界/下界，而不是简单的「第一个数字」。"""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return value_parser.extreme(value, field, want_max)


def compare_products(products: list, question: str, rows: list | None = None,
                     limit: int = MAX_PRODUCTS, allowed=None) -> dict:
    """把一个/一组产品整理成对比表：列按问题焦点排序，一行一条库内记录。"""
    picked = list(products)[:limit]
    ids = {str(item.get("product_id") or "") for item in picked}
    names = {str(item.get("product_name") or "") for item in picked}
    data = rows if rows is not None else _product_rows(ids, names)
    if allowed is not None:
        data = [(meta, doc) for meta, doc in data if access.meta_allowed(meta, allowed)]

    grouped: dict = {}
    for meta, _doc in data:
        title = _title(meta)
        pid = str(meta.get("product_id") or "")
        if title in names:
            grouped.setdefault(title, []).append(meta)
        elif pid in ids:
            grouped.setdefault(pid, []).append(meta)

    all_metas = [meta for metas in grouped.values() for meta in metas]
    present = [key for key in COLUMN_ORDER if any(meta.get(key) not in (None, "") for meta in all_metas)]
    asked = []
    for pattern, keys in FIELD_HINTS:
        if pattern.search(question or ""):
            asked.extend(key for key in keys if key not in asked)
    columns = [key for key in asked if key in present][:MAX_COLUMNS]
    # 问题没点名也要把骨架列建出来，否则卡片是「有温度没时长」的残表
    for key in SKELETON:
        if len(columns) >= MAX_COLUMNS:
            break
        if key in present and key not in columns:
            columns.append(key)
    for key in present:
        if len(columns) >= MAX_COLUMNS:
            break
        if key not in columns:
            columns.append(key)
    labels = dict(FIELDS)

    table = []
    for item in picked:
        key = str(item.get("product_name") or "")
        metas = grouped.get(key) or grouped.get(str(item.get("product_id") or "")) or []
        # 一条记录 = 一份资料里的一个测试条件。先把互相补全的 chunk 碎片并成一条，
        # 再按「命中焦点字段多、字段齐」排序取前几条。
        records = _merge_fragments(metas, columns, asked)
        if not records:
            continue
        records.sort(
            key=lambda record: (
                -sum(1 for column in asked if column in record["cells"]),
                -len(record["cells"]),
            )
        )
        for record in records[:MAX_ROWS_PER_PRODUCT]:
            table.append(
                {
                    "product": key,
                    "product_id": item.get("product_id"),
                    "cells": {column: [value] for column, value in record["cells"].items()},
                    "metas": record["metas"],
                }
            )

    # 列只有极少数行有值时不占版面：一行有值就建列，表看着像填表失败
    if table:
        columns = [
            key for key in columns
            if key in asked
            or key in SKELETON
            or sum(1 for row in table if row["cells"].get(key)) / len(table) >= 0.3
        ]
        # 列被裁掉后变成空壳的行不能算记录：标题里的条数、卡片里的行数、
        # 「N 个产品没可比数据」的统计必须同源，否则标题和内容永远对不上。
        table = [row for row in table if any(row["cells"].get(key) for key in columns)]
        if not table:
            columns = []

    return {
        "products": picked,
        "columns": [{"key": key, "label": labels.get(key, key)} for key in columns],
        "rows": table,
        "reason": "",
        "total": len(products),
    }


def _span_conclusion(result: dict, labels: dict):
    """单产品自身的区间宽度：某个产品的 min ~ max 有多宽。"""
    ordered = sorted(
        result["columns"],
        key=lambda column: column["key"] not in value_parser.RANGE_FIELDS,
    )
    for column in ordered:
        best = None
        for row in result["rows"]:
            parsed = value_parser.parse((row["cells"].get(column["key"]) or [""])[0], column["key"])
            if not parsed or not parsed.get("unit"):
                continue  # 没有单位的数不拿来下结论：宁可不答，也别答一个没法核对的差值
            width = value_parser.span(parsed)
            if width is None:
                continue
            if best is None or width > best[0]:
                best = (width, row, parsed)
        if best:
            width, row, parsed = best
            label = labels.get(column["key"], column["key"])
            unit = parsed.get("unit") or ""
            return f"结论: {row['product']} {label} {parsed['text']}，跨度 {width:g} {unit}".strip(), row
    return "", None


def _gap_conclusion(result: dict, labels: dict, want_max: bool = True):
    """跨产品极差：同一字段上最高和最低的两个产品差多少。

    「FFKM 和 FKM 耐温差距」问的不是任何一个产品自身的区间，而是两者极值之差，
    拿单产品跨度去答会答成「跨度 210 °C」，跟用户问的完全是两件事。
    """
    ordered = sorted(
        result["columns"],
        key=lambda column: column["key"] not in value_parser.RANGE_FIELDS,
    )
    for column in ordered:
        per_product: dict = {}
        for row in result["rows"]:
            cell = (row["cells"].get(column["key"]) or [""])[0]
            parsed = value_parser.parse(cell, column["key"])
            if not parsed or not parsed.get("numeric") or not parsed.get("unit"):
                continue  # 单位未标注的值可以展示，但不能用来算「相差多少」
            if column["key"] in value_parser.RANGE_FIELDS and value_parser.single_value_range(
                cell, column["key"]
            ):
                continue  # 单值「范围」已是公认的误标，别拿它下结论
            value = parsed["max"] if want_max else parsed["min"]
            current = per_product.get(row["product"])
            if current is None or (value > current[0] if want_max else value < current[0]):
                per_product[row["product"]] = (value, row, parsed)
        if len(per_product) < 2:
            continue
        top = max(per_product.values(), key=lambda item: item[0])
        bottom = min(per_product.values(), key=lambda item: item[0])
        if top[0] == bottom[0]:
            continue  # 全部一样，没有差距可报
        label = labels.get(column["key"], column["key"])
        unit = top[2].get("unit") or ""
        return (
            f"结论: {label}最高 {top[1]['product']} {top[2]['text']}，"
            f"最低 {bottom[1]['product']} {bottom[2]['text']}，相差 {top[0] - bottom[0]:g} {unit}".strip(),
            top[1],
        )
    return "", None


def _conclusion(result: dict, question: str):
    """求极值、区间跨越、产品间差距都由代码算，不交给模型。"""
    want_max = any(word in question for word in MAX_UP_WORDS)
    want_min = any(word in question for word in MAX_DOWN_WORDS)
    want_gap = any(word in question for word in GAP_WORDS) and not (want_max or want_min)
    want_span = any(word in question for word in SPAN_WORDS) and not (want_max or want_min)
    if not want_gap and not want_span and want_max == want_min:
        return "", None
    labels = {column["key"]: column["label"] for column in result["columns"]}
    products = {row["product"] for row in result["rows"]}

    # 「差距」在多产品场景下优先答产品间极差，单产品才退化成自身区间宽度
    if want_gap and len(products) >= 2:
        text, row = _gap_conclusion(result, labels, want_max=True)
        if text:
            return text, row
    if want_span or want_gap:
        text, row = _span_conclusion(result, labels)
        if text:
            return text, row
        if want_gap:
            return _gap_conclusion(result, labels, want_max=True)
        return "", None

    for column in result["columns"]:
        best = None
        for row in result["rows"]:
            value = _num(row["cells"].get(column["key"]), column["key"], want_max)
            if value is None:
                continue
            if best is None or (value > best[0] if want_max else value < best[0]):
                best = (value, row)
        if best:
            row = best[1]
            values = row["cells"].get(column["key"]) or []
            raw = " / ".join(values[:2])
            return f"结论: {row['product']} {labels.get(column['key'], column['key'])}: {raw}", row
    return "", None


def render_compare(result: dict, question: str = "") -> str:
    """确定性答案：标题 + 对比表 + 可选结论行（Markdown，前端已能渲染表格）。"""
    if not result["rows"] or not result["columns"]:
        return ""
    products = result["products"]
    reason = REASON_LABELS.get(result.get("reason"), "")
    name = products[0].get("product_name") or ""
    title = f"命中 {len(result['products'])} 个产品 · {len(result['rows'])} 条记录" + (
        f" · {reason}" if reason else ""
    )
    if len(products) == 1 and name:
        title = f"{name} · {reason or '产品数据'}"

    columns = result["columns"]
    head = "| " + " | ".join(["产品"] + [column["label"] for column in columns] + ["来源"]) + " |"
    sep = "| " + " | ".join(["---"] * (len(columns) + 2)) + " |"

    numbering: dict = {}

    def tag(meta) -> int:
        if id(meta) not in numbering:
            numbering[id(meta)] = len(numbering) + 1
        return numbering[id(meta)]

    body = []
    for row in result["rows"]:
        cells = [str(row["product"]).replace("|", "、")]
        for column in columns:
            values = row["cells"].get(column["key"]) or []
            text = " / ".join(values[:MAX_CELL_VALUES])
            if len(values) > MAX_CELL_VALUES:
                text += f" 等{len(values)}个值"
            cells.append(text.replace("|", "、") or "—")
        marks = []
        for meta in row["metas"]:
            number = tag(meta)
            if number not in marks:
                marks.append(number)
        shown = marks[:2]
        label = "".join(f"[{n}]" for n in shown)
        if len(marks) > len(shown):
            label += f"+{len(marks) - len(shown)}"
        body.append("| " + " | ".join(cells + [label or "—"]) + " |")

    out = [f"### {title}", "", head, sep, *body, ""]
    conclusion, source_row = _conclusion(result, question)
    if conclusion and source_row is not None:
        marks = [tag(meta) for meta in source_row["metas"][:1]]
        out.append(conclusion + (" " + "".join(f"[{n}]" for n in marks) if marks else ""))
        out.append("")
    out.append("数值取自库内记录，未推断、未换算；展开下方引用可核对原文。")
    missing = [item.get("product_name") for item in products
               if str(item.get("product_name")) not in {row["product"] for row in result["rows"]}]
    if missing:
        out.append(f"另有 {len(missing)} 个产品库内没有可对比的记录：" + "、".join(str(m) for m in missing[:4]))
    if result["total"] > len(products):
        out.append(f"还有 {result['total'] - len(products)} 个产品未列出，加供应商或牌号可缩小范围。")
    return "\n".join(out)


def compare_payload(result: dict, question: str = "", numbering: dict | None = None) -> dict:
    """前端渲染用的结构化对比数据（产品卡片）。

    与 render_compare 同源同序：数值、分组、引用编号完全一致，只是把宽表拆成
    「一个产品一张卡 + 每条记录一行键值」——九列宽表在窄屏上只会把表头折断。
    """
    products = result.get("products") or []
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if not rows or not columns:
        return {}
    reason = REASON_LABELS.get(result.get("reason"), "")
    name = products[0].get("product_name") or ""
    title = f"命中 {len(products)} 个产品 · {len(rows)} 条记录" + (f" · {reason}" if reason else "")
    if len(products) == 1 and name:
        title = f"{name} · {reason or '产品数据'}"

    numbering = numbering or {}
    groups: list = []
    by_product: dict = {}
    for row in rows:
        product = str(row.get("product") or "")
        refs: list = []
        for meta in row.get("metas") or []:
            mark = numbering.get(id(meta))
            if mark and mark not in refs:
                refs.append(mark)
        fields = []
        for column in columns:
            values = row["cells"].get(column["key"]) or []
            if not values:
                continue
            fields.append(
                {
                    "key": column["key"],
                    "label": column["label"],
                    "value": " / ".join(values[:MAX_CELL_VALUES]),
                    "extra": max(0, len(values) - MAX_CELL_VALUES),
                }
            )
        if not fields:
            continue
        group = by_product.get(product)
        if group is None:
            group = {"product": product, "records": []}
            by_product[product] = group
            groups.append(group)
        group["records"].append({"fields": fields, "refs": refs})

    # 跨牌号同源值：两个牌号所有数值完全一致，多半是同一份 datasheet 被挂到了多个
    # 牌号上，不是两家真做了同样的测试。前端据此加「同源」角标，别当成两边都验证过。
    signatures: dict = {}
    for group in groups:
        signature = tuple(
            sorted((field["key"], field["value"]) for record in group["records"] for field in record["fields"])
        )
        signatures.setdefault(signature, []).append(group["product"])
    for group in groups:
        signature = tuple(
            sorted((field["key"], field["value"]) for record in group["records"] for field in record["fields"])
        )
        group["shared_with"] = [name for name in signatures.get(signature, []) if name != group["product"]]

    conclusion, conclusion_refs = "", []
    text, row = _conclusion(result, question)
    if text and row is not None:
        conclusion = text
        conclusion_refs = [numbering[id(meta)] for meta in row["metas"][:1] if id(meta) in numbering]

    listed = {row["product"] for row in rows}
    missing = [str(item.get("product_name")) for item in products if str(item.get("product_name")) not in listed]

    # 问题问的是区间（耐温范围/高温低温），但某个产品的温度全是单点测试值：
    # 逐卡提示，别让人把某个测试条件当成耐温上下限。
    notes: list = []
    wants_range = any(word in (question or "") for word in ("范围", "区间", "耐温", "高温", "低温", "温度"))
    if wants_range:
        for group in groups:
            keys = {field["key"] for record in group["records"] for field in record["fields"]}
            if keys & set(value_parser.TEMPERATURE_FIELDS) and not keys & set(value_parser.RANGE_FIELDS):
                group["note"] = "只有单点测试温度，库内无区间数据"

    return {
        "title": title,
        "reason": reason,
        "columns": columns,
        "groups": groups,
        "records": sum(len(group["records"]) for group in groups),
        "products": len(products),
        "conclusion": conclusion,
        "conclusion_refs": conclusion_refs,
        "missing": missing[:4],
        "missing_total": len(missing),
        "truncated": max(0, (result.get("total") or 0) - len(products)),
        "notes": notes,
    }


def answer_by_products(question: str, collection: str = CATALOG,
                       product: str | None = None, allowed=None) -> dict | None:
    """有目标产品就走结构化对比，返回 None 表示交给 LLM 兜底。"""
    if _resolve(collection) != CATALOG:
        return None
    if asks_self(question):  # “你是谁”这类问题不可能是产品查询，绝不出表格
        return None
    if _asks_document(question):  # 问「资料里的原文」不走表格
        return None
    resolved = resolve_products(question, product=product, allowed=allowed)
    if not resolved["products"]:
        return None
    result = compare_products(resolved["products"], question, allowed=allowed)
    result["reason"] = resolved["reason"]
    if not result["rows"]:
        return None
    text = render_compare(result, question)
    if not text:
        return None
    # 表格里编号 = 引用列表序号，只把表里用到的记录带出来
    ordered, seen = [], set()
    for row in result["rows"]:
        for meta in row["metas"]:
            if id(meta) in seen:
                continue
            seen.add(id(meta))
            ordered.append(meta)
    numbering = {id(meta): i for i, meta in enumerate(ordered, start=1)}
    return {
        "text": text,
        "payload": compare_payload(result, question, numbering),
        "sources": build_sources(ordered),
        "products": [item.get("product_id") for item in result["products"]],
        "reason": resolved["reason"],
        "columns": result["columns"],
        "rows": result["rows"],
    }


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
    """旧集合名统一落到产品库；产品筛选走 product_id，不再拆库。"""
    legacy = {"materials", "peek", "fkm", "catalog"}
    if not name or name == "all" or str(name).lower() in legacy:
        return CATALOG
    return name


def resolve_collection(name: str) -> str:
    """对外公开的归一化：材质名 / 旧库名 / 空值 → 真实集合名。"""
    return _resolve(name)


def source_stats(collection: str = CATALOG, allowed=None) -> list:
    names = [_resolve(collection)]
    items = []

    for name in names:
        store = _client().get_or_create_collection(name)
        groups: dict = {}
        for _id, meta in _rows(store):
            meta = meta or {}
            if not access.meta_allowed(meta, allowed):
                continue
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


def records_of(collection: str, source: str, limit: int = 200, allowed=None) -> list:
    store = _client().get_or_create_collection(_resolve(collection))
    data = store.get(include=["metadatas", "documents"])
    rows = []
    for meta, doc in zip(data.get("metadatas") or [], data.get("documents") or []):
        meta = meta or {}
        if not access.meta_allowed(meta, allowed):
            continue
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
        try:
            hybrid.delete(_resolve(collection), ids)
        except Exception:  # noqa: BLE001 - 词法索引清理失败不影响删除结果
            pass
    return len(ids)


def search_debug(question: str, collection: str = CATALOG, top_k: int = 8, allowed=None) -> list:
    store = _client().get_or_create_collection(collection)
    text = expand_query(question)
    kwargs = {"n_results": top_k}
    scope = access.where(allowed)
    if scope:
        kwargs["where"] = scope

    if embeddings.ready():
        result = store.query(query_embeddings=[embeddings.embed_one(text)], **kwargs)
    else:
        result = store.query(query_texts=[text], **kwargs)

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    hits = []
    for rank, (doc, meta, dist) in enumerate(zip(docs, metas, dists), start=1):
        meta = meta or {}
        if not access.meta_allowed(meta, allowed):
            continue
        hits.append(
            {
                "rank": rank,
                "score": round(1 / (1 + float(dist)), 4) if dist is not None else None,
                "grade": meta.get("grade_key") or meta.get("grade_raw") or "",
                "product_id": meta.get("product_id") or "",
                "product_name": meta.get("product_name") or "",
                "material": meta.get("material") or "",
                "supplier": meta.get("supplier") or "",
                "application": meta.get("application_text") or "",
                "industries": meta.get("industries") or "",
                "parts": meta.get("parts") or "",
                "source": meta.get("source") or "",
                "collection": collection,
                "snippet": (doc or "")[:240],
            }
        )
    return hits


def product_identity(meta: dict) -> tuple:
    """chunk → (product_id, 展示名)，产品页与统计共用同一套归属规则。"""
    meta = meta or {}
    pid = str(meta.get("product_id") or meta.get("product_name") or "unknown")
    title = _title(meta)
    # 只有厂商名的老数据归到「未标注产品」，不能让 Solvay 变成一个产品
    if title == catalog.LITERATURE:
        return catalog.LITERATURE, catalog.LITERATURE
    if catalog.is_unnamed(title):
        return catalog.UNNAMED, catalog.UNNAMED
    return pid, title


def product_stats(material: str | None = None, industry: str | None = None,
                  part: str | None = None, medium: str | None = None,
                  allowed=None) -> list:
    """按产品聚合：一个产品 = 供应商 + 牌号；应用（行业/部件/介质）是筛选主维度。"""
    try:
        store = _client().get_or_create_collection(CATALOG)
        data = store.get(include=["metadatas"])
    except Exception:
        return []

    groups: dict = {}
    for meta in data.get("metadatas") or []:
        meta = meta or {}
        if not access.meta_allowed(meta, allowed):
            continue
        if material and material not in ("all", "") and meta.get("material") != material:
            continue
        if not applications.matches(meta, industry or "", part or "", medium or ""):
            continue
        pid, title = product_identity(meta)
        group = groups.setdefault(
            pid,
            {
                "product_id": pid,
                "product_name": title,
                "material": meta.get("material") or "",
                "material_label": meta.get("material_label") or "",
                "supplier": meta.get("supplier") or "",
                "records": 0,
                "doc_types": set(),
                "grades": set(),
                "sources": set(),
                "industries": set(),
                "parts": set(),
                "media": set(),
                "certs": set(),
                "applications": set(),
            },
        )
        group["records"] += 1
        if meta.get("doc_type"):
            group["doc_types"].add(str(meta["doc_type"]))
        if meta.get("grade"):
            group["grades"].add(str(meta["grade"]))
        base = str(meta.get("source") or "").split(" p.")[0]
        if base:
            group["sources"].add(base)
        for field in ("industries", "parts", "media", "certs"):
            group[field].update(applications.values(meta, field))
        if meta.get("application_text"):
            group["applications"].add(str(meta["application_text"]))

    out = []
    for group in groups.values():
        group["grades"] = sorted(group["grades"])[:4]
        group["doc_types"] = sorted(group["doc_types"])
        group["sources"] = sorted(group["sources"])
        group["documents"] = len(group["sources"])
        group["applications"] = sorted(group["applications"])[:6]
        for field in ("industries", "parts", "media", "certs"):
            group[field] = sorted(group[field])
        out.append(group)
    return sorted(out, key=lambda g: (-g["records"], g["product_name"]))


def application_facets(allowed=None) -> dict:
    """全库应用分布（行业/部件/介质/认证），供前端筛选栏。"""
    try:
        data = _client().get_or_create_collection(CATALOG).get(include=["metadatas"])
    except Exception:
        return applications.facets([])
    metas = [m for m in (data.get("metadatas") or []) if access.meta_allowed(m, allowed)]
    return applications.facets(metas)


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
        if not did:
            continue
        if did not in mapping:
            title = _title(meta)
            if catalog.is_unnamed(title):
                product_id, product_name = "", ""
            elif title == catalog.LITERATURE:
                product_id, product_name = catalog.LITERATURE, catalog.LITERATURE
            else:
                product_id, product_name = meta.get("product_id") or "", title
            mapping[did] = {
                "product_id": product_id,
                "product_name": product_name,
                "material": meta.get("material") or "",
                "material_label": meta.get("material_label") or "",
                "industries": "",
                "parts": "",
                "media": "",
                "certs": "",
                "application_text": "",
            }
        item = mapping[did]
        if not item["product_name"] and meta.get("product_name"):
            item["product_name"] = meta.get("product_name")
            item["product_id"] = meta.get("product_id") or item["product_id"]
        if not item["application_text"] and meta.get("application_text"):
            item["application_text"] = str(meta["application_text"])
        # 一份资料里不同页/不同表的行业可能不同，合并后用于资料库筛选
        for field in ("industries", "parts", "media", "certs"):
            merged = dict.fromkeys(applications.values(item, field))
            merged.update(dict.fromkeys(applications.values(meta, field)))
            item[field] = "|".join(merged)
    return mapping


DETAIL_FIELDS = [(key, label) for key, label in FIELDS if key != "confidence"]


def _field_value(meta: dict, key: str) -> str:
    value = meta.get(key)
    if value in (None, "", [], {}):
        return ""
    value = localize(value) if isinstance(value, str) else value
    return str(value).strip()


def product_detail(product_id: str, limit: int = 500, allowed=None) -> dict:
    """产品详情：抽取字段（带来源）+ 来源文档 + 明细记录，供产品页使用。"""
    empty = {
        "product": {"product_id": product_id},
        "fields": [],
        "sources": [],
        "records": [],
    }
    try:
        data = _client().get_or_create_collection(CATALOG).get(
            include=["metadatas", "documents"]
        )
    except Exception:
        return empty

    rows: list = []
    for meta, doc in zip(data.get("metadatas") or [], data.get("documents") or []):
        meta = meta or {}
        if not access.meta_allowed(meta, allowed):
            continue
        pid, _title_text = product_identity(meta)
        if product_id not in ("", "all") and pid != product_id:
            continue
        rows.append((meta, doc or ""))

    if not rows:
        return empty

    head = {
        "product_id": product_id,
        "product_name": "",
        "supplier": "",
        "material": "",
        "material_label": "",
        "records": sum(len(group["records"]) for group in groups),
        "documents": 0,
        "grades": [],
        "doc_types": [],
        "industries": [],
        "parts": [],
        "media": [],
        "certs": [],
        "applications": [],
    }

    fields: dict = {}
    documents: dict = {}
    detail_rows: list = []

    for meta, doc in rows:
        _pid, title = product_identity(meta)
        base, page = _split_source(str(meta.get("source") or ""))
        doc_id = str(meta.get("doc_id") or "")
        head["product_name"] = head["product_name"] or title
        head["supplier"] = head["supplier"] or str(meta.get("supplier") or "")
        head["material"] = head["material"] or str(meta.get("material") or "")
        head["material_label"] = head["material_label"] or str(meta.get("material_label") or "")
        for field, bucket in (("grades", "grade"),):
            value = str(meta.get(bucket) or "")
            if value and value not in head[field]:
                head[field].append(value)
        doc_type = str(meta.get("doc_type") or "")
        if doc_type and doc_type not in head["doc_types"]:
            head["doc_types"].append(doc_type)
        for field in ("industries", "parts", "media", "certs"):
            for value in applications.values(meta, field):
                if value and value not in head[field]:
                    head[field].append(value)
        if meta.get("application_text"):
            text = str(meta["application_text"])
            if text not in head["applications"]:
                head["applications"].append(text)

        doc_entry = documents.setdefault(
            base, {"source": base, "doc_id": doc_id, "pages": [], "records": 0}
        )
        doc_entry["records"] += 1
        if doc_id and not doc_entry["doc_id"]:
            doc_entry["doc_id"] = doc_id
        if page and page.isdigit() and int(page) not in doc_entry["pages"]:
            doc_entry["pages"].append(int(page))

        values = []
        for key, label in DETAIL_FIELDS:
            text = _field_value(meta, key)
            if not text:
                continue
            values.append({"key": key, "label": label, "value": text})
            slot = fields.setdefault(key, {"key": key, "label": label, "values": {}})
            sources = slot["values"].setdefault(text, [])
            source_ref = {"source": base, "page": page, "doc_id": doc_id}
            if source_ref not in sources:
                sources.append(source_ref)

        try:
            confidence = round(float(meta.get("confidence") or 0), 2)
        except (TypeError, ValueError):
            confidence = None
        detail_rows.append(
            {
                "source": base,
                "page": page,
                "doc_id": doc_id,
                "confidence": confidence,
                "values": values,
                "chunk": doc[:400],
            }
        )

    head["documents"] = len(documents)
    head["grades"] = sorted(head["grades"])[:8]
    head["doc_types"] = sorted(head["doc_types"])
    head["applications"] = sorted(head["applications"])[:8]

    field_list = []
    for slot in fields.values():
        values = []
        for value, sources in sorted(slot["values"].items(), key=lambda kv: -len(kv[1])):
            values.append(
                {
                    "value": value,
                    "count": len(sources),
                    "sources": sources[:8],
                }
            )
        field_list.append({"key": slot["key"], "label": slot["label"], "values": values})
    order = {key: index for index, (key, _label) in enumerate(DETAIL_FIELDS)}
    field_list.sort(key=lambda item: order.get(item["key"], 99))

    source_list = []
    for entry in documents.values():
        entry["pages"] = sorted(entry["pages"])
        source_list.append(entry)
    source_list.sort(key=lambda item: (-item["records"], item["source"]))

    if limit:
        detail_rows = detail_rows[:limit]

    return {
        "product": head,
        "fields": field_list,
        "sources": source_list,
        "records": detail_rows,
    }
