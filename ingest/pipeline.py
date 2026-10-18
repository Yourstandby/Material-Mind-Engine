"""投喂流水线：解析 → 分块 → 抽取 → 归一化 → 去重 → 向量化 → upsert。

用法（CLI）: python -m ingest.pipeline inbox/*.pdf --collection peek
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import chromadb

from core import embeddings
from core.config import env
from ingest.chunk import split
from ingest.extract import extract
from ingest.parse import parse

MIN_CONFIDENCE = 0.6
QUARANTINE = Path("quarantine.jsonl")
LOG = Path("ingest.log")


def _log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def serialize(record: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in record.items() if v not in (None, "", [], {}))


def _meta(record: dict) -> dict:
    return {k: v for k, v in record.items() if isinstance(v, (str, int, float, bool))}


def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=env("CHROMA_PATH", "./chroma_db"))


def ingest(filename: str, data: bytes, collection: str = "materials"):
    """生成器：逐条抛出进度事件。"""
    if not embeddings.ready():
        yield {"type": "error", "message": "未配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL，无法入库"}
        return

    yield {"type": "stage", "stage": "parse", "file": filename}
    try:
        doc = parse(filename, data)
    except Exception as exc:
        _log(f"[{collection}] {filename} 解析失败: {exc}")
        yield {"type": "error", "message": f"解析失败: {exc}"}
        return

    chars = sum(len(page["text"]) for page in doc["pages"])
    _log(f"[{collection}] {filename} 解析 {len(doc['pages'])} 页 / {chars} 字 / 警告 {len(doc['warnings'])}")

    for warning in doc["warnings"]:
        yield {"type": "warn", "message": warning}
    if not doc["pages"]:
        yield {"type": "error", "message": "没有解析出任何文本（可能是扫描件，需要 OCR）"}
        return

    chunks = []
    for page in doc["pages"]:
        for piece in split(page["text"]):
            chunks.append({"page": page["page"], "text": piece})

    if not chunks:
        yield {"type": "error", "message": f"解析到 {chars} 字，但没有有效分块"}
        return

    _log(f"  切分 {len(chunks)} 块")
    yield {"type": "stage", "stage": "extract", "chunks": len(chunks)}

    records: list[dict] = []
    failed = 0
    last_error = ""
    for index, chunk in enumerate(chunks, start=1):
        label = f"{filename} p.{chunk['page']}"
        try:
            found = extract(chunk["text"], label)
        except Exception as exc:
            failed += 1
            last_error = f"{type(exc).__name__}: {exc}"
            _log(f"  第 {index}/{len(chunks)} 块抽取失败: {last_error}")
            yield {"type": "warn", "message": f"第 {index}/{len(chunks)} 块抽取失败: {exc}"}
            continue
        records.extend(found)
        yield {"type": "progress", "done": index, "total": len(chunks), "found": len(records)}

    _log(f"  抽取 {len(records)} 条（失败块 {failed}/{len(chunks)}）")

    if not records:
        if failed == len(chunks):
            message = f"全部 {len(chunks)} 块抽取失败：{last_error}"
        else:
            message = f"读到了 {chars} 字，但没有可结构化的性能数据（多为描述性文字）"
        yield {"type": "done", "added": 0, "skipped": 0, "quarantined": 0, "message": message}
        return

    keep, quarantined = [], 0
    for record in records:
        if record["confidence"] < MIN_CONFIDENCE:
            quarantined += 1
            with QUARANTINE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            keep.append(record)

    yield {"type": "stage", "stage": "embed", "records": len(keep)}
    documents = [serialize(r) for r in keep]
    ids = [hashlib.md5(d.encode()).hexdigest() for d in documents]

    client = _client()
    model = embeddings.config()["model"]
    store = client.get_or_create_collection(collection, metadata={"embedding_model": model})
    known = (store.metadata or {}).get("embedding_model")
    if known and known != model:
        yield {
            "type": "error",
            "message": f"该知识库由 {known} 构建，当前 embedding 为 {model}，需重建 collection",
        }
        return

    try:
        found_ids = set(store.get(ids=ids).get("ids") or [])
    except Exception:
        found_ids = set()

    try:
        vectors = embeddings.embed(documents)
    except Exception as exc:
        yield {"type": "error", "message": f"向量化失败: {exc}"}
        return

    store.upsert(ids=ids, documents=documents, metadatas=[_meta(r) for r in keep], embeddings=vectors)
    if not known:  # 旧库没有元数据时补写，防止查询回退到默认嵌入
        try:
            store.modify(metadata={"embedding_model": model})
        except Exception:
            pass
    for record in keep:
        yield {"type": "record", "record": record}

    yield {
        "type": "done",
        "added": len(keep) - len(found_ids),
        "skipped": len(found_ids),
        "quarantined": quarantined,
        "total": len(keep),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--collection", default="materials")
    args = parser.parse_args()

    for path in args.files:
        file_path = Path(path)
        print(f"[{file_path.name}]")
        for event in ingest(file_path.name, file_path.read_bytes(), args.collection):
            if event["type"] in ("done", "error"):
                print("  ", json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    main()
