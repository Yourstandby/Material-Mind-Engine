"""数字版 PDF 的「文字 + 坐标」。

pypdf / pdfium 的 get_text() 只给纯文本，表格会塌成一维——列头和数据被拼进同一行，
抽取器只能猜哪个温度配哪个数值。这里用 pdfium 的逐字符框（get_charbox）重建行列，
和扫描件走 OCR 一样汇到同一个中间态（ingest/layout.py）。

坐标说明：pdfium 的 charbox 是 (left, bottom, right, top)，且 y 轴向上。
layout 只做相对比较（行聚类按 y、列切分按 x），不关心方向，所以直接换算即可。
"""

from __future__ import annotations


def _median(values: list[float]) -> float:
    values = sorted(v for v in values if v > 0)
    if not values:
        return 0.0
    return values[len(values) // 2]


def page_width(document, index: int) -> float:
    try:
        return float(document[index].get_size()[0])
    except Exception:  # noqa: BLE001
        return 1000.0


def lines_of_page(document, index: int) -> list[dict]:
    """把一页 PDF 的文字拆成「行」，每行带 bbox，供 layout 还原表格。

    index 从 0 开始（pdfium 习惯）；返回的 box 是 rapidocr 风格的 4 点框。
    """
    page = document[index]
    textpage = page.get_textpage()
    total = textpage.count_chars()

    chars = []
    for i in range(total):
        text = textpage.get_text_range(i, 1) or ""
        if not text.strip():  # 跳过空格/换行，词间隙靠几何间距判断
            continue
        try:
            left, bottom, right, top = textpage.get_charbox(i)
        except Exception:  # noqa: BLE001 - 个别字符没有框，跳过即可
            continue
        if right - left <= 0 or top - bottom <= 0:
            continue
        chars.append(
            {
                "text": text,
                "x0": left,
                "x1": right,
                "y0": bottom,
                "y1": top,
                "yc": (bottom + top) / 2,
                "h": top - bottom,
            }
        )

    if not chars:
        return []

    tolerance = max(_median([c["h"] for c in chars]) * 0.5, 1.0)

    # y 向上：从上往下即 y 递减
    chars.sort(key=lambda c: (-c["yc"], c["x0"]))
    rows: list[list[dict]] = []
    for ch in chars:
        if rows:
            last = rows[-1]
            center = sum(c["yc"] for c in last) / len(last)
            if abs(ch["yc"] - center) <= tolerance:
                last.append(ch)
                continue
        rows.append([ch])

    lines = []
    for row in rows:
        row.sort(key=lambda c: c["x0"])
        height = _median([c["h"] for c in row]) or tolerance
        # 词/列边界用「相对字宽」判定，不用字高：字高与字距无关，
        # 用 0.2 倍字高会把 TR10 拆成 TR1 + 0、把 MPa 拆成 M + Pa。
        # 字宽中位数的 0.6 倍以下视为同一词，以上视为新单元。
        char_width = _median([c["x1"] - c["x0"] for c in row]) or height
        gap = max(1.2, char_width * 0.6)
        spans: list[list[dict]] = [[]]
        prev = None
        for ch in row:
            if prev is not None and ch["x0"] - prev["x1"] > gap and spans[-1]:
                spans.append([])
            spans[-1].append(ch)
            prev = ch

        for span in spans:
            text = "".join(c["text"] for c in span).strip()
            if not text:
                continue
            lines.append(
                {
                    "text": text,
                    "score": None,
                    "box": [
                        [span[0]["x0"], min(c["y0"] for c in span)],
                        [span[-1]["x1"], min(c["y0"] for c in span)],
                        [span[-1]["x1"], max(c["y1"] for c in span)],
                        [span[0]["x0"], max(c["y1"] for c in span)],
                    ],
                }
            )
    return lines


def to_markdown(document, index: int) -> tuple[str, bool]:
    """返回（Markdown 或纯文本，是否命中表格）。认不出表格时返回空串，由调用方保留原文本。"""
    from ingest import layout

    lines = lines_of_page(document, index)
    if len(lines) < 4:
        return "", False
    text, is_table = layout.render(lines, page_width(document, index))
    return text, is_table
