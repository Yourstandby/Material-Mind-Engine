"""核心纯函数的单元测试。

用法: python -m pytest tests/
"""

from core.normalize import grade_key, normalize_record, supplier, units
from core.query_engine import expand_query
from ingest.chunk import split


def test_grade_key_strips_x_prefix():
    assert grade_key("X6055B") == "6055B"
    assert grade_key("chemraz 640") == "CHEMRAZ640"


def test_supplier_alias():
    assert supplier("大金") == "Daikin Industries"
    assert supplier("Greene Tweed") == "Greene Tweed"


def test_units_temperature():
    assert units("100°F") == "37.8°C"


def test_units_pressure():
    assert units("100psi") == "0.69 MPa"


def test_units_duration():
    assert units("24 hrs") == "24 h"


def test_normalize_record_adds_keys():
    out = normalize_record({"grade": "X6055B", "supplier": "daikin"})
    assert out["grade_key"] == "6055B"
    assert out["grade_raw"] == "X6055B"
    assert out["supplier"] == "Daikin Industries"


def test_expand_query_adds_temperature_terms():
    assert "temperature range" in expand_query("PEEK 的耐温范围是多少？")


def test_expand_query_keeps_unrelated_question():
    assert expand_query("密度是多少？") == "密度是多少？"


def test_chunk_splits_long_text_with_overlap():
    text = "".join(str(i % 10) for i in range(2000))
    chunks = split(text, size=800, overlap=100)
    assert len(chunks) == 3
    assert all(len(chunk) <= 800 for chunk in chunks)
    assert chunks[0][-100:] == chunks[1][:100]  # 重叠区一致
