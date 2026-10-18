"""把长文本切成带重叠的块。"""

import re


def split(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""

    for para in paragraphs:
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
