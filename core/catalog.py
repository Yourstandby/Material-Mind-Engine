"""产品身份与材质归类。

产品 = 供应商 + 牌号，是检索、问答、资料库的主单位；
材质（FFKM / FKM / PEEK …）只是产品的属性，用于筛选，不再拆库。
"""

from __future__ import annotations

import re

MATERIAL_RULES = (
    ("PTFE", ("聚四氟乙烯", "ptfe", "teflon")),
    ("FFKM", ("全氟醚", "全氟橡胶", "全氟弹性体", "perfluoroelastomer", "perfluoro", "ffkm", "全氟")),
    ("PEEK", ("聚醚醚酮", "peek")),
    ("FKM", ("氟橡胶", "氟弹性体", "fluoroelastomer", "fkm", "viton")),
    ("VMQ", ("硅橡胶", "silicone", "vmq")),
    ("EPDM", ("三元乙丙", "epdm")),
    ("NBR", ("丁腈", "nbr")),
    ("PU", ("聚氨酯", "polyurethane")),
)

LABELS = {
    "FFKM": "全氟醚橡胶",
    "FKM": "氟橡胶",
    "PEEK": "聚醚醚酮",
    "PTFE": "聚四氟乙烯",
    "VMQ": "硅橡胶",
    "EPDM": "三元乙丙橡胶",
    "NBR": "丁腈橡胶",
    "PU": "聚氨酯",
    "OTHER": "未归类",
}

# 老库名 → 材质，仅用于迁移阶段兜底
LEGACY = {"materials": "FFKM", "peek": "PEEK", "fkm": "FKM"}


def material_of(composition: str = "", grade: str = "", fallback: str = "") -> str:
    text = f"{composition or ''} {grade or ''}".lower()
    for code, keys in MATERIAL_RULES:
        if any(key.lower() in text for key in keys):
            return code
    if fallback in LABELS:
        return fallback
    if fallback in LEGACY:
        return LEGACY[fallback]
    return "OTHER"


def material_label(code: str) -> str:
    return LABELS.get(code or "OTHER", "未归类")


def slug(text: str, limit: int = 48) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", (text or "").strip().lower().replace("®", ""))
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:limit].strip("-") or "unknown"


def clean_supplier(supplier: str) -> str:
    value = (supplier or "").strip()
    if not value:
        return ""
    # 域名 / URL 不是供应商
    if re.match(r"^(www\.|https?://)", value, re.I) or re.search(r"[a-z0-9-]+\.(com|cn|net|org)\b", value, re.I):
        return ""
    # 材质代号被误抓成供应商
    if value.upper() in set(LABELS) | {"FFPM", "FFKM", "FKM", "PEEK", "PTFE"}:
        return ""
    return re.sub(r"\s+", " ", value)


def clean_grade(grade: str) -> str:
    value = re.sub(r"[®™©]", "", grade or "")
    return re.sub(r"\s+", " ", value).strip()


def product_key(supplier: str, grade: str) -> str:
    return slug(product_name(supplier, grade))


def product_name(supplier: str, grade: str) -> str:
    clean_sup, clean_grd = clean_supplier(supplier), clean_grade(grade)
    # "Perlast" + "Perlast G75M" → 去掉重复前缀
    if clean_sup and clean_grd.lower().startswith(clean_sup.lower()):
        clean_sup = ""
    name = f"{clean_sup} {clean_grd}".strip()
    return name or "未标注产品"
