"""版面还原：把 OCR 的「文字 + bbox」重建成二维行列。

RapidOCR 输出的是一堆无序文本框，直接拼成一串文字喂给 LLM，表格必然塌成一维：
「-20 200 18 70h」这种顺序错乱会让抽取器把 A 行的温度绑到 B 行的数值上。

这里按几何位置还原：
    1. 行分桶：按 y 中心聚类，容差取行高中位数的一半；
    2. 列切分：把整页所有文本框的 x 区间做投影，投影里的空白间隙就是列边界；
    3. 同列文本按行拼接，输出 Markdown 表格（首行视为表头）。

只依赖几何，不做语义判断——语义留给下游 LLM。
"""

from __future__ import annotations

import re


def _box(line: dict) -> tuple[float, float, float, float]:
    """把 rapidocr 的 4 点框归一成 (x0, y0, x1, y1)。"""
    raw = line.get("box") or line.get("boxes") or []
    try:
        points = [(float(p[0]), float(p[1])) for p in raw]
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0, 0.0, 0.0)
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _rows(lines: list[dict], tolerance: float) -> list[list[dict]]:
    """按 y 中心聚类成行，行内按 x 排序。"""
    items = []
    for line in lines:
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        x0, y0, x1, y1 = _box(line)
        items.append({"text": text, "y": (y0 + y1) / 2, "x0": x0, "x1": x1, "h": y1 - y0})
    items.sort(key=lambda it: (it["y"], it["x0"]))

    rows: list[list[dict]] = []
    for item in items:
        if rows:
            last = rows[-1]
            center = sum(it["y"] for it in last) / len(last)
            if abs(item["y"] - center) <= tolerance:
                last.append(item)
                continue
        rows.append([item])
    for row in rows:
        row.sort(key=lambda it: it["x0"])
    return rows


def _column_bounds(rows: list[list[dict]], page_width: float) -> list[tuple[float, float]]:
    """找列边界：只有「多数行都在此空白」的 x 区间才算列间隙（gutter）。

    早先的做法是把全页所有文本框做并集再找空隙，但标题、跨栏长句会横跨全宽，
    整页归一列，表格就永远切不出来。改成按行多数投票：
    某个 x 位置被少于 40% 的行覆盖，才算真正的列间空白。
    """
    if not rows:
        return [(0.0, page_width)]

    width = max(int(page_width) + 1, 2)
    cover = [0] * width
    for row in rows:
        spans = sorted((it["x0"], it["x1"]) for it in row)
        merged: list[list[float]] = []
        for x0, x1 in spans:
            if merged and x0 <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], x1)
            else:
                merged.append([x0, x1])
        for x0, x1 in merged:
            for x in range(max(0, int(x0)), min(int(x1) + 1, width)):
                cover[x] += 1

    # 列间隙的特征是「几乎所有行都空」，而标题/图注这类跨宽行最多 +1 票，不会把列内容区变成通道。
    # 阈值取低一些：表格页往往还夹着标题、图注、页码，这些行占了行数很大比例，
    # 用 40% 会把整页都判成通道，表格永远切不出来。
    threshold = max(2, int(len(rows) * 0.15))
    # 数值列之间的空白往往只有 4~8pt，下限取 8pt 时相邻两列会并成一格（如「15 18」）。
    # 误切的代价由「多数行必须都空白」这个条件把住，不靠大阈值。
    gap_min = max(3.0, page_width * 0.006)

    # 把「低覆盖率」的连续区间当成列间隙
    columns: list[list[float]] = []
    run_start = None
    for x in range(width):
        if cover[x] < threshold:
            if run_start is None:
                run_start = x
            continue
        if run_start is not None:
            if x - run_start >= gap_min:
                columns.append([run_start, x])
            run_start = None
    if run_start is not None and width - run_start >= gap_min:
        columns.append([run_start, width - 1])

    if not columns:
        return [(0.0, page_width)]

    # 间隙之外的部分就是列
    bounds: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in columns:
        if start - cursor > 1:
            bounds.append((cursor, float(start)))
        cursor = float(end)
    if page_width - cursor > 1:
        bounds.append((cursor, page_width))

    # 切得太碎（超过 15 列）说明这不是规整表格，宁可退回单列跑纯文本
    if not 2 <= len(bounds) <= 15:
        return [(0.0, page_width)]
    return bounds


def _cell_index(x_center: float, bounds: list[tuple[float, float]]) -> int:
    """文本中心的 x 落在哪个列区间；落在间隙里就取最近的列。"""
    best, best_dist = 0, float("inf")
    for index, (x0, x1) in enumerate(bounds):
        if x0 <= x_center <= x1:
            return index
        dist = min(abs(x_center - x0), abs(x_center - x1))
        if dist < best_dist:
            best, best_dist = index, dist
    return best


def to_table(lines: list[dict], page_width: float = 1000.0) -> list[list[str]]:
    """返回二维单元格矩阵；行数不足 2 或列数不足 2 时返回空列表（说明不是表格）。"""
    heights = [(_box(l)[3] - _box(l)[1]) for l in lines if str(l.get("text") or "").strip()]
    heights = [h for h in heights if h > 0]
    tolerance = (sorted(heights)[len(heights) // 2] if heights else 20.0) * 0.6

    rows = _rows(lines, tolerance)
    if len(rows) < 2:
        return []
    bounds = _column_bounds(rows, page_width)
    if len(bounds) < 2:
        return []

    grid: list[list[str]] = []
    for row in rows:
        cells = [""] * len(bounds)
        for item in row:
            index = _cell_index((item["x0"] + item["x1"]) / 2, bounds)
            cells[index] = f"{cells[index]} {item['text']}".strip()
        # 只占一列的“行”通常是页眉、图注、页码，混进来会把表头挤到第一行
        if sum(1 for c in cells if c.strip()) >= 2:
            grid.append(cells)
    return grid if len(grid) >= 2 else []


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _looks_like_header(row: list[str]) -> bool:
    """首行是不是真实表头（而非页眉）。

    页眉的典型特征：夹着页码、或只有文档标题那一两个横跨多列的长单元格。
    误把页眉当表头，下游 LLM 拿到的列名就是文档名，从此对错列。
    """
    cells = [c.strip() for c in row if c.strip()]
    if len(cells) < 3:
        return False
    # 单独的数字格子：页码、页脚
    if any(re.fullmatch(r"\d{1,3}", c) for c in cells):
        return False
    # 表头单元格都很短；含长句说明这是正文被切进了表格
    return all(len(c) <= 12 for c in cells)


def to_markdown(grid: list[list[str]], head: list[str] | None = None) -> str:
    """矩阵 → Markdown 表格。

    head 为空时用首行；首行不像表头（页眉/文档标题）则改用「列1..列N」占位，
    并把原首行降为数据行——错误列名比无名列危害大，至少不丢数据。
    """
    if not grid:
        return ""
    width = max(len(row) for row in grid)
    grid = [row + [""] * (width - len(row)) for row in grid]

    if head is not None:
        names = list(head)[:width] + [""] * max(0, width - len(head))
        body = grid
    elif _looks_like_header(grid[0]):
        names = grid[0]
        body = grid[1:]
    else:
        names = [f"列{i}" for i in range(1, width + 1)]
        body = grid

    out = ["| " + " | ".join(_md_cell(c) for c in names) + " |"]
    out.append("| " + " | ".join("---" for _ in names) + " |")
    for row in body:
        out.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
    return "\n".join(out)


def _is_table_like(grid: list[list[str]]) -> bool:
    """判断是否真是表格，宁严不宽。

    误判的代价比漏判大：双栏叙述页被当成表格后会重排，把原本正确的阅读顺序弄乱。
    表格的特征是「多列、多行、单元格短」：叙述行的单元格是一整句，长度骗不了人。
    """
    if not grid or len(grid[0]) < 3 or len(grid) < 4:
        return False
    dense_rows = [row for row in grid if sum(1 for c in row if c.strip()) >= 3]
    if len(dense_rows) < 4:
        return False
    if len(dense_rows) / len(grid) < 0.6:
        return False
    lengths = [len(c.strip()) for row in dense_rows for c in row if c.strip()]
    if not lengths:
        return False
    lengths.sort()
    # 中位数超 20 字、或尾部有长句，都说明这是被切碎的整句段落
    if lengths[len(lengths) // 2] > 20:
        return False
    if lengths[int(len(lengths) * 0.9)] > 30:
        return False

    # 真表格通常至少有 2 列是数值列；叙述页的数字散在各列，凑不出来
    numeric_cols = 0
    for col in range(len(grid[0])):
        values = [row[col].strip() for row in dense_rows if row[col].strip()]
        if len(values) < 2:
            continue
        if sum(1 for v in values if re.search(r"\d", v)) / len(values) >= 0.5:
            numeric_cols += 1
    return numeric_cols >= 2


def render(lines: list[dict], page_width: float = 1000.0) -> tuple[str, bool]:
    """输入 OCR lines，返回（重建文本，是否命中表格）。

    命中表格时输出 Markdown 表格；否则按行输出纯文本，保持原有阅读顺序。
    """
    if not lines:
        return "", False

    grid = to_table(lines, page_width)
    if _is_table_like(grid):
        return to_markdown(grid), True

    rows = _rows(lines, 20.0)
    text = "\n".join(" ".join(it["text"] for it in row) for row in rows if row)
    return text, False


def low_confidence(lines: list[dict], threshold: float = 0.8) -> list[str]:
    """置信度偏低的文本，入库前应进人工复核而不是直接写数值。"""
    out = []
    for line in lines or []:
        try:
            score = float(line.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        text = str(line.get("text") or "").strip()
        if text and score and score < threshold:
            out.append(text)
    return out


_NUMBERISH = re.compile(r"\d")


def has_number(text: str) -> bool:
    return bool(_NUMBERISH.search(text or ""))
