"""数据守卫：入库前的字段级体检与纠偏，同一套规则也供存量扫描使用。

设计前提：抽取质量不可能靠改写 prompt 一次性解决，脏值一定会进库。所以把「什么算
合法值」写成规则表，在**记录进入系统的那一个卡点**（``ingest.extract._records``）统一
执行，以后换资料、换模型、换 prompt 都自动过一遍，不需要每次人工复查。

每条规则返回 issue，带 ``severity``：
    error  值不可信，已置空（进不了卡片，也不会被当成结论依据）
    warn   已修正或只是标注（例如缺单位：展示层会补，但记一笔便于统计）
    info   定性描述等正常情况

``python -m ingest.guard --dry-run``  只读扫描存量库，看每条规则命中多少条。
``python -m ingest.guard --write``    把修正结果写回 metadata（幂等，可反复跑）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import values

# 物理不可能：任何情况下都不该出现，一律置空（这类值一定是抽错了）
HARD = {
    "temp_range": (-273.0, 1200.0),
    "test_temp": (-273.0, 1200.0),
    "test_duration": (0.0, 100000.0),
    "cs": (0.0, 1000.0),
    "chem_resistance": (0.0, 1000.0),
}

# 领域常见范围：越界只提示、不改值。极端工况真的存在，删了就是把真数据弄丢；
# 但「压缩永久变形 220%」这种量级异常必须留痕，方便回查是抽取错还是牌号特殊。
SOFT = {
    "temp_range": (-100.0, 400.0),
    "test_temp": (-100.0, 400.0),
    "test_duration": (0.0, 10000.0),
    "cs": (0.0, 100.0),
    "chem_resistance": (0.0, 200.0),
}

NUMERIC_FIELDS = tuple(HARD)


def _issue(field: str, code: str, severity: str, raw, detail: str = "") -> dict:
    return {
        "field": field,
        "code": code,
        "severity": severity,
        "raw": "" if raw is None else str(raw)[:120],
        "detail": detail,
    }


def _as_hours(parsed: dict) -> float | None:
    """把已解析的时长统一成小时；不确定单位时返回 None。"""
    if parsed.get("unit") != "min" or parsed.get("max") is None:
        return None
    return round(parsed["max"] / 60, 2)


def audit(record: dict) -> tuple[dict, list[dict]]:
    """体检一条记录：返回（修正后的记录，问题清单）。

    只做能证明的修正（置空、降级、单位换算），不做任何猜测性补全。
    """
    out = dict(record)
    issues: list[dict] = []

    for field in NUMERIC_FIELDS:
        raw = out.get(field)
        if raw in (None, "", [], {}):
            continue
        parsed = values.parse(raw, field)
        if not parsed:
            continue

        if not parsed.get("numeric"):
            # 「优 / 良 / 室温」这类原文留着，只是不能参与比较
            issues.append(_issue(field, "non_numeric", "warn", raw, "无数字，不参与比较"))
            continue

        low, high = HARD[field]
        if parsed["min"] < low or parsed["max"] > high:
            issues.append(
                _issue(field, "out_of_bounds", "error", raw, f"物理上不可能，允许区间 {low:g}~{high:g}")
            )
            out[field] = None
            continue

        soft_low, soft_high = SOFT[field]
        if parsed["min"] < soft_low or parsed["max"] > soft_high:
            issues.append(
                _issue(field, "unusual_value", "warn", raw, f"超出常见范围 {soft_low:g}~{soft_high:g}，保留原值")
            )

        # 时长混入 min：换算成 h，和库里其他记录才能排序
        if field == "test_duration" and parsed.get("unit") == "min":
            hours = _as_hours(parsed)
            if hours is not None:
                issues.append(_issue(field, "duration_unit", "warn", raw, f"换算为 {hours:g} h"))
                out[field] = f"{hours:g} h"
                continue

        # 能按字段安全推断单位的，直接写回值本身：metadata 里就是干净值，
        # 检索、展示、比较都不必再猜一遍。推断规则和展示层共用 core/values.py。
        if parsed.get("inferred") and parsed.get("unit"):
            issues.append(_issue(field, "unit_inferred", "warn", raw, f"按字段补单位 → {parsed['text']}"))
            out[field] = parsed["text"]
            continue

        if parsed.get("unit") is None and field in values.FIELD_UNIT:
            # 推断不出来说明值本身可疑（越界或非数值），记 warn 但不改
            issues.append(_issue(field, "unit_missing", "warn", raw, "无法确定单位"))

    # 温度范围必须是真的上下界。单值多半是把测试温度错填进范围列，
    # 这种情况把值降级到 test_temp（不丢数据），否则置空（不留错语义）。
    range_raw = out.get("temp_range")
    if range_raw and values.single_value_range(range_raw):
        if not out.get("test_temp"):
            out["test_temp"] = values.display(range_raw, "test_temp")
            issues.append(
                _issue("temp_range", "single_value_range", "warn", range_raw, "降级为测试温度")
            )
        else:
            issues.append(
                _issue("temp_range", "single_value_range", "warn", range_raw, "与测试温度重复，置空")
            )
        out["temp_range"] = None

    # 置空后不留空字符串占位，metadata 里空值会让「有值率」统计失真
    for field in NUMERIC_FIELDS:
        if out.get(field) in (None, ""):
            out[field] = None

    return out, issues


def summarize(issues: list[dict]) -> dict:
    """按规则聚合，用于入库日志与存量扫描报告。"""
    stats: dict = {}
    for issue in issues:
        bucket = stats.setdefault(issue["code"], {"field": issue["field"], "severity": issue["severity"], "count": 0})
        bucket["count"] += 1
    return stats


def log_path() -> Path:
    path = Path("logs") / "guard.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def record_issues(issues: list[dict], *, doc_id: str = "", filename: str = "") -> None:
    """落一行审计日志，便于以后统计「哪类资料最容易脏」。"""
    if not issues:
        return
    line = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "doc_id": doc_id,
        "filename": filename,
        "issues": issues,
    }
    try:
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---- 存量扫描：老库里已有的脏值，按同一套规则体检 ----


def scan(collection: str, limit: int | None = None) -> tuple[dict, list[tuple[str, dict, dict]]]:
    """只读扫描一个 collection，返回（统计，待更新的 [(id, 旧meta, 新meta)]）。"""
    import chromadb

    from core.config import env

    client = chromadb.PersistentClient(path=env("CHROMA_PATH", "./chroma_db"))
    store = client.get_or_create_collection(collection)
    data = store.get(include=["metadatas"])
    ids = data.get("ids") or []
    metas = data.get("metadatas") or []

    stats: dict = {}
    changes: list[tuple[str, dict, dict]] = []
    for index, (rid, meta) in enumerate(zip(ids, metas)):
        if limit and index >= limit:
            break
        meta = meta or {}
        fixed, issues = audit(meta)
        for code, bucket in summarize(issues).items():
            stats.setdefault(code, {**bucket, "count": 0})["count"] += bucket["count"]
        # 只回写真正变了的值，避免无意义的全量 update
        patch = {key: fixed[key] for key in NUMERIC_FIELDS if fixed.get(key) != meta.get(key)}
        if patch:
            changes.append((rid, meta, {**meta, **patch}))
    return stats, changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数据守卫：存量记录体检")
    parser.add_argument("--collection", default="catalog")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--write", action="store_true", help="把修正写回 metadata（默认只读）")
    parser.add_argument("--dry-run", action="store_true", help="兼容写法：与默认行为相同，不写回")
    args = parser.parse_args(argv)

    stats, changes = scan(args.collection, args.limit)
    if not stats:
        print("未发现需要修正的记录")
        return 0
    print(f"命中 {sum(b['count'] for b in stats.values())} 处问题，涉及 {len(changes)} 条记录：")
    for code, bucket in sorted(stats.items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {code:<20} {bucket['field']:<16} {bucket['severity']:<6} {bucket['count']:>6}")

    if not args.write:
        print("\n只读模式。加 --write 才会写回 metadata。")
        return 0

    import chromadb

    from core.config import env

    store = chromadb.PersistentClient(path=env("CHROMA_PATH", "./chroma_db")).get_or_create_collection(
        args.collection
    )
    for start in range(0, len(changes), 200):
        batch = changes[start : start + 200]
        store.update(ids=[row[0] for row in batch], metadatas=[row[2] for row in batch])
    print(f"已写回 {len(changes)} 条记录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
