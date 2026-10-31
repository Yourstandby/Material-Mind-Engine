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

# 入库归属：用户可增删的文件夹。未分类是兜底目录，不可删除。
UNFILED = "unfiled"
DEFAULT_FOLDERS = (
    (UNFILED, "未分类"),
    ("fkm", "氟橡胶"),
    ("ffkm", "全氟醚橡胶"),
    ("peek", "聚醚醚酮"),
)

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
        data = {"version": 1, "docs": {}}
    else:
        try:
            data = json.loads(INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"version": 1, "docs": {}}
    data.setdefault("docs", {})
    changed = False
    if "folders" not in data:
        for fid, name in DEFAULT_FOLDERS:
            data["folders"] = data.get("folders") or {}
            data["folders"][fid] = {"id": fid, "name": name, "created_at": _now()}
        changed = True
    else:
        data["folders"] = data.get("folders") or {}
    # 未分类是兜底目录，始终存在
    if UNFILED not in data["folders"]:
        data["folders"][UNFILED] = {"id": UNFILED, "name": "未分类", "created_at": _now()}
        changed = True
    if changed:
        _write(data)
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
    folder_id: str = "",
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
        "folder_id": folder_id or previous.get("folder_id") or UNFILED,
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


def folders() -> list[dict]:
    """文件夹及资料数。未分类排最后。"""
    data = _read()
    counts: dict = {}
    for doc in data["docs"].values():
        fid = doc.get("folder_id") or UNFILED
        counts[fid] = counts.get(fid, 0) + 1
    items = [
        {**folder, "count": counts.get(fid, 0), "deletable": fid != UNFILED}
        for fid, folder in data["folders"].items()
    ]
    items.sort(key=lambda item: (item["id"] == UNFILED, item["name"]))
    return items


def folder_map() -> dict:
    return {item["id"]: item["name"] for item in folders()}


def ensure_folder(name: str, folder_id: str = "") -> str:
    """按名称找文件夹，没有就建；用于入库自动分类。"""
    data = _read()
    name = (name or "").strip() or "未分类"
    for fid, folder in data["folders"].items():
        if folder.get("name") == name:
            return fid
        if folder_id and fid == folder_id:
            return fid
    fid = folder_id or re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or doc_id(name.encode())
    base, index = fid, 2
    while fid in data["folders"]:
        fid = f"{base}-{index}"
        index += 1
    data["folders"][fid] = {"id": fid, "name": name, "created_at": _now()}
    _write(data)
    return fid


def create_folder(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("文件夹名称不能为空")
    if len(name) > 40:
        raise ValueError("文件夹名称过长")
    data = _read()
    if any(folder.get("name") == name for folder in data["folders"].values()):
        raise ValueError("同名文件夹已存在")
    fid = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or doc_id(name.encode())
    base, index = fid, 2
    while fid in data["folders"]:
        fid = f"{base}-{index}"
        index += 1
    folder = {"id": fid, "name": name, "created_at": _now()}
    data["folders"][fid] = folder
    _write(data)
    return {**folder, "count": 0, "deletable": True}


def rename_folder(fid: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("文件夹名称不能为空")
    data = _read()
    folder = data["folders"].get(fid)
    if not folder:
        raise KeyError("文件夹不存在")
    if fid == UNFILED:
        raise ValueError("未分类不能重命名")
    if any(item.get("name") == name and item_id != fid for item_id, item in data["folders"].items()):
        raise ValueError("同名文件夹已存在")
    folder["name"] = name
    _write(data)
    return folder


def delete_folder(fid: str) -> dict:
    """删除文件夹，资料移到未分类，不删文件。"""
    if fid == UNFILED:
        raise ValueError("未分类不能删除")
    data = _read()
    if fid not in data["folders"]:
        raise KeyError("文件夹不存在")
    moved = 0
    for doc in data["docs"].values():
        if (doc.get("folder_id") or UNFILED) == fid:
            doc["folder_id"] = UNFILED
            doc["updated_at"] = _now()
            moved += 1
    name = data["folders"][fid].get("name") or fid
    del data["folders"][fid]
    _write(data)
    return {"deleted": name, "moved": moved}


def move_doc(did: str, folder_id: str) -> dict | None:
    data = _read()
    doc = data["docs"].get(did)
    if not doc:
        return None
    if folder_id not in data["folders"]:
        raise ValueError("文件夹不存在")
    doc["folder_id"] = folder_id
    doc["updated_at"] = _now()
    _write(data)
    return doc


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
