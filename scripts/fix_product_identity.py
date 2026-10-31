"""修正库里的产品身份：厂商名不算产品，文献单独归类。

背景：
1. 旧版 catalog.product_name() 在牌号缺失时退回供应商名，索引里出现了
   product_name = "Solvay" / "3M" / "Chemours"，问答表格的「产品」列和前端
   「按产品浏览」都把厂商当产品展示；
2. 论文/专利/报告本来就没有牌号，它们不是「抽漏了」，却被归成「未标注产品」，
   看上去像解析失败。这里按文献单独归类，置信度回到 0.6。

doc_type 从 library/index.json 按 doc_id 回填。

只改元数据，不动向量、不重新 embedding：
    python -m scripts.fix_product_identity --dry-run
    python -m scripts.fix_product_identity
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from core import catalog
from core.query_engine import CATALOG, _client


def _doc_types() -> dict[str, str]:
    path = Path("library/index.json")
    if not path.exists():
        return {}
    try:
        docs = json.loads(path.read_text(encoding="utf-8")).get("docs") or {}
    except (OSError, json.JSONDecodeError):
        return {}
    items = docs.values() if isinstance(docs, dict) else docs
    return {str(d.get("doc_id")): str(d.get("doc_type") or "") for d in items if d.get("doc_id")}


def main() -> None:
    dry = "--dry-run" in sys.argv
    store = _client().get_or_create_collection(CATALOG)
    data = store.get(include=["metadatas"])
    ids = list(data.get("ids") or [])
    metas = list(data.get("metadatas") or [])
    doc_types = _doc_types()

    changed_ids, changed_metas = [], []
    renamed: dict[tuple[str, str], int] = {}
    for rid, meta in zip(ids, metas):
        meta = dict(meta or {})
        doc_type = str(meta.get("doc_type") or doc_types.get(str(meta.get("doc_id") or "")) or "")
        if doc_type:
            meta["doc_type"] = doc_type
        before = (str(meta.get("product_id") or ""), str(meta.get("product_name") or ""))
        title = catalog.product_title(
            meta.get("supplier") or "",
            meta.get("grade") or "",
            meta.get("product_name") or "",
            doc_type,
        )
        if title == catalog.LITERATURE:
            product_id = "literature"
        elif catalog.is_unnamed(title):
            product_id = catalog.UNNAMED
        else:
            product_id = catalog.product_key(
                meta.get("supplier") or "", meta.get("grade") or title
            )
        after = (product_id, title)

        # 产品身份不明 → 置信度封顶，即便当初 LLM 自报了 0.9
        identity = catalog.identity_confidence(
            meta.get("supplier") or "",
            meta.get("grade") or "",
            meta.get("product_name") or "",
            doc_type,
        )
        raw = meta.get("llm_confidence")
        if raw is None:
            raw = meta.get("confidence")
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            raw = 0.5
        confidence = round(min(raw, identity), 3)

        if (
            after == before
            and meta.get("confidence") == confidence
            and meta.get("identity_confidence") == identity
            and meta.get("doc_type") == (doc_type or None)
        ):
            continue
        renamed[(before, after)] = renamed.get((before, after), 0) + 1
        meta["product_id"], meta["product_name"] = product_id, title
        meta["llm_confidence"], meta["identity_confidence"] = raw, identity
        meta["confidence"] = confidence
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
