"""文献级元数据抽取：标题 / 作者 / 年份 / 类型 / DOI / 专利号。

规则优先，零 token 消耗；推断不出的字段留空，可人工在界面上补。
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fa5]")

MOJIBAKE = (
    ("Â®", "®"),
    ("Â©", "©"),
    ("Â°", "°"),
    ("â€™", "’"),
    ("â€˜", "‘"),
    ("â€œ", "“"),
    ("â€\x9d", "”"),
    ("â€“", "–"),
    ("â€”", "—"),
    ("Â", ""),
)

PATENT_RE = re.compile(
    r"\b(CN|US|EP|WO|JP|KR|TW)\s?-?\s?(\d{6,13})(?:\s?[A-Z]\d?)?\b", re.I
)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>），。；]+")
STANDARD_RE = re.compile(r"\b(ISO|ASTM|GB/T|GB|JIS|DIN|IEC|EN|SEMI)\s?[\d][\d.\-]*", re.I)

SKIP_LINE = re.compile(
    r"^(page|p\.)\s*\d+"
    r"|^\d{1,4}$"
    r"|^[©(c)]\s*\d{4}"
    r"|^copyright"
    r"|^all rights reserved"
    r"|^(technical|product)\s+data\s+sheet$"
    r"|^data\s*sheet$"
    r"|^datasheet$"
    r"|^(table|figure|fig\.)\s*\d"
    r"|^https?://"
    r"|^www\."
    r"|^[\w.+-]+@[\w.-]+"
    r"|^rev(ision)?\.?\s*[:\d]"
    r"|^version\s"
    r"|^issued\b"
    r"|^(confidential|proprietary)",
    re.I,
)

EN_NAME = r"[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-zA-Z'’\-]+)+"
EN_AUTHORS = re.compile(rf"^{EN_NAME}(?:\s*[,，;；]\s*{EN_NAME})+$")
ZH_AUTHORS = re.compile(r"^[\u4e00-\u9fa5]{2,4}(?:\s*[,，、;；]\s*[\u4e00-\u9fa5]{2,4})+$")

DOC_TYPES = ("patent", "paper", "standard", "datasheet", "report", "manual", "other")

TYPE_LABEL = {
    "patent": "专利",
    "paper": "论文",
    "standard": "标准",
    "datasheet": "数据表",
    "report": "报告",
    "manual": "手册",
    "other": "其他",
}


def _clean(text: str) -> str:
    value = text or ""
    for bad, good in MOJIBAKE:
        value = value.replace(bad, good)
    # GBK 误读的 ®（如 CHEMRAZ庐）只在紧跟 ASCII 字母时替换，避免误伤中文
    value = re.sub(r"([A-Za-z])\u5e90", r"\1®", value)
    # 剔除 PDF 解码残留的孤立代理字符，否则 JSON / 前端会报错
    value = value.encode("utf-8", "ignore").decode("utf-8")
    return re.sub(r"\s+", " ", value).strip()


def _pdf_title(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        info = PdfReader(io.BytesIO(data)).metadata or {}
        title = _clean(str(info.get("/Title") or ""))
        if 6 <= len(title) <= 180 and not title.lower().endswith((".pdf", ".doc")):
            return title
    except Exception:
        pass
    return ""


def _first_text(pages: list) -> str:
    return "\n".join(page.get("text") or "" for page in pages[:2])


def _pick_title(lines: list[str], filename: str) -> tuple[str, int]:
    """返回 (标题, 标题所在行号)。"""
    cleaned = [_clean(line) for line in lines[:26]]

    # 被 PDF 硬换行截断的标题："Low compression set, multi-" + "purpose ..."
    for index in range(len(cleaned) - 1):
        if cleaned[index].endswith(("-", "—")) and cleaned[index].endswith("-"):
            nxt = cleaned[index + 1]
            if nxt and nxt[:1].islower():
                cleaned[index] = cleaned[index][:-1] + nxt
                cleaned[index + 1] = ""

    best, best_score, best_i = "", -1, 0
    for index, line in enumerate(cleaned):
        if not (8 <= len(line) <= 160):
            continue
        if SKIP_LINE.search(line):
            continue
        if re.search(r"www\.|https?://|丨|@", line):
            continue
        if not re.search(r"[A-Za-z\u4e00-\u9fa5]{3}", line):
            continue
        letters = sum(1 for ch in line if ch.isalpha() or CJK.match(ch))
        if letters < len(line) * 0.45:
            continue
        score = 80 - index * 3
        if 15 <= len(line) <= 110:
            score += 12
        if line[0].isdigit():
            score -= 20
        if line.upper() == line and len(line) < 24:
            score -= 25
        if line.endswith((",", "，", ";", "；", "-", "—")):
            score -= 30
        if re.search(r"\b(data ?sheet|datasheet|technical data)\b", line, re.I):
            score -= 40
        if score > best_score:
            best, best_score, best_i = line, score, index

    if best and best_score >= 62:
        return best, best_i

    stem = re.sub(r"[_\-]+", " ", Path(filename).stem).strip()
    return (stem or "未命名资料"), 0


def _pick_authors(lines: list[str], start: int) -> str:
    for raw in lines[start + 1 : start + 10]:
        line = _clean(raw)
        if not (4 <= len(line) <= 200):
            continue
        if EN_AUTHORS.match(line) or ZH_AUTHORS.match(line):
            names = re.split(r"\s*[,，、;；]\s*", line)
            return ", ".join(name.strip() for name in names if name.strip())
        if re.search(r"(author|作者|发明人|inventor)", line, re.I):
            value = re.split(r"[:：]", line, maxsplit=1)
            if len(value) == 2 and 2 <= len(value[1].strip()) <= 120:
                return _clean(value[1])
    return ""


def _pick_year(text: str, filename: str) -> int | None:
    now = time.localtime().tm_year
    candidates: list[int] = []

    for pattern, weight in (
        (r"[©(c)]\s*((?:19|20)\d{2})", 3),
        (r"copyright\s*((?:19|20)\d{2})", 3),
        (r"(?:rev|revision|version)\.?\s*[:\s]?((?:19|20)\d{2})", 2),
    ):
        for match in re.finditer(pattern, text, re.I):
            year = int(match.group(1))
            if 1990 <= year <= now:
                candidates.extend([year] * weight)

    # “2024-2030 预测”取起始年，而不是最远的预测年
    for match in re.finditer(r"((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})", text):
        year = int(match.group(1))
        if 1990 <= year <= now:
            candidates.extend([year] * 2)

    for match in re.finditer(r"\b((?:19|20)\d{2})\b", text[:6000]):
        year = int(match.group(1))
        if 1990 <= year <= now:
            candidates.append(year)

    for match in re.finditer(r"\b((?:19|20)\d{2})\b", filename):
        year = int(match.group(1))
        if 1990 <= year <= now:
            candidates.append(year)

    return max(candidates) if candidates else None


def _pick_publisher(text: str, filename: str, suppliers: list[str] | None) -> str:
    for pattern in (
        r"(?:申请人|专利权人|申请者)\s*[:：]\s*([^\n]{2,60})",
        r"(?:applicant|assignee|owner)\s*[:：]\s*([^\n]{2,60})",
        r"(?:manufacturer|producer|supplier)\s*[:：]\s*([^\n]{2,60})",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return _clean(match.group(1))

    lowered = text.lower()
    for name in suppliers or []:
        if name and name.lower() in lowered:
            return name

    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    token = stem.split(" ")[0]
    return token if token.isalpha() and len(token) > 2 else ""


def _pick_doc_type(text: str, filename: str, doi: str, patent_no: str) -> str:
    low = (text + " " + filename).lower()

    if patent_no and re.search(r"专利|patent|申请号|公开号", low):
        return "patent"
    if re.search(r"专利号|申请号|公开号|授权公告号", low):
        return "patent"
    if STANDARD_RE.search(text) and re.search(r"standard|标准|规范", low):
        return "standard"
    if doi or (re.search(r"abstract|摘要", low) and re.search(r"references|参考文献", low)):
        return "paper"
    if re.search(r"data ?sheet|datasheet|数据表|技术数据|typ\.? values|典型值", low):
        return "datasheet"
    if re.search(r"market|forecast|report|研究报告|行业", low):
        return "report"
    if re.search(r"manual|handbook|instruction|手册|说明书", low):
        return "manual"
    return "other"


def extract(filename: str, data: bytes, pages: list, suppliers: list[str] | None = None) -> dict:
    """从文件名 + 前两页文本推断文献元数据。"""
    text = _first_text(pages)
    lines = [line for line in text.splitlines()]

    pdf_title = _pdf_title(data) if filename.lower().endswith(".pdf") else ""
    title, title_at = _pick_title(lines, filename)
    if pdf_title:
        title = pdf_title

    doi_match = DOI_RE.search(text)
    doi = doi_match.group(0).rstrip(".,;") if doi_match else ""

    patent_match = PATENT_RE.search(text) or PATENT_RE.search(filename)
    patent_no = f"{patent_match.group(1).upper()}{patent_match.group(2)}" if patent_match else ""

    doc_type = _pick_doc_type(text, filename, doi, patent_no)
    authors = _pick_authors(lines, title_at) if doc_type in ("paper", "patent") else ""
    year = _pick_year(text, filename)
    publisher = _pick_publisher(text, filename, suppliers)
    language = "zh" if len(CJK.findall(text[:4000])) > 60 else "en"

    confidence = round(
        sum(bool(v) for v in (title, doc_type != "other", year, publisher, doi or patent_no or authors))
        / 5,
        2,
    )

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doc_type": doc_type,
        "publisher": publisher,
        "doi": doi,
        "patent_no": patent_no,
        "language": language,
        "confidence": confidence,
    }
