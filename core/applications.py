"""应用场景归类：行业 / 部件 / 介质 / 认证。

公司更关心「用在哪」而不是「是什么材质」：应用是检索、筛选与问答的第一等维度，
材质仍是产品属性。所有归类结果以 "|" 连接的字符串写入向量元数据
（Chroma 元数据只接受标量），标签用中文，英语资料也能通过规则命中。
"""

from __future__ import annotations

import re

# (code, 中文标签, 关键词)
INDUSTRIES = (
    ("semiconductor", "半导体", (
        "半导体", "晶圆", "芯片", "刻蚀", "沉积", "光刻", "等离子", "洁净室",
        "semiconductor", "wafer", "etch", "deposition", "lithography", "plasma", "cleanroom",
    )),
    ("chemical", "化工", (
        "化工", "化学", "强酸", "强碱", "腐蚀", "反应釜", "换热器",
        "chemical", "acid", "alkali", "corrosion", "reactor", "process",
    )),
    ("automotive", "汽车", (
        "汽车", "发动机", "燃油", "变速箱", "制动", "整车",
        "automotive", "engine", "fuel", "transmission", "brake", "vehicle",
    )),
    ("food", "食品饮料", (
        "食品", "饮料", "乳品", "酿造", "food", "beverage", "dairy", "brew",
    )),
    ("medical", "医疗", (
        "医疗", "医用", "制药", "灭菌", "植入", "medical", "pharma", "steril", "implant",
    )),
    ("energy", "能源电力", (
        "能源", "电力", "核电", "光伏", "锂电", "油气", "钻井",
        "energy", "power", "nuclear", "solar", "battery", "oil", "gas", "drilling",
    )),
    ("aerospace", "航空航天", (
        "航空", "航天", "飞机", "aerospace", "aviation", "aircraft",
    )),
    ("general", "通用工业", (
        "通用", "机械", "泵", "阀", "工业", "general", "industrial", "pump", "valve",
    )),
)

PARTS = (
    ("oring", "O形圈", ("o形圈", "o 形圈", "o型圈", "o 型圈", "o-ring", "oring", "o ring")),
    ("seal", "密封件", ("密封件", "密封圈", "油封", "垫片", "垫圈", "seal", "gasket", "packing")),
    ("valve", "阀门", ("阀座", "阀体", "阀门", "valve", "seat")),
    ("hose", "管/软管", ("软管", "管路", "管道", "hose", "tube", "pipe")),
    ("diaphragm", "隔膜", ("隔膜", "膜片", "diaphragm", "membrane")),
    ("bearing", "轴承/衬套", ("轴承", "衬套", "bearing", "bushing")),
    ("coating", "涂层/衬里", ("涂层", "衬里", "coating", "lining")),
    ("flange", "法兰", ("法兰", "flange")),
)

MEDIA = (
    ("acid", "酸", ("强酸", "酸", "硫酸", "盐酸", "硝酸", "氢氟酸", "磷酸", "acid", "sulfuric", "hydrochloric", "nitric", "hf")),
    ("alkali", "碱", ("强碱", "碱", "氢氧化钠", "氢氧化钾", "氨水", "alkali", "caustic", "naoh", "koh")),
    ("solvent", "溶剂", ("溶剂", "丙酮", "甲苯", "二甲苯", "醇", "酮", "solvent", "acetone", "toluene", "xylene", "alcohol")),
    ("oil", "油", ("润滑油", "矿物油", "液压油", "油", "oil", "lubricant", "hydraulic")),
    ("fuel", "燃料", ("燃油", "汽油", "柴油", "燃料", "fuel", "gasoline", "diesel")),
    ("steam", "蒸汽", ("蒸汽", "高温蒸汽", "steam")),
    ("water", "水/海水", ("冷却水", "海水", "纯水", "热水", "water", "seawater", "coolant")),
    ("gas", "气体", ("气体", "氧气", "氮气", "氢气", "二氧化碳", "gas", "oxygen", "nitrogen", "hydrogen", "co2")),
    ("plasma", "等离子", ("等离子", "plasma")),
    ("refrigerant", "制冷剂", ("制冷剂", "冷媒", "refrigerant", "freon")),
)

CERTS = (
    ("fda", "FDA", ("fda", "21 cfr 177")),
    ("usp_vi", "USP Class VI", ("usp class vi", "usp vi", "class vi")),
    ("iso10993", "ISO 10993", ("iso 10993", "10993")),
    ("reach", "REACH", ("reach",)),
    ("rohs", "RoHS", ("rohs",)),
    ("ul", "UL", ("ul 认证", "ul listed", "ul94", "ul 94", "ul")),
    ("nsf", "NSF", ("nsf", "nsf 51", "nsf 61")),
    ("atex", "ATEX", ("atex",)),
    ("api", "API", ("api 6a", "api 6d", "api 610", "api")),
)

ALL_RULES = (
    ("industries", INDUSTRIES),
    ("parts", PARTS),
    ("media", MEDIA),
    ("certs", CERTS),
)

# 检索补全：中文提问 → 常见英文写法，避免英语资料因语言差异落选
QUERY_ALIASES = (
    ("半导体", "semiconductor wafer etch plasma"),
    ("晶圆", "wafer semiconductor"),
    ("化工", "chemical process acid corrosion"),
    ("汽车", "automotive engine fuel"),
    ("食品", "food beverage"),
    ("医疗", "medical pharma sterilization"),
    ("能源", "energy power oil gas"),
    ("航空", "aerospace aviation"),
    ("o形圈", "o-ring oring"),
    ("密封", "seal gasket"),
    ("阀", "valve seat"),
    ("隔膜", "diaphragm"),
    ("等离子", "plasma"),
    ("耐等离子", "plasma resistant"),
    ("灭菌", "sterilization"),
)

_FIELD_KEYS = (
    "application", "industry", "part", "medium", "certifications",
    "application_text", "industries", "parts", "media", "certs",
    "composition", "chem_resistance", "test_medium", "material",
)


def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value or "")


def _match(text: str, rules) -> list:
    low = (text or "").lower()
    hits = []
    for code, label, keys in rules:
        for key in keys:
            # 英文按词边界匹配，中文直接包含
            if key.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", low):
                    hits.append((code, label))
                    break
            elif key in low:
                hits.append((code, label))
                break
    return hits


def classify(text: str) -> dict:
    """把一段文本归类成 {industries:[(code,label)], parts:[...], ...}。"""
    out = {}
    for field, rules in ALL_RULES:
        out[field] = _match(text, rules)
    return out


def _pack(values) -> str:
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return "|".join(seen)


def _labels(hits) -> list:
    return [label for _code, label in hits]


def _codes(hits) -> list:
    return [code for code, _label in hits]


def enrich(record: dict) -> dict:
    """给抽取记录补应用字段：保留 LLM 原文，同时写入规范化标签。

    返回的键都是字符串，可直接作为 Chroma 元数据：
      application / industries / parts / media / certs / application_text
    """
    out = {k: v for k, v in (record or {}).items() if isinstance(v, (str, int, float, bool))}
    raw_text = " ".join(_text(record.get(key)) for key in _FIELD_KEYS)
    found = classify(raw_text)

    industries = _labels(found["industries"])
    parts = _labels(found["parts"])
    media = _labels(found["media"])
    certs = _labels(found["certs"])

    # LLM 明确写出的类别优先（可能不在关键词表里），再用规则兜底
    raw_fields = {
        "industry": INDUSTRIES,
        "part": PARTS,
        "medium": MEDIA,
        "certifications": CERTS,
    }
    for key, bucket in (("industry", industries), ("part", parts), ("medium", media), ("certifications", certs)):
        value = _text(record.get(key)).strip()
        if not value:
            continue
        rules = raw_fields[key]
        hits = _match(value, rules)
        if hits:
            bucket.extend(label for _code, label in hits)
        else:
            bucket.insert(0, value)

    industries = list(dict.fromkeys(industries))
    parts = list(dict.fromkeys(parts))
    media = list(dict.fromkeys(media))
    certs = list(dict.fromkeys(certs))

    application = _text(record.get("application")).strip()
    out["industries"] = _pack(industries)
    out["industry_codes"] = _pack(_codes(found["industries"]))
    out["parts"] = _pack(parts)
    out["parts_codes"] = _pack(_codes(found["parts"]))
    out["media"] = _pack(media)
    out["certs"] = _pack(certs)
    out["application_text"] = application or "、".join(
        part for part in (_pack(industries).replace("|", "、"), _pack(parts).replace("|", "、")) if part
    )
    return out


def values(meta: dict, field: str) -> list:
    return [v for v in str((meta or {}).get(field) or "").split("|") if v]


def matches(meta: dict, industry: str = "", part: str = "", medium: str = "") -> bool:
    """应用筛选：传中文标签或英文 code 都行，空值表示不限。"""
    checks = (("industries", "industry_codes", industry),
              ("parts", "parts_codes", part),
              ("media", "", medium))
    for label_field, code_field, wanted in checks:
        if not wanted or wanted == "all":
            continue
        haystack = values(meta, label_field)
        if code_field:
            haystack += values(meta, code_field)
        if wanted not in haystack:
            return False
    return True


def facets(metas) -> dict:
    """应用分布，供前端筛选栏使用：[{code,label,count}]。"""
    counters = {field: {} for field, _rules in ALL_RULES}
    for meta in metas or []:
        meta = meta or {}
        for field, rules in ALL_RULES:
            labels = values(meta, field)
            codes = values(meta, "industry_codes" if field == "industries" else f"{field}_codes")
            for (code, label, _keys) in rules:
                if label in labels or code in codes:
                    counters[field][(code, label)] = counters[field].get((code, label), 0) + 1
    out = {}
    for field, counts in counters.items():
        out[field] = [
            {"code": code, "label": label, "count": count}
            for (code, label), count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
    return out
