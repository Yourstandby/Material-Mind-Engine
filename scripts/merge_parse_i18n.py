"""一次性脚本：合并 ingest/parse.py 的 i18n 冲突（U+FFFD 乱码回退 + 上游 OCR 逻辑）。

保留 ours（上游 ocr.image_to_text 路径），追加 theirs 的「乱码页用 pdfium 重取」段落。
"""
path = "ingest/parse.py"
lines = open(path, encoding="utf-8").read().split("\n")

out = []
i = 0
while i < len(lines):
    line = lines[i]
    if not line.startswith("<<<<<<<"):
        out.append(line)
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
    i += 1  # skip >>>>>>>

    # theirs 里只取「乱码回退」这一段，OCR 段用上游实现
    head = []
    for item in theirs:
        if item.strip().startswith("if not text.strip() and ocr_base()"):
            break
        head.append(item)
    out.extend(head)
    out.extend(ours)

text = "\n".join(out)
text = text.replace(
    "    for index, page in enumerate(pdf_pages, start=1):",
    "    document = None  # 延迟打开 pdfium：仅乱码页或扫描页时才用\n"
    "    for index, page in enumerate(pdf_pages, start=1):",
    1,
)
open(path, "w", encoding="utf-8", newline="\n").write(text)
print("merged, markers left:", text.count("<<<<<<<"), text.count(">>>>>>>"))
