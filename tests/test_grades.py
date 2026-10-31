"""牌号兜底抽取的回归：真实数据表能抓到，标准号/材质不许误伤。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest import grades

DATASHEET = Path("inbox/_done/catalog/E10.pdf")


def test_brand_plus_grade_from_nichias_catalog():
    text = "BLAZER ™ S2\nBLAZER ™ FC\nPERFLUORO PFW ™\nFLUORO ELASTOMER FB"
    found = grades.candidates(text)
    assert "BLAZER S2" in found
    assert "BLAZER FC" in found
    assert "PERFLUORO PFW" in found
    assert "FLUORO ELASTOMER FB" in found


def test_brand_plus_grade_from_eu_datasheet():
    text = "Tecnoflon P 959 | 氟橡胶 | Solvay\nVarox ® DBPH-50"
    found = grades.candidates(text)
    assert "Tecnoflon P 959" in found
    assert "Varox DBPH-50" in found


def test_standards_and_materials_are_not_grades():
    text = "按 ASTM D2000 与 ISO 9001 测试，材质 FKM / FFKM / PTFE，硬度 70 MPa"
    assert grades.candidates(text) == []


def test_pdf_line_wrap_does_not_swallow_next_word():
    # NICHIAS 目录里「BLAZER FC」下一行还是「BLAZER」，不能拼成 BLAZER FC BLAZER
    found = grades.candidates("BLAZER ™ FC\nBLAZER ™ FE\n")
    assert "BLAZER FC" in found
    assert not any(part.count("BLAZER") > 1 for part in found)


def test_backfill_requires_document_level_evidence():
    records = [{"source": "x.pdf p.1"}, {"source": "x.pdf p.2"}]
    pages = {1: "BLAZER ™ S2 数据", 2: "BLAZER ™ S2 数据"}
    assert grades.backfill(records, pages, "x.pdf", "datasheet") == 2
    assert records[0]["grade"] == "BLAZER S2"
    assert records[0]["grade_key"] == "BLAZERS2"

    # 只在单页出现一次 → 视为噪声，不回填
    lonely = [{"source": "y.pdf p.1"}]
    assert grades.backfill(lonely, {1: "BLAZER ™ S2"}, "y.pdf", "datasheet") == 0
    assert "grade" not in lonely[0]


def test_backfill_skips_literature_and_keeps_existing_grade():
    pages = {1: "Tecnoflon P 959 两次 Tecnoflon P 959"}
    lit = [{"source": "p.pdf p.1"}]
    assert grades.backfill(lit, pages, "p.pdf", "paper") == 0

    known = [{"source": "p.pdf p.1", "grade": "FKM-100"}]
    assert grades.backfill(known, pages, "p.pdf", "datasheet") == 0
    assert known[0]["grade"] == "FKM-100"


@pytest.mark.skipif(not DATASHEET.exists(), reason="缺少真实目录样本")
def test_real_nichias_catalog_regression():
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(DATASHEET))
    pages = {i + 1: (page.extract_text() or "") for i, page in enumerate(reader.pages)}
    counts = grades._counts(pages, DATASHEET.name)
    assert counts.get("blazer s2", 0) >= 2
    assert counts.get("fluoro elastomer fb", 0) >= 2
    # 不能把标准号当牌号
    assert not any("as568" == key or key.startswith("iso") for key in counts)


def test_ambiguous_page_is_not_relabelled():
    """目录页同时列多个型号 → 标任何一个都会错，保持未标注。"""
    pages = {1: "BLAZER S2 / BLAZER FC", 2: "BLAZER S2 / BLAZER FC"}
    record = [{"source": "c.pdf p.1"}]
    assert grades.backfill(record, pages, "c.pdf", "datasheet") == 0
    assert "grade" not in record[0]

    single = {1: "BLAZER S2 数据", 2: "BLAZER S2 数据"}
    one = [{"source": "c.pdf p.1"}]
    assert grades.backfill(one, single, "c.pdf", "datasheet") == 1
    assert one[0]["grade"] == "BLAZER S2"
