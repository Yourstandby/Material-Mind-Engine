"""打印文件中的冲突块（带行号），用于 rebase 冲突人工合并。

用法: python scripts/show_conflicts.py ingest/parse.py
"""
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8", errors="replace").read().split("\n")

i = 0
while i < len(lines):
    if lines[i].startswith("<<<<<<<"):
        start = i
        sides = {"ours": [], "theirs": []}
        cur = "ours"
        i += 1
        while i < len(lines) and not lines[i].startswith(">>>>>>>"):
            if lines[i].startswith("======="):
                cur = "theirs"
            else:
                sides[cur].append((i + 1, lines[i]))
            i += 1
        print(f"--- conflict at line {start + 1} ---")
        for side in ("ours", "theirs"):
            print(f"[{side}] {len(sides[side])} lines")
            for num, line in sides[side]:
                print(f"{num}: {line}")
    i += 1
