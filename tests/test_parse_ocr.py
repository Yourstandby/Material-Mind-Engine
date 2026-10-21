import io

import pytest

pytest.importorskip("PIL")
pytest.importorskip("pypdfium2")

from PIL import Image, ImageDraw  # noqa: E402


def _scan_pdf() -> bytes:
    """生成一个没有文字层的 PDF（整页是图片），模拟扫描件。"""
    image = Image.new("RGB", (360, 120), "white")
    ImageDraw.Draw(image).text((10, 40), "SCAN 315C", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=150)
    return buffer.getvalue()


def test_scanned_pdf_uses_ocr(monkeypatch):
    from ingest import ocr, parse

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "max_pages", lambda: 5)
    monkeypatch.setattr(ocr, "image_to_text", lambda data, filename="": "OCR TEXT 315")

    doc = parse.parse("scan.pdf", _scan_pdf())
    assert doc["pages"], "OCR 结果应写入 pages"
    assert "OCR" in doc["pages"][0]["text"]
    assert any("OCR" in warning for warning in doc["warnings"])


def test_scanned_pdf_without_ocr_warns(monkeypatch):
    from ingest import ocr, parse

    monkeypatch.setattr(ocr, "available", lambda: False)
    doc = parse.parse("scan.pdf", _scan_pdf())
    assert doc["pages"] == []
    assert any("扫描件" in warning for warning in doc["warnings"])
