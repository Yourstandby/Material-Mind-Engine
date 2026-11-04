"""文档解析：任意格式 -> {"file","pages":[{"page","text"}],"warnings":[]}"""

import io
import re
from pathlib import Path

from ingest import ocr

TEXTUAL = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log"}


def _broken(text: str) -> bool:
    """文本层几乎不可读时才整页 OCR：缺字过半或篇幅极短还缺字。

    轻微缺字（日文字体没嵌 ToUnicode）不要换 OCR —— OCR 会把表格打散成
    一列一行的流水账，牌号就对不上数据了，反而更难抽取。
    """
    if not text:
        return False
    bad = text.count("\ufffd")
    if not bad:
        return False
    return bad / max(len(text), 1) >= 0.2 or (len(text) < 80 and bad >= 3)


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


def _pdfium_doc(data: bytes):
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(io.BytesIO(data))


def _is_pdf(data: bytes) -> bool:
    """看文件头而不是后缀：.crx 这类伪装后缀里装的常常就是 PDF。"""
    return b"%PDF" in data[:1024]


def _ocrcache_doc(data: bytes, name: str) -> dict | None:
    """预处理（scripts/pdf_ocr.py）跑完的整本 OCR 文本；有就直接用，不再碰 OCR 服务。"""
    from ingest import ocrcache

    pages = ocrcache.load(ocrcache.key(data))
    if not pages:
        return None
    items = [
        {
            "page": number,
            "text": text,
            "layout": "table" if text.lstrip().startswith("|") else "text",
        }
        for number, text in sorted(pages.items())
        if text.strip()
    ]
    warnings = [f"使用离线 OCR 文本层（{len(items)} 页，logs/pdf_ocr_cache）"]
    empty = len(pages) - len(items)
    if empty:
        warnings.append(f"{empty} 页 OCR 后仍无文字，已跳过")
    return {"file": name, "pages": items, "warnings": warnings}


def _table_text(document, index: int, text: str) -> tuple[str, bool]:
    """有文本层的页：只有当版面能还原出表格时才替换成 Markdown 表格。

    普通段落保留原文本——原文本的阅读顺序通常比几何重排更自然，
    盲目重排反而会把跨栏正文拼错。
    """
    if len(text.strip()) < 40:
        return text, False
    # 字体缺 ToUnicode 的页（日文资料常见）字形都是 U+FFFD，重排只会把乱码搅得更乱
    if text.count("\ufffd") > max(3, len(text) * 0.01):
        return text, False
    try:
        from ingest import pdftext

        rebuilt, is_table = pdftext.to_markdown(document, index - 1)
    except Exception:  # noqa: BLE001 - 版面还原失败就退回原文本
        return text, False
    if not is_table or rebuilt.count("|") < 8:
        return text, False
    # ���格还原多了会丢字：字数明显变少就不采用
    if len(rebuilt.replace("|", "").replace(" ", "")) < len(text.strip()) * 0.6:
        return text, False
    return rebuilt, True


def _pdf_pdfium(data: bytes, name: str) -> dict:
    """用 pdfium 解析：AES 加密的 PDF 需要 cryptography，本机没装时靠 pdfium 自带的解密。"""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    pages, warnings = [], []
    for index in range(1, len(document) + 1):
        try:
            text = document[index - 1].get_textpage().get_text_bounded() or ""
        except Exception as exc:  # noqa: BLE001 - 单页失败不影响整份
            text = ""
            warnings.append(f"第 {index} 页解析失败: {type(exc).__name__}: {exc}")

        if not text.strip() and ocr.available():
            try:
                text = ocr.image_to_text(_render_page(data, index), f"{name}-p{index}.png")
                warnings.append(
                    f"第 {index} 页无文本层，已 OCR 识别 {len(text)} 字"
                    if text
                    else f"第 {index} 页无文本层，OCR 未识别出文字"
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"第 {index} 页 OCR 失败: {type(exc).__name__}: {exc}")

        if text.strip() and _broken(text) and ocr.available():
            broken_before = text.count("\ufffd")
            try:
                ocr_text = ocr.image_to_text(_render_page(data, index), f"{name}-p{index}.png")
                if ocr_text and ocr_text.count("\ufffd") < broken_before:
                    text = ocr_text
                    warnings.append(f"第 {index} 页缺字 {broken_before} 处，已 OCR 重新识别")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"第 {index} 页补 OCR 失败: {type(exc).__name__}: {exc}")

        if text.strip() and not _broken(text):
            text, is_table = _table_text(document, index, text)
            if is_table:
                warnings.append(f"第 {index} 页已按版面还原为表格")

        if text.strip():
            pages.append({"page": index, "text": text, "layout": "table" if text.lstrip().startswith("|") else "text"})
        else:
            warnings.append(f"第 {index} 页无文本层（可能是扫描件）")
    return {"file": name, "pages": pages, "warnings": warnings}


def _pdf(data: bytes, name: str):
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:  # 缺 cryptography 时 AES 会抛 DependencyError
                unlocked = 0
            if not unlocked:
                raise RuntimeError("加密且无法用空密码打开")
        pdf_pages = list(reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf 解不开就交给 pdfium
        try:
            result = _pdf_pdfium(data, name)
        except Exception as exc2:  # noqa: BLE001
            return {
                "file": name,
                "pages": [],
                "warnings": [f"PDF 解析失败（{type(exc2).__name__}: {exc2}）；文件可能已加密，请先去掉密码再上传"],
            }
        result["warnings"].insert(0, f"pypdf 无法解析（{type(exc).__name__}），已改用 pdfium")
        return result

    pages, warnings = [], []
    ocr_used = 0
    ocr_limit = ocr.max_pages() if ocr.available() else 0

    document = None  # 延迟打开 pdfium：仅乱码页或扫描页时才用
    for index, page in enumerate(pdf_pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # 单页失败不影响整份
            text = ""
            warnings.append(f"第 {index} 页解析失败: {exc}")

        # pypdf 对部分日文字体映射不出字形（U+FFFD），同一页改用 pdfium 再取一次
        if "\ufffd" in text:
            try:
                if document is None:
                    import pypdfium2 as pdfium

                    document = pdfium.PdfDocument(data)
                alt = document[index - 1].get_textpage().get_text_bounded() or ""
                if alt and alt.count("\ufffd") < text.count("\ufffd"):
                    text = alt
            except Exception:  # noqa: BLE001 - 取不到就保留原文本
                pass

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

        # 文本层有大片缺字（日文字体没嵌 ToUnicode，常见于日文 PDF）：整页 OCR 重来
        if text.strip() and _broken(text) and ocr.available():
            broken_before = text.count("\ufffd")
            try:
                if document is None:
                    import pypdfium2 as pdfium

                    document = pdfium.PdfDocument(data)
                ocr_text = ocr.image_to_text(_render_page(data, index), f"{name}-p{index}.png")
                if ocr_text and ocr_text.count("\ufffd") < broken_before:
                    text = ocr_text
                    warnings.append(f"第 {index} 页缺字 {broken_before} 处，已 OCR 重新识别")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"第 {index} 页补 OCR 失败: {type(exc).__name__}: {exc}")

        if text.strip() and not _broken(text):
            if document is None:
                document = _pdfium_doc(data)
            text, is_table = _table_text(document, index, text)
            if is_table:
                warnings.append(f"第 {index} 页已按版面还原为表格")

        if text.strip():
            pages.append(
                {"page": index, "text": text, "layout": "table" if text.lstrip().startswith("|") else "text"}
            )
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
    # 后缀伪装（.crx 实为 PDF，绕 DLP 时常见）：按文件头识别；文本与 Office 格式仍按后缀走
    disguised = ext not in TEXTUAL and ext not in (".docx", ".xlsx", ".xlsm", ".html", ".htm")
    if ext == ".pdf" or (disguised and _is_pdf(data)):
        cached = _ocrcache_doc(data, filename)
        if cached is not None:
            return cached
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
