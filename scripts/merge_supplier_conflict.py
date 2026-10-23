"""一次性脚本：合并 3161f20（按 supplier 过滤）在 query_engine.py / server/main.py 的冲突。"""
import sys


def split_conflicts(lines):
    out, blocks, i = [], [], 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<<"):
            out.append(lines[i])
            i += 1
            continue
        i += 1
        ours, theirs, cur = [], [], "ours"
        while i < len(lines) and not lines[i].startswith(">>>>>>>"):
            if lines[i].startswith("======="):
                cur = "theirs"
            elif cur == "ours":
                ours.append(lines[i])
            else:
                theirs.append(lines[i])
            i += 1
        i += 1
        blocks.append((ours, theirs))
    return out, blocks


def merge_query_engine(ours, theirs):
    supplier = [line for line in theirs if line.strip().startswith(("suppliers =", "if suppliers", "supplier_where", "where = supplier_where", "# 问题点名厂商"))]
    merged, inserted = [], False
    for line in ours:
        if not inserted and line.startswith("    kwargs = {"):
            merged.extend(supplier)
            merged.append("")
            inserted = True
        merged.append(line)
    return merged


def merge_server(ours, theirs):
    capture = [line for line in ours if "_capture_failure(" in line]
    body = [line for line in theirs if "_capture_failure(" not in line]
    return capture + body


targets = {
    "core/query_engine.py": merge_query_engine,
    "server/main.py": merge_server,
}

for path, fn in targets.items():
    lines = open(path, encoding="utf-8").read().split("\n")
    # 重建：head + 每个冲突块的合并结果 + 冲突块之间的普通行
    rebuilt, i = [], 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            start = i
            i += 1
            ours, theirs, cur = [], [], "ours"
            while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                if lines[i].startswith("======="):
                    cur = "theirs"
                elif cur == "ours":
                    ours.append(lines[i])
                else:
                    theirs.append(lines[i])
                i += 1
            i += 1
            rebuilt.extend(fn(ours, theirs))
        else:
            rebuilt.append(lines[i])
            i += 1
    text = "\n".join(rebuilt)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(path, "markers left:", text.count("<<<<<<<"), text.count(">>>>>>>"))
