"""文献仓库：登记 → 规范命名 → 与向量库对齐。

索引文件 library/index.json 是唯一事实来源，记录每份资料的
元数据、物理路径、所属库与解析状态；向量库通过 doc_id 关联。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import chromadb

from core import vector_store as vs
from core.config import env

ROOT = Path("library")
INDEX = ROOT / "index.json"

EDITABLE = (
    "title",
    "authors",
    "year",
    "doc_type",
    "publisher",
    "doi",
    "patent_no",
    "language",
    "tags",
    "notes",
)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read() -> dict:
    if not INDEX.exists():
        return {"version": 1, "docs": {}}
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "docs": {}}
    data.setdefault("docs", {})
    return data


def _write(data: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INDEX)


def doc_id(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


def slug(text: str, limit: int = 56) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", (text or "").strip().lower())
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:limit].strip("-") or "untitled"


def canonical_name(doc: dict) -> str:
    ext = Path(doc.get("original_name") or "file.pdf").suffix.lower() or ".pdf"
    year = doc.get("year") or "nd"
    kind = doc.get("doc_type") or "doc"
    who = doc.get("publisher") or (doc.get("authors") or "").split(",")[0] or "unknown"
    title = doc.get("title") or Path(doc.get("original_name") or "untitled").stem
    return f"{year}_{kind}_{slug(who, 24)}_{slug(title, 60)}{ext}"


def register(
    data: bytes,
    filename: str,
    collection: str,
    pages: int = 0,
    chars: int = 0,
    records: int = 0,
    meta: dict | None = None,
    path: str | None = None,
) -> dict:
    store = _read()
    did = doc_id(data)
    previous = store["docs"].get(did, {})
    entry = {
        "doc_id": did,
        "collection": collection,
        "original_name": previous.get("original_name") or Path(filename).name,
        "path": path if path is not None else previous.get("path", ""),
        "pages": pages or previous.get("pages", 0),
        "chars": chars or previous.get("chars", 0),
        "records": records,
        "ingested_at": previous.get("ingested_at") or _now(),
        "updated_at": _now(),
        **(meta or {}),
    }
    # 人工改过的字段不被自动抽取覆盖
    if previous.get("locked"):
        for key in EDITABLE:
            if key in previous:
                entry[key] = previous[key]
        entry["locked"] = True

    entry["canonical_name"] = previous.get("canonical_name") or canonical_name(entry)
    store["docs"][did] = entry
    _write(store)
    return entry


def get(did: str) -> dict | None:
    return _read()["docs"].get(did)


def all_docs() -> list[dict]:
    docs = list(_read()["docs"].values())
    return sorted(docs, key=lambda d: (-(d.get("year") or 0), d.get("title") or ""))


def update(did: str, fields: dict) -> dict | None:
    store = _read()
    doc = store["docs"].get(did)
    if not doc:
        return None
    for key, value in fields.items():
        if key in EDITABLE:
            doc[key] = value
    doc["locked"] = True
    doc["updated_at"] = _now()
    doc["canonical_name"] = canonical_name(doc)
    _write(store)
    return doc


def _store(collection: str):
    return vs.collection(collection)


def retag_source(collection: str, old: str, new: str) -> int:
    """把向量库里 source 前缀从旧文件名换成新文件名。"""
    try:
        store = _store(collection)
        data = store.get(include=["metadatas"])
    except Exception:
        return 0
    ids, metas = [], []
    for rid, meta in zip(data.get("ids") or [], data.get("metadatas") or []):
        meta = dict(meta or {})
        source = str(meta.get("source") or "")
        base, _, page = source.partition(" p.")
        if base != old:
            continue
        meta["source"] = new + (f" p.{page}" if page else "")
        ids.append(rid)
        metas.append(meta)
    if ids:
        store.update(ids=ids, metadatas=metas)
    return len(ids)


def rename(did: str) -> dict:
    """按规范命名重命名磁盘文件，并同步向量库来源。"""
    store = _read()
    doc = store["docs"].get(did)
    if not doc:
        raise KeyError("资料不存在")

    target = canonical_name(doc)
    old_name = Path(doc.get("path") or doc.get("original_name")).name
    if target == old_name:
        return {"renamed": False, "name": target}

    path = Path(doc.get("path") or "")
    if path and path.exists():
        new_path = path.with_name(target)
        if new_path.exists() and new_path != path:
            new_path = new_path.with_name(f"{new_path.stem}-{did[:4]}{new_path.suffix}")
        path.rename(new_path)
        doc["path"] = str(new_path)
    else:
        doc["path"] = str(Path("inbox/_done") / doc["collection"] / target)

    doc["original_name"] = doc.get("original_name") or old_name
    doc["canonical_name"] = target
    doc["updated_at"] = _now()
    store["docs"][did] = doc
    _write(store)

    moved = retag_source(doc["collection"], old_name, target)
    return {"renamed": True, "name": target, "records": moved}


def sync_counts() -> None:
    """用向量库里的实际记录数回填索引。"""
    store = _read()
    changed = False
    for did, doc in store["docs"].items():
        try:
            got = _store(doc["collection"]).get(where={"doc_id": did})
            count = len(got.get("ids") or [])
        except Exception:
            continue
        if doc.get("records") != count:
            doc["records"] = count
            changed = True
    if changed:
        _write(store)
