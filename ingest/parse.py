"""文档解析：任意格式 -> {"file","pages":[{"page","text"}],"warnings":[]}"""

import io
import re
from pathlib import Path

from ingest import ocr

TEXTUAL = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log"}


def decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _render_page(data: bytes, page_no: int) -> bytes:
    """把 PDF 某一页渲染成 PNG（200 DPI），供 OCR 使用。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(io.BytesIO(data))
    page = pdf[page_no - 1]
    bitmap = page.render(scale=200 / 72)
    image = bitmap.to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf(data: bytes, name: str):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages, warnings = [], []
    ocr_used = 0
    ocr_limit = ocr.max_pages() if ocr.available() else 0

    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # 单页失败不影响整份
            text = ""
            warnings.append(f"第 {index} 页解析失败: {exc}")

        if not text.strip() and ocr_limit:
            if ocr_used >= ocr_limit:
                warnings.append(f"第 {index} 页无文本层，已超过 OCR 页数上限（{ocr_limit}）")
            else:
                try:
                    text = ocr.image_to_text(_render_page(data, index), f"{name}-p{index}.png")
                    if text.strip():
                        ocr_used += 1
                        warnings.append(f"第 {index} 页无文本层，已用 OCR 识别")
                except Exception as exc:
                    warnings.append(f"第 {index} 页 OCR 失败: {exc}")

        if text.strip():
            pages.append({"page": index, "text": text})
        else:
            warnings.append(f"第 {index} 页无文本层（可能是扫描件，需配置 OCR）")

    return {"file": name, "pages": pages, "warnings": warnings}


def _docx(data: bytes, name: str):
    from docx import Document

    document = Document(io.BytesIO(data))
    blocks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return {"file": name, "pages": [{"page": 1, "text": "\n".join(blocks)}], "warnings": []}


def _xlsx(data: bytes, name: str):
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    pages, warnings = [], []
    for sheet in book.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            warnings.append(f"工作表 {sheet.title} 为空")
            continue
        header = [str(c) if c is not None else f"列{i + 1}" for i, c in enumerate(rows[0])]
        lines = []
        for row in rows[1:]:
            pairs = [
                f"{header[i]}: {value}"
                for i, value in enumerate(row)
                if value not in (None, "") and i < len(header)
            ]
            if pairs:
                lines.append("; ".join(pairs))
        if lines:
            pages.append({"page": 1, "text": f"[{sheet.title}]\n" + "\n".join(lines)})
    return {"file": name, "pages": pages, "warnings": warnings}


def _html(data: bytes, name: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(decode(data), "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows) + "\n")
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return {"file": name, "pages": [{"page": 1, "text": text}], "warnings": []}


def parse(filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _pdf(data, filename)
    if ext == ".docx":
        return _docx(data, filename)
    if ext in (".xlsx", ".xlsm"):
        return _xlsx(data, filename)
    if ext in (".html", ".htm"):
        return _html(data, filename)
    if ext in TEXTUAL or not ext:
        text = decode(data)
        return {"file": filename, "pages": [{"page": 1, "text": text}], "warnings": []}
    raise ValueError(f"不支持的格式: {ext}")
