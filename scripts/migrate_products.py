"""把按材质拆分的知识库合并为单一产品库 catalog。

复用已有向量，不重新 embedding：
    python -m scripts.migrate_products
"""

from __future__ import annotations

import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

import chromadb

from core import catalog
from core.config import env

SRC = ("materials", "fkm", "peek")
TARGET = "catalog"


def main() -> None:
    client = chromadb.PersistentClient(path=env("CHROMA_PATH", "./chroma_db"))
    target = client.get_or_create_collection(TARGET)
    model = (target.metadata or {}).get("embedding_model")

    moved = 0
    for name in SRC:
        try:
            source = client.get_collection(name)
        except Exception:
            continue
        if source.count() == 0:
            continue
        if not model:
            model = (source.metadata or {}).get("embedding_model")

        data = source.get(include=["documents", "metadatas", "embeddings"])
        embeddings = data.get("embeddings")
        if embeddings is None:
            embeddings = []
        ids, docs, metas, vectors = [], [], [], []
        for rid, doc, meta, vector in zip(
            list(data.get("ids") or []),
            list(data.get("documents") or []),
            list(data.get("metadatas") or []),
            embeddings,
        ):
            meta = dict(meta or {})
            supplier = str(meta.get("supplier") or "")
            grade = str(meta.get("grade") or meta.get("grade_raw") or "")
            material = catalog.material_of(meta.get("composition") or "", grade, fallback=name)
            meta["product_id"] = catalog.product_key(supplier, grade)
            meta["product_name"] = catalog.product_name(supplier, grade)
            meta["material"] = material
            meta["material_label"] = catalog.material_label(material)
            ids.append(rid)
            docs.append(doc or "")
            metas.append(meta)
            vectors.append(vector)

        if ids:
            target.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=vectors)
        moved += len(ids)
        print(f"  {name:<10} {len(ids):>3} 条 → {TARGET}")

    if model:
        target.modify(metadata={"embedding_model": model})

    data = target.get(include=["metadatas"])
    groups: dict[str, dict] = {}
    for meta in data.get("metadatas") or []:
        meta = meta or {}
        key = str(meta.get("product_id") or "unknown")
        group = groups.setdefault(
            key,
            {"name": meta.get("product_name"), "material": meta.get("material"),
             "supplier": meta.get("supplier"), "count": 0},
        )
        group["count"] += 1

    print(f"\n{TARGET}: {target.count()} 条 / {moved} 条搬迁 / {len(groups)} 个产品")
    for key, group in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {group['name'][:42]:<44} {group['material']:<5} {group['count']:>3} 条")


if __name__ == "__main__":
    main()
