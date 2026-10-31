"""牌号兜底抽取。

LLM 抽取会把牌号漏在表头、页眉或文件名里，尤其是「品牌 + 型号」分两行写的
日系目录（NICHIAS 的 BLAZER ™ S2 / PERFLUORO PFW ™、NOK 的 JIS 记号）和欧系
数据表（Tecnoflon P 959、Viton GLT、Solvay XPE）。

这里用品牌词典 + 严格段校验从原文里认牌号。原则是宁可漏，也不要把
ASTM D2000、ISO 9001、FFKM 这类标准号/材质代号写进产品库——错的产品名比
没有产品名更糟，它会凭空造出一个不存在的产品。

只在文档层面稳定出现（≥2 次）的候选才回填，单页噪声不会挂到记录上。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import catalog
from core.normalize import grade_key

# 商业品牌/系列名。命中后最多再吞两段型号，例如：
#   "BLAZER ™ S2" -> BLAZER S2 ; "Tecnoflon P 959" -> Tecnoflon P 959
BRANDS = (
    "perfluoro pfw",
    "pure rubber",
    "fluoro elastomer",
    "fluoro plus",
    "tecnoflon",
    "viton",
    "kalrez",
    "chemraz",
    "perlast",
    "simriz",
    "isolast",
    "aflas",
    "fluorel",
    "hifluor",
    "encore",
    "sifel",
    "blazer",
    "dyneon",
    "varox",
    "dbph",
    "dbpx",
    "xpe",
)

# 材质代号、标准、单位：看着像型号但不是牌号
REJECT = {
    "FKM", "FFKM", "PTFE", "PEEK", "VMQ", "EPDM", "NBR", "HNBR", "SBR", "IIR",
    "ACM", "AEM", "PU", "BR", "CR", "NR", "EVA", "OTHER", "TM", "R",
    "ASTM", "ISO", "DIN", "GB", "JIS", "IEC", "EN", "UL", "SAE", "MIL", "ANSI",
    "BS", "API", "NSF", "FDA", "REACH", "ROHS", "MSDS", "SDS", "CAT", "NO",
    "PDF", "PAGE",
}

# 型号后面跟的英文虚词/字段名：PDF 换行时容易粘在一起（"BLAZER NEXT IS"）
STOPWORDS = {
    "is", "was", "are", "be", "the", "and", "or", "of", "to", "in", "for",
    "with", "base", "type", "grade", "series", "page", "cat", "no", "has",
    "at", "by", "on", "it", "as", "an", "a", "per", "free",
}

BRAND_RE = re.compile(
    r"\b(?:" + "|".join(sorted((re.escape(b) for b in BRANDS), key=len, reverse=True)) + r")\b",
    re.I,
)
PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{0,7}")


def _normalize(text: str) -> str:
    value = (text or "").replace("™", " ").replace("®", " ").replace("©", " ")
    value = value.replace("|", " ").replace("_", " ")
    return re.sub(r"\s+", " ", value)


def _part_ok(part: str, first: bool) -> bool:
    token = part.strip(".,;:()[]'\"")
    if not token or token.upper() in REJECT or token.lower() in STOPWORDS:
        return False
    if BRAND_RE.fullmatch(token):  # 不能再吞一个品牌名："BLAZER FC BLAZER"
        return False
    if token.isdigit():
        return not first and 2 <= len(token) <= 5
    # 全大写型号：S2 / NEXT / P / DBPH-50 / PFW
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{0,7}", token):
        if any(ch.isdigit() for ch in token):
            return True
        return 1 <= len(token) <= 4
    # 少字母 + 多数字：Vi655 / X75545 / X 75545
    return bool(re.fullmatch(r"[A-Za-z]{1,4}[\-/]?\d{2,8}[A-Za-z]?", token))


def candidates(text: str) -> list[str]:
    """从一段文本里认出可能的牌号，按出现顺序返回。"""
    clean = _normalize(text)
    found: list[str] = []
    for match in BRAND_RE.finditer(clean):
        parts: list[str] = []
        for raw in clean[match.end() : match.end() + 48].split():
            if len(parts) >= 2:
                break
            token = raw.strip(".,;:()[]'\"")
            if not PART_RE.fullmatch(token):
                break
            if not _part_ok(token, first=not parts):
                break
            parts.append(token)
        name = " ".join([match.group(0)] + parts)
        if name not in found:
            found.append(name)
    return found


def _counts(page_texts: dict[int, str], filename: str = "") -> dict[str, int]:
    """文档级出现次数：牌号在目录/数据表里会反复出现，单页噪声只有一次。"""
    counts: dict[str, int] = {}
    texts = list(page_texts.values())
    stem = Path(filename or "").stem.replace("_", " ").replace("-", " ")
    if stem:
        texts.append(stem)
    for text in texts:
        for name in candidates(text):
            counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    return counts


def _best(text: str, counts: dict[str, int], minimum: int = 2) -> str:
    """页内候选里选最具体的：带型号段的优先于只有品牌名的。

    目录封面会反复出现品牌（BLAZER ×16），但把整本目录的材料都叫 BLAZER
    等于没抽；带型号的 BLAZER S2 才是有区分度的产品。
    """
    scored = []
    for name in candidates(text):
        count = counts.get(name.casefold(), 0)
        if count < minimum:
            continue
        scored.append(((len(name.split()) > 1, count, len(name)), name))
    if not scored:
        return ""
    # 同一页出现多个具体型号 → 这是目录/汇总页，标任何一个都会错
    if sum(1 for score, _name in scored if score[0]) > 1:
        return ""
    return max(scored)[1]


def _page_of(source: str) -> int:
    match = re.search(r"\bp\.(\d+)\s*$", source or "")
    return int(match.group(1)) if match else 0


def backfill(
    records: list[dict],
    page_texts: dict[int, str],
    filename: str = "",
    doc_type: str = "",
) -> int:
    """给缺牌号的记录补 product identity，返回补了几条。

    论文/专利不补：那里面出现的 "Viton GLT" 是作者引用的别家材料，挂到记录上
    会把文献数据错认成该产品的参数。
    """
    if catalog.is_literature(doc_type) or not records:
        return 0
    counts = _counts(page_texts, filename)
    if not counts:
        return 0
    filled = 0
    for record in records:
        if str(record.get("grade") or record.get("grade_raw") or "").strip():
            continue
        text = page_texts.get(_page_of(str(record.get("source") or "")))
        if not text:
            text = " ".join(page_texts.values())
        name = _best(text, counts)
        if not name:
            continue
        record["grade"] = name
        record["grade_raw"] = name
        record["grade_key"] = grade_key(name)
        filled += 1
    return filled
