"""卡片质量门禁：结构化对比输出的硬性断言。

和 ``eval.run`` 分工不同：``eval.run`` 测「答案对不对」，这里只测「卡片有没有退化」。
卡片是确定性渲染的，质量问题是可判定的，所以可以直接当门禁跑：

    python -m eval.cards                 # 跑 eval/questions.jsonl 里能出卡片的题
    python -m eval.cards --only FFKM     # 只跑问题里含某关键词的
    python -m eval.cards --limit 10

退出码非零表示发现退化。断言全部基于 payload，不读 LLM 输出。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
QUESTIONS = ROOT / "questions.jsonl"

# 卡片里必须带单位的字段：裸数字上卡片会让用户把 232 当成任意东西
UNIT_FIELDS = ("temp_range", "test_temp", "test_duration", "cs", "chem_resistance")
BARE_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


def check_payload(payload: dict) -> list[str]:
    """返回违规清单；空列表表示卡片合格。"""
    problems: list[str] = []
    if not payload:
        return ["payload 为空"]

    groups = payload.get("groups") or []
    if not groups:
        return ["payload 没有任何产品卡片"]

    seen_signatures: set = set()
    total_records = 0
    for group in groups:
        name = group.get("product") or "未标产品"
        records = group.get("records") or []
        if not records:
            problems.append(f"{name}: 卡片里没有记录")
        total_records += len(records)

        field_signatures: set = set()
        for index, record in enumerate(records, start=1):
            fields = record.get("fields") or []
            if not fields:
                problems.append(f"{name} 第 {index} 条: 没有字段")
                continue
            keys = [field.get("key") for field in fields]
            if len(set(keys)) != len(keys):
                problems.append(f"{name} 第 {index} 条: 字段重复 {keys}")
            signature = tuple(sorted((field.get("key"), field.get("value")) for field in fields))
            if signature in field_signatures:
                problems.append(f"{name} 第 {index} 条: 与同产品另一条记录完全相同")
            field_signatures.add(signature)

            for field in fields:
                if not field.get("label"):
                    problems.append(f"{name}: 字段 {field.get('key')} 缺 label")
                if field.get("key") in UNIT_FIELDS and BARE_NUMBER.match(str(field.get("value") or "").strip()):
                    problems.append(
                        f"{name}: {field.get('label')}={field.get('value')} 是裸数字，缺单位"
                    )
        # 整张卡完全相同的两个产品：不是错，但必须被标成同源，否则会被当成两家各自验证
        signature = tuple(
            sorted(
                (field.get("key"), field.get("value"))
                for record in records
                for field in record.get("fields") or []
            )
        )
        if signature and signature in seen_signatures and not group.get("shared_with"):
            problems.append(f"{name}: 与另一张卡片数值完全相同，但没标 shared_with")
        if signature:
            seen_signatures.add(signature)

    declared = payload.get("records")
    if declared is not None and declared != total_records:
        problems.append(f"标题声明 {declared} 条记录，卡片实际 {total_records} 条")

    refs = payload.get("conclusion_refs") or []
    title = payload.get("title") or ""
    if refs and not all(isinstance(ref, int) and ref > 0 for ref in refs):
        problems.append(f"结论引用编号非法: {refs}")
    if payload.get("conclusion") and not refs:
        problems.append("有结论但没有引用编号，无法核对原文")
    if not title:
        problems.append("payload 缺 title")

    return problems


def load(only: str | None = None, limit: int | None = None) -> list[dict]:
    if not QUESTIONS.exists():
        return []
    items = []
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item = json.loads(line)
        if only and only not in item.get("q", ""):
            continue
        items.append(item)
    return items[:limit] if limit else items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="卡片质量门禁")
    parser.add_argument("--only")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    from core.query_engine import answer_by_products

    items = load(args.only, args.limit)
    if not items:
        print("没有可用题目")
        return 0

    checked = 0
    failed = 0
    skipped = 0
    for item in items:
        question = item.get("q") or ""
        try:
            direct = answer_by_products(question)
        except Exception as exc:  # noqa: BLE001 - 单题出错不该中断门禁
            print(f"  [ERR ] {question} → {type(exc).__name__}: {exc}")
            failed += 1
            continue
        if not direct or not direct.get("payload"):
            skipped += 1
            continue
        checked += 1
        problems = check_payload(direct["payload"])
        if problems:
            failed += 1
            print(f"  [FAIL] {question}（{len(problems)} 处）")
            for problem in problems[:6]:
                print(f"         - {problem}")
        else:
            print(f"  [ OK ] {question}")

    print(f"\n检查 {checked} 张卡片，跳过 {skipped} 题（不出卡片），{failed} 张不合格")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
