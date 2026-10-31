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
    """用 x 投影的空白间隙切列。间隙太窄（例如同一列内两个词之间的空格）不算列边界。"""
    spans = sorted((it["x0"], it["x1"]) for row in rows for it in row)
    if not spans:
        return [(0.0, page_width)]

    merged: list[list[float]] = [list(spans[0])]
    for x0, x1 in spans[1:]:
        if x0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])

    # 合并被极窄间隙隔开的分段：只有明显的列间空白才切
    gap_min = max(8.0, page_width * 0.015)
    bounds: list[list[float]] = [merged[0]]
    for x0, x1 in merged[1:]:
        if x0 - bounds[-1][1] < gap_min:
            bounds[-1][1] = max(bounds[-1][1], x1)
        else:
            bounds.append([x0, x1])
    return [(b[0], b[1]) for b in bounds]


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
        if any(cells):
            grid.append(cells)
    return grid if len(grid) >= 2 else []


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def to_markdown(grid: list[list[str]]) -> str:
    """矩阵 → Markdown 表格，首行当表头。"""
    if not grid:
        return ""
    width = max(len(row) for row in grid)
    grid = [row + [""] * (width - len(row)) for row in grid]
    head, body = grid[0], grid[1:]
    out = ["| " + " | ".join(_md_cell(c) for c in head) + " |"]
    out.append("| " + " | ".join("---" for _ in head) + " |")
    for row in body:
        out.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
    return "\n".join(out)


def _is_table_like(grid: list[list[str]]) -> bool:
    """列数 ≥2，且至少两行有 ≥2 个非空单元格——避免把单列表述硬拆成表格。"""
    if not grid or len(grid[0]) < 2:
        return False
    dense = sum(1 for row in grid if sum(1 for c in row if c.strip()) >= 2)
    return dense >= 2


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
