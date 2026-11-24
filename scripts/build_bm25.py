"""从向量库现有集合重建 BM25 词法索引（hybrid_bm25.db）。

换 embedding 模型不需要跑这个（词法索引与向量无关）；
只有首次启用混合检索、或索引文件丢失/损坏时才需要。

用法:
    python -m scripts.build_bm25                  # 全部集合
    python -m scripts.build_bm25 --collection catalog
    python -m scripts.build_bm25 --reset          # 先清空再重建
"""

from __future__ import annotations

import argparse
import sys

from core import hybrid
from core import vector_store as vs

BATCH = 400


def collection_names() -> list[str]:
    names = []
    for item in vs.client().list_collections():
        names.append(getattr(item, "name", None) or str(item))
    return sorted({name for name in names if name})


def rebuild_one(name: str, reset: bool = False) -> int:
    if reset:
        hybrid.drop(name)
    store = vs.client().get_or_create_collection(name)
    total, offset = 0, 0
    while True:
        data = store.get(limit=BATCH, offset=offset, include=["documents", "metadatas"])
        ids = data.get("ids") or []
        documents = data.get("documents") or []
        metadatas = data.get("metadatas") or []
        if not ids:
            break
        rows = [
            (rid, doc or "", meta or {})
            for rid, doc, meta in zip(ids, documents, metadatas)
        ]
        total += hybrid.index(name, rows)
        offset += len(ids)
        print(f"  {name}: {offset} 条", flush=True)
        if len(ids) < BATCH:
            break
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建 BM25 词法索引")
    parser.add_argument("--collection", default="", help="只重建指定集合（默认全部）")
    parser.add_argument("--reset", action="store_true", help="先清空该集合的索引再重建")
    args = parser.parse_args(argv)

    targets = [args.collection] if args.collection else collection_names()
    if not targets:
        print("没有可索引的集合", file=sys.stderr)
        return 1

    print(f"索引文件: {hybrid.db_path()}")
    grand = 0
    for name in targets:
        print(f"- {name}", flush=True)
        try:
            grand += rebuild_one(name, reset=args.reset)
        except Exception as exc:  # noqa: BLE001 - 单个集合失败不影响其他集合
            print(f"  ! {name} 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"完成，共索引 {grand} 条，当前索引总量 {hybrid.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
