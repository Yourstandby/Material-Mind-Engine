"""回填应用字段：行业 / 部件 / 介质 / 认证。

用法：
    python -m scripts.backfill_applications             # 只补元数据（不重算向量）
    python -m scripts.backfill_applications --reembed   # 同时重算向量文本（推荐）

旧数据只有 material / chem_resistance / test_medium 等字段，本脚本按
core.applications 的规则补齐，已存在的标签会保留，不会覆盖人工修正。
"""

from __future__ import annotations

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

from core import applications, embeddings
from core import vector_store as vs

BATCH = 200


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default="catalog")
    parser.add_argument("--reembed", action="store_true", help="按补齐后的字段重算向量")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.reembed and not embeddings.ready():
        print("未配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL，不能重算向量")
        raise SystemExit(1)

    store = vs.client().get_or_create_collection(args.collection)
    data = store.get(include=["metadatas", "documents"])
    ids = data.get("ids") or []
    metas = data.get("metadatas") or []
    docs = data.get("documents") or []
    print(f"{args.collection}: {len(ids)} 条记录")

    total = 0
    touched = 0
    pending_ids: list[str] = []
    pending_meta: list[dict] = []
    pending_doc: list[str] = []

    def flush() -> None:
        nonlocal pending_ids, pending_meta, pending_doc
        if not pending_ids or args.dry_run:
            pending_ids, pending_meta, pending_doc = [], [], []
            return
        if args.reembed:
            vectors = embeddings.embed(pending_doc)
            store.upsert(
                ids=pending_ids,
                metadatas=pending_meta,
                documents=pending_doc,
                embeddings=vectors,
            )
        else:
            store.update(ids=pending_ids, metadatas=pending_meta)
        pending_ids, pending_meta, pending_doc = [], [], []

    for _id, meta, doc in zip(ids, metas, docs):
        meta = dict(meta or {})
        enriched = applications.enrich(meta)
        changed = any(meta.get(k) != v for k, v in enriched.items())
        meta.update(enriched)
        if changed:
            touched += 1
        total += 1
        if args.reembed:
            from ingest.pipeline import serialize

            pending_doc.append(serialize(meta))
        else:
            pending_doc.append(doc or "")
        pending_ids.append(_id)
        pending_meta.append(meta)
        if len(pending_ids) >= BATCH:
            flush()
    flush()

    print(f"已处理 {total} 条，{touched} 条有新增/更新{'（dry-run，未写入）' if args.dry_run else ''}")
    if args.reembed:
        print("向量已按应用字段重算")


if __name__ == "__main__":
    main()
