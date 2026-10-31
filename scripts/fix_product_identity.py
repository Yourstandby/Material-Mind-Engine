"""修正库里的产品身份：只有厂商、没有牌号的记录不能算产品。

背景：旧版 catalog.product_name() 在牌号缺失时退回供应商名，于是索引里
出现了 product_name = "Solvay" / "3M" / "Chemours"，问答表格的「产品」列
和前端「按产品浏览」都会把厂商当产品展示。

本脚本只改元数据，不动向量、不重新 embedding：
    python -m scripts.fix_product_identity --dry-run
    python -m scripts.fix_product_identity
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from core import catalog
from core.query_engine import CATALOG, _client


def main() -> None:
    dry = "--dry-run" in sys.argv
    store = _client().get_or_create_collection(CATALOG)
    data = store.get(include=["metadatas"])
    ids = list(data.get("ids") or [])
    metas = list(data.get("metadatas") or [])

    changed_ids, changed_metas = [], []
    renamed: dict[tuple[str, str], int] = {}
    for rid, meta in zip(ids, metas):
        meta = dict(meta or {})
        before = (str(meta.get("product_id") or ""), str(meta.get("product_name") or ""))
        title = catalog.product_title(
            meta.get("supplier") or "", meta.get("grade") or "", meta.get("product_name") or ""
        )
        product_id = catalog.UNNAMED if catalog.is_unnamed(title) else catalog.product_key(
            meta.get("supplier") or "", meta.get("grade") or title
        )
        after = (product_id, title)
        if after == before:
            continue
        renamed[(before, after)] = renamed.get((before, after), 0) + 1
        meta["product_id"], meta["product_name"] = product_id, title
        changed_ids.append(rid)
        changed_metas.append(meta)

    for (before, after), count in sorted(renamed.items(), key=lambda kv: -kv[1]):
        print(f"  {before[1] or '—':<40} → {after[1]:<24} {count:>4} 条")
    print(f"\n{len(ids)} 条中 {len(changed_ids)} 条需要修正")

    if dry or not changed_ids:
        if dry:
            print("（--dry-run，未写入）")
        return
    store.update(ids=changed_ids, metadatas=changed_metas)
    print(f"已写入 {len(changed_ids)} 条")


if __name__ == "__main__":
    main()
