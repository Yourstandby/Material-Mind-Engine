import io

import pytest

pytest.importorskip("PIL")
pytest.importorskip("pypdfium2")

from PIL import Image, ImageDraw  # noqa: E402


def _scan_pdf() -> bytes:
    image = Image.new("RGB", (360, 120), "white")
    ImageDraw.Draw(image).text((10, 40), "SCAN 315C", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=150)
    return buffer.getvalue()


def test_ocrcache_roundtrip(tmp_path, monkeypatch):
    from ingest import ocrcache

    monkeypatch.setattr(ocrcache, "DIR", tmp_path)
    digest = ocrcache.key(b"%PDF-1.4 fake")

    # 没写完成标记（或页数不齐）时不能被采用
    ocrcache.append(digest, 1, "第一页")
    assert ocrcache.load(digest) is None

    ocrcache.append(digest, 2, "第二页")
    ocrcache.finish(digest, 2, 300)
    assert ocrcache.load(digest) == {1: "第一页", 2: "第二页"}

    ocrcache.finish(digest, 3, 300)  # 声明 3 页但只有 2 页 → 视为半成品
    assert ocrcache.load(digest) is None
    ocrcache.clear(digest)


def test_parse_prefers_offline_ocr_cache(tmp_path, monkeypatch):
    from ingest import ocr, ocrcache, parse

    monkeypatch.setattr(ocrcache, "DIR", tmp_path)
    data = _scan_pdf()
    digest = ocrcache.key(data)
    ocrcache.append(digest, 1, "| 牌号 | 硬度 |\n| --- | --- |\n| 315C | 70 |")
    ocrcache.finish(digest, 1, 300)

    # 缓存命中时不应该再调 OCR
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "image_to_text", lambda *a, **k: pytest.fail("不该走在线 OCR"))
    doc = parse.parse("scan.pdf", data)
    assert doc["pages"][0]["text"].startswith("| 牌号 |")
    assert doc["pages"][0]["layout"] == "table"
    assert any("离线 OCR" in warning for warning in doc["warnings"])


def test_disguised_pdf_suffix_is_sniffed(tmp_path, monkeypatch):
    """绕 DLP 常见做法：PDF 改后缀成 .crx。按文件头识别，不靠扩展名。"""
    from ingest import ocr, ocrcache, parse

    monkeypatch.setattr(ocrcache, "DIR", tmp_path)  # 空目录 → 无缓存，走正常 PDF 解析
    monkeypatch.setattr(ocr, "available", lambda: False)

    data = _scan_pdf()
    doc = parse.parse("Spectroscopic.crx", data)
    assert doc["file"] == "Spectroscopic.crx"
    # 无文本层且没 OCR 时才为空；关键是没抛「不支持的格式」
    assert "pages" in doc
    assert not any("不支持的格式" in warning for warning in doc["warnings"])
