"""实体归一化：牌号、供应商、单位。入库前跑一次，查询时用同一套规则。"""

import re

GRADE_PREFIX = re.compile(r"^X(?=\d)")

SUPPLIER_ALIAS = {
    "daikin": "Daikin Industries",
    "大金": "Daikin Industries",
    "dyneon": "3M Dyneon",
    "solvay": "Syensqo",
    "syensqo": "Syensqo",
    "索尔维": "Syensqo",
    "苏威": "Syensqo",
    "tecnoflon": "Syensqo",
    "chemours": "Chemours",
    "科慕": "Chemours",
    "solvay speciality": "Syensqo",
    "greene tweed": "Greene Tweed",
    "parker": "Parker Hannifin",
    "victrex": "Victrex",
    "ensinger": "Ensinger",
    "mitsuichem": "Mitsui Chemicals",
    "三井": "Mitsui Chemicals",
}

UNIT_FIELDS = ("temp_range", "test_temp", "test_duration", "cs", "chem_resistance", "pressure")


def grade_key(value) -> str:
    """检索/去重用的牌号键：如 X6055B -> 6055B。"""
    if not value:
        return ""
    text = re.sub(r"\s+", "", str(value)).upper()
    return GRADE_PREFIX.sub("", text)


def supplier(value) -> str:
    if not value:
        return ""
    raw = str(value).strip()
    low = raw.lower()
    for alias, canonical in SUPPLIER_ALIAS.items():
        if alias in low:
            return canonical
    return raw


def _f_to_c(match: re.Match) -> str:
    return f"{round((float(match.group(1)) - 32) * 5 / 9, 1)}°C"


def units(text):
    """保守转换：只处理明确的数值 + 单位，其余原样保留。"""
    if not text:
        return text
    out = str(text)
    out = re.sub(r"(-?\d+(?:\.\d+)?)\s*°?\s*F\b", _f_to_c, out)
    out = re.sub(r"(-?\d+(?:\.\d+)?)\s*°?\s*C\b", r"\1°C", out, flags=re.I)
    out = re.sub(
        r"(\d+(?:\.\d+)?)\s*psi\b",
        lambda m: f"{round(float(m.group(1)) * 0.006895, 2)} MPa",
        out,
        flags=re.I,
    )
    out = re.sub(r"(\d+(?:\.\d+)?)\s*(?:hr|hrs|hours?)\b", r"\1 h", out, flags=re.I)
    return out


def normalize_record(record: dict) -> dict:
    out = dict(record)
    out["grade_raw"] = str(out.get("grade") or "")
    out["grade_key"] = grade_key(out.get("grade"))
    if out.get("supplier"):
        out["supplier"] = supplier(out["supplier"])
    for field in UNIT_FIELDS:
        if out.get(field):
            out[field] = units(out[field])
    return out
