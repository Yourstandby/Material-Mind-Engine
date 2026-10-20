"""失败案例审阅：列出、归类、导出为评测题。

用法：
    python -m scripts.failure_review                # 列出未归类
    python -m scripts.failure_review --stats
    python -m scripts.failure_review --classify 3 A --expected 315
    python -m scripts.failure_review --export 3      # 转成评测题并标记已修复
    python -m scripts.failure_review --resolve 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from core import failures

QUESTIONS = Path(__file__).resolve().parent.parent / "eval" / "questions.jsonl"


def show(items: list[dict]) -> None:
    if not items:
        print("没有待处理的失败案例")
        return
    for item in items:
        verdict = failures.VERDICTS.get(item.get("verdict") or "", item.get("verdict") or "未归类")
        print(f"\n#{item['id']}  {item['ts']}  {item['username'] or '-'}  "
              f"[{item['status']}/{verdict}] 触发={item['trigger']} 分数={item['top_score']}")
        print(f"  问: {item['question'][:80]}")
        print(f"  答: {(item['answer'] or '').replace(chr(10), ' ')[:120]}")
        if item.get("sources"):
            print(f"  源: {', '.join(item['sources'][:3])}")
        if item.get("expected"):
            print(f"  期望: {item['expected']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="列出未归类（默认）")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--classify", nargs=2, metavar=("ID", "VERDICT"))
    parser.add_argument("--expected", help="正确答案（分类时填写）")
    parser.add_argument("--note")
    parser.add_argument("--export", type=int, metavar="ID", help="转成评测题")
    parser.add_argument("--resolve", type=int, metavar="ID")
    args = parser.parse_args()

    if args.stats:
        stats = failures.stats()
        print("状态:", stats["status"])
        print("归类:", stats["verdict"])
        return

    if args.classify:
        fid, verdict = int(args.classify[0]), args.classify[1].upper()
        ok = failures.classify(fid, verdict, args.expected, args.note)
        print("已归类" if ok else "未找到")
        return

    if args.export:
        line = failures.to_eval_line(args.export)
        if not line:
            print("只有 A/B 类失败可以转成评测题")
            return
        with QUESTIONS.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        failures.resolve(args.export, "已转为评测题")
        print("已追加到 eval/questions.jsonl 并标记修复：")
        print(line)
        return

    if args.resolve:
        print("已标记修复" if failures.resolve(args.resolve, args.note) else "未找到")
        return

    show(failures.list_items("open"))


if __name__ == "__main__":
    main()
