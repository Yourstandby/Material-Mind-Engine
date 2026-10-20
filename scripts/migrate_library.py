"""为已有资料补建文献索引，并把 doc_id 回填进向量库。

用法：
    python -m scripts.migrate_library            # 只建索引 + 回填
    python -m scripts.migrate_library --rename   # 顺带按规范命名重命名文件
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import chromadb

from core.config import env
from ingest import metadata, repo
from ingest.parse import parse

DONE = Path("inbox/_done")


def _client():
    return chromadb.PersistentClient(path=env("CHROMA_PATH", "./chroma_db"))


def build_index() -> dict[tuple[str, str], dict]:
    lookup: dict[tuple[str, str], dict] = {}
    files = [p for p in DONE.rglob("*") if p.is_file() and not p.name.startswith(".")]
    if not files:
        print("inbox/_done 下没有文件")
        return lookup

    for path in sorted(files):
        collection = path.parent.name
        data = path.read_bytes()
        try:
            doc = parse(path.name, data)
        except Exception as exc:
            print(f"  跳过 {path.name}: 解析失败 {exc}")
            continue

        meta = metadata.extract(path.name, data, doc["pages"])
        chars = sum(len(page["text"]) for page in doc["pages"])

        entry = repo.register(
            data=data,
            filename=path.name,
            collection=collection,
            pages=len(doc["pages"]),
            chars=chars,
            records=0,
            meta=meta,
            path=str(path),
        )
        lookup[(collection, path.name)] = entry

        print(
            f"  {path.name[:44]:<46} {meta['doc_type']:<10} "
            f"{str(meta['year'] or '—'):<6} {meta['title'][:38]}"
        )
    return lookup


def backfill(lookup: dict[tuple[str, str], dict]) -> int:
    client = _client()
    total = 0
    for collection in client.list_collections():
        store = client.get_collection(collection.name)
        data = store.get(include=["metadatas"])
        ids, metas = [], []
        for rid, meta in zip(data.get("ids") or [], data.get("metadatas") or []):
            meta = dict(meta or {})
            if meta.get("doc_id"):
                continue
            base = str(meta.get("source") or "").split(" p.")[0]
            entry = lookup.get((collection.name, base))
            if not entry:
                continue
            meta["doc_id"] = entry["doc_id"]
            ids.append(rid)
            metas.append(meta)
        if ids:
            store.update(ids=ids, metadatas=metas)
        print(f"  {collection.name}: 回填 {len(ids)} 条")
        total += len(ids)
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rename", action="store_true", help="按规范命名重命名原始文件")
    args = parser.parse_args()

    print("建立文献索引：")
    lookup = build_index()

    print("回填 doc_id：")
    filled = backfill(lookup)
    repo.sync_counts()

    if args.rename:
        print("应用规范命名：")
        for entry in lookup.values():
            try:
                result = repo.rename(entry["doc_id"])
            except Exception as exc:
                print(f"  {entry['original_name']}: 失败 {exc}")
                continue
            if result["renamed"]:
                print(f"  {entry['original_name'][:40]} → {result['name']}")
        repo.sync_counts()

    docs = repo.all_docs()
    print(f"\n完成：{len(docs)} 份资料，回填 {filled} 条记录")
    print(f"索引：{repo.INDEX.resolve()}")


if __name__ == "__main__":
    main()
