"""版面还原与表格分块的回归测试。"""

from ingest import layout
from ingest.chunk import split


def _line(text, x0, y0, x1, y1, score=0.95):
    return {"text": text, "score": score, "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def test_render_detects_two_column_table():
    lines = [
        _line("牌号", 10, 10, 60, 30),
        _line("温度", 200, 10, 250, 30),
        _line("时间", 400, 10, 450, 30),
        _line("G-101", 10, 40, 70, 60),
        _line("200°C", 200, 40, 260, 60),
        _line("70 h", 400, 40, 450, 60),
        _line("G-102", 10, 70, 70, 90),
        _line("225°C", 200, 70, 260, 90),
        _line("21 h", 400, 70, 450, 90),
        _line("G-103", 10, 100, 70, 120),
        _line("250°C", 200, 100, 260, 120),
        _line("21 h", 400, 100, 450, 120),
        _line("G-104", 10, 130, 70, 150),
        _line("300°C", 200, 130, 260, 150),
        _line("70 h", 400, 130, 450, 150),
    ]
    text, is_table = layout.render(lines, 500)
    assert is_table is True
    assert text.count("\n") >= 4  # 表头 + 分隔 + 4 行数据
    assert "G-101" in text and "200°C" in text
    # 同一行必须落在同一行，不能把温度绑到下一行牌号
    row = next(l for l in text.splitlines() if "G-101" in l)
    assert "200°C" in row and "70 h" in row


def test_two_column_prose_is_not_a_table():
    """双栅叙述页不能被当成表格重排。"""
    lines = []
    for i in range(6):
        lines.append(_line("这是一个很长的句子说明" if i % 2 == 0 else "另一句同样长的说明文字", 10, i * 20, 250, i * 20 + 16))
        lines.append(_line("右栅同样是完整的叙述句子内容", 300, i * 20, 560, i * 20 + 16))
    text, is_table = layout.render(lines, 600)
    assert is_table is False


def test_render_falls_back_to_plain_text_for_single_column():
    lines = [_line(f"第{i}行说明文字", 10, i * 30, 200, i * 30 + 20) for i in range(5)]
    text, is_table = layout.render(lines, 500)
    assert is_table is False
    assert "第0行说明文字" in text


def test_low_confidence_flags_weak_lines():
    lines = [_line("压缩永久变形 18", 10, 10, 100, 30, score=0.42), _line("200°C", 10, 40, 60, 60, score=0.99)]
    flagged = layout.low_confidence(lines, 0.8)
    assert flagged == ["压缩永久变形 18"]


def test_chunk_keeps_table_header_in_every_piece():
    head = "| 牌号 | 温度 | 时间 |\n| --- | --- | --- |"
    rows = [f"| G{i:03d} | 200°C | 70 h |" for i in range(120)]
    chunks = split("前言。\n\n" + head + "\n" + "\n".join(rows) + "\n\n结尾。", size=400)
    table_pieces = [c for c in chunks if "| G" in c]
    assert len(table_pieces) > 1
    assert all("| 牌号 |" in c for c in table_pieces)
    assert chunks[0].startswith("前言")
    assert chunks[-1].startswith("结尾")
