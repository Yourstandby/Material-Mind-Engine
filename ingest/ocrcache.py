"""离线 OCR 文本层缓存：预处理产物，投喂时直接复用，跳过逐页在线 OCR。

位置：logs/pdf_ocr_cache/<sha1>.v1.jsonl
格式：每页一行 {"page":n,"text":"..."}，整本跑完再追加一行 {"done":true,"pages":N,"dpi":...}
约定：只有 done 行存在且页数齐全时 parse() 才采用，半成品一律忽略——
      否则被取消的预处理会让半本书悄悄进库。

键是 PDF 文件的 sha1，所以预处理和投喂可以对不上文件名/后缀（.crx 伪装成 PDF 也能命中）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.config import ROOT

VERSION = "v1"
DIR = Path(ROOT) / "logs" / "pdf_ocr_cache"


def key(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def path(digest: str) -> Path:
    return DIR / f"{digest}.{VERSION}.jsonl"


def load(digest: str) -> dict[int, str] | None:
    """返回 {页码: 文本}；未完成或页数不齐时返回 None。"""
    pages: dict[int, str] = {}
    total = 0
    try:
        with path(digest).open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("done"):
                    total = int(item.get("pages") or 0)
                elif "page" in item:
                    pages[int(item["page"])] = str(item.get("text") or "")
    except OSError:
        return None
    if not total or len(pages) != total:
        return None
    return pages


def append(digest: str, page: int, text: str) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"page": int(page), "text": text}, ensure_ascii=False)
    with path(digest).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def finish(digest: str, total: int, dpi: int) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"done": True, "pages": int(total), "dpi": int(dpi)}, ensure_ascii=False)
    with path(digest).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def clear(digest: str) -> None:
    path(digest).unlink(missing_ok=True)


def stats(digest: str) -> dict:
    """已完成的页（用于续跑时跳过）。"""
    done: dict[int, str] = {}
    try:
        with path(digest).open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and "page" in item:
                    done[int(item["page"])] = str(item.get("text") or "")
    except OSError:
        return {}
    return done
