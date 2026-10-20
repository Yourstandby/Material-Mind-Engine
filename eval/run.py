"""RAG 评测：检索命中率 / 数值准确率 / 引用准确率。

用法：
    python -m eval.run --dry-run          # 只测检索，不调用 LLM（省 token）
    python -m eval.run                    # 完整评测（20 题，约 20 次 LLM 调用）
    python -m eval.run --limit 5          # 只跑前 5 题
    python -m eval.run --only 压缩         # 只跑问题里含某关键词的题

产出：控制台汇总 + eval/report-<时间>.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from core.query_engine import query, retrieve

ROOT = Path(__file__).resolve().parent
QUESTIONS = ROOT / "questions.jsonl"
CATALOG = "catalog"


def norm(text: str) -> str:
    """去掉空白与温度符号，便于数值比对。"""
    return re.sub(r"[\s°℃%]+", "", text or "")


def load(only: str | None = None, limit: int | None = None) -> list[dict]:
    items = []
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(json.loads(line))
    if only:
        items = [item for item in items if only in item["q"]]
    return items[:limit] if limit else items


def evaluate(items: list[dict], top_k: int, dry: bool) -> list[dict]:
    rows = []
    for index, item in enumerate(items, start=1):
        started = time.time()
        try:
            docs, metas = retrieve(item["q"], CATALOG, top_k, item.get("material"))
        except Exception as exc:  # noqa: BLE001
            rows.append({**item, "hit": None, "value_ok": None, "cite_ok": None,
                         "answer": "", "error": f"{type(exc).__name__}: {exc}"})
            print(f"  [{index:>2}] 检索失败: {exc}")
            continue

        sources = [str((m or {}).get("source") or "") for m in metas]
        expect_source = item.get("expect_source")
        hit = any(expect_source in source for source in sources) if expect_source else None

        answer, value_ok, cite_ok = "", None, None
        if not dry:
            try:
                answer, _ = query(item["q"], CATALOG, top_k, item.get("material"))
            except Exception as exc:  # noqa: BLE001
                answer = ""
                print(f"  [{index:>2}] 生成失败: {exc}")
            expect_value = item.get("expect_value")
            if expect_value:
                value_ok = norm(expect_value) in norm(answer)
            cites = [int(x) for x in re.findall(r"\[(\d+)\]", answer)]
            if cites:
                cite_ok = all(1 <= c <= len(sources) for c in cites)

        rows.append({**item, "hit": hit, "value_ok": value_ok, "cite_ok": cite_ok,
                     "answer": answer, "sources": sources, "ms": int((time.time() - started) * 1000)})
        flag = "✓" if (hit is not False and value_ok is not False) else "✗"
        print(f"  [{index:>2}] {flag} 检索={_fmt(hit)} 数值={_fmt(value_ok)} 引用={_fmt(cite_ok)}  {item['q'][:26]}")
    return rows


def _fmt(value) -> str:
    return "—" if value is None else ("是" if value else "否")


def summarize(rows: list[dict], dry: bool) -> dict:
    def rate(key):
        known = [row[key] for row in rows if row.get(key) is not None]
        return (sum(1 for x in known if x) / len(known), len(known)) if known else (None, 0)

    hit, hit_n = rate("hit")
    value, value_n = rate("value_ok")
    cite, cite_n = rate("cite_ok")
    return {"total": len(rows), "hit": hit, "hit_n": hit_n, "value": value, "value_n": value_n,
            "cite": cite, "cite_n": cite_n, "dry": dry}


def report(rows: list[dict], stats: dict) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M")
    path = ROOT / f"report-{stamp}.md"
    pct = lambda x: "—" if x is None else f"{x * 100:.0f}%"
    lines = [
        f"# 评测报告 {stamp}",
        "",
        f"- 题目数：{stats['total']}",
        f"- 检索命中率：{pct(stats['hit'])}（{stats['hit_n']} 题可判定）",
        f"- 数值准确率：{'（dry-run 未测）' if stats['dry'] else pct(stats['value']) + f'（{stats['value_n']} 题可判定）'}",
        f"- 引用有效率：{'（dry-run 未测）' if stats['dry'] else pct(stats['cite']) + f'（{stats['cite_n']} 题可判定）'}",
        "",
        "| # | 问题 | 检索 | 数值 | 引用 | 耗时 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | {row['q']} | {_fmt(row['hit'])} | {_fmt(row['value_ok'])} | "
            f"{_fmt(row['cite_ok'])} | {row.get('ms', 0)}ms |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只测检索，不调用 LLM")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--only")
    args = parser.parse_args()

    items = load(args.only, args.limit)
    if not items:
        print("没有匹配的题目")
        return

    print(f"评测 {len(items)} 题 · top_k={args.top_k} · {'仅检索' if args.dry_run else '完整'}\n")
    rows = evaluate(items, args.top_k, args.dry_run)
    stats = summarize(rows, args.dry_run)
    path = report(rows, stats)

    pct = lambda x: "—" if x is None else f"{x * 100:.0f}%"
    print("\n结果")
    print(f"  检索命中率  {pct(stats['hit'])}  ({stats['hit_n']} 题)")
    if not args.dry_run:
        print(f"  数值准确率  {pct(stats['value'])}  ({stats['value_n']} 题)")
        print(f"  引用有效率  {pct(stats['cite'])}  ({stats['cite_n']} 题)")
    print(f"\n报告：{path}")

    if not args.dry_run and stats["value"] is not None and stats["value"] < 0.9:
        print("提示：数值准确率低于 90%，优先检查抽取质量与被检索到的来源，而不是调提示词。")
        sys.exit(1)


if __name__ == "__main__":
    main()
