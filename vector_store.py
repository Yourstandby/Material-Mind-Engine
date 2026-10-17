"""清洗后 JSON -> Chroma 本地向量库（文本序列化 + 嵌入 + 入库）。

用法: python vector_store.py [cleaned.json] [--profile profiles/ffkm.yaml] [--collection ffkm]
      [--persist ./chroma_db] [--reset]

不传 input 时，按 --profile 的 profile_stem 自动读取 raw_json/{profile_stem}/merged.json。
"""
import argparse
import hashlib
import json
from pathlib import Path

import chromadb


def serialize(record):
    return "\n".join(f"{k}: {v}" for k, v in record.items() if v not in (None, "", [], {}))


def resolve_input(args):
    if args.input:
        return Path(args.input)
    return Path("raw_json") / Path(args.profile).stem / "merged.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?")
    parser.add_argument("--profile", default="profiles/ffkm.yaml")
    parser.add_argument("--collection", default="materials")
    parser.add_argument("--persist", default="./chroma_db")
    parser.add_argument("--reset", action="store_true", help="入库前删除并重建当前 collection")
    args = parser.parse_args()

    with open(resolve_input(args), encoding="utf-8") as f:
        records = json.load(f)

    if not records:
        print("[跳过] 无数据")
        return

    client = chromadb.PersistentClient(path=args.persist)
    if args.reset:
        client.delete_collection(args.collection)
    collection = client.get_or_create_collection(args.collection)

    documents = [serialize(r) for r in records]
    ids = [hashlib.md5(d.encode()).hexdigest() for d in documents]
    metadatas = [
        {k: v for k, v in r.items() if isinstance(v, (str, int, float, bool))}
        for r in records
    ]

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"{len(ids)} records -> {args.persist}/{args.collection}")


if __name__ == "__main__":
    main()
