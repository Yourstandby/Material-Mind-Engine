"""把长文本切成带重叠的块。"""

import re


def split(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """按段落切块。

    Markdown 表格是原子单元：表格行之间没有空行，不能按字符硬切，
    否则表头和数据被切到两个块里，抽取器只能瞎猜列对应关系。
    超过 size 的表格会单独成块并允许超出上限（宁可大块，不要碎表格）。
    """
    text = (text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""

    for para in paragraphs:
        if _is_table(para):
            # 表格前后断开，让表头与数据待在同一个块里
            if buf:
                chunks.append(buf.strip())
                buf = ""
            for piece in _split_table(para, size):
                chunks.append(piece)
            continue

        if len(para) > size:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            step = max(size - overlap, 200)
            for i in range(0, len(para), step):
                piece = para[i : i + size].strip()
                if piece:
                    chunks.append(piece)
            continue

        if buf and len(buf) + len(para) + 1 > size:
            chunks.append(buf.strip())
            buf = buf[-overlap:]
        buf = f"{buf}\n{para}" if buf else para

    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def _is_table(block: str) -> bool:
    """连续 ≥2 行且都以 | 开头，视为 Markdown 表格块。"""
    lines = [l for l in block.splitlines() if l.strip()]
    rows = sum(1 for l in lines if l.lstrip().startswith("|"))
    return rows >= 2 and rows == len(lines)


def _split_table(block: str, size: int) -> list[str]:
    """表格超长时按行切，但每块都重复表头，保证列语义不丢。"""
    lines = [l for l in block.splitlines() if l.strip()]
    if len("\n".join(lines)) <= size:
        return ["\n".join(lines)]

    head = lines[:2]
    head_len = sum(len(l) + 1 for l in head)
    pieces: list[str] = []
    buf: list[str] = []
    used = head_len
    for line in lines[2:]:
        line_len = len(line) + 1
        if buf and used + line_len > size:
            pieces.append("\n".join(head + buf))
            buf = []
            used = head_len
        buf.append(line)
        used += line_len
    if buf:
        pieces.append("\n".join(head + buf))
    return pieces
