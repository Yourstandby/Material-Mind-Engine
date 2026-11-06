"""目标产品解析 + 结构化对比的回归测试（不联网、不碰 Chroma）。"""

import pytest

from core import query_engine as qe

PRODUCTS = [
    {
        "product_id": "p1",
        "product_name": "Perlast G75M",
        "supplier": "Perlast",
        "material": "FFKM",
        "material_label": "全氟醚橡胶",
        "records": 3,
        "industries": ["半导体"],
        "parts": ["O形圈"],
        "media": ["等离子"],
    },
    {
        "product_id": "p2",
        "product_name": "Syensqo Tecnoflon P 959",
        "supplier": "Syensqo",
        "material": "FKM",
        "material_label": "氟橡胶",
        "records": 2,
        "industries": ["汽车"],
        "parts": ["密封件"],
        "media": ["油"],
    },
    {
        "product_id": "p3",
        "product_name": "Daikin Industries DAI-EL G-701",
        "supplier": "Daikin Industries",
        "material": "FKM",
        "material_label": "氟橡胶",
        "records": 2,
        "industries": ["化工"],
        "parts": ["密封件"],
        "media": ["酸"],
    },
]

META = {
    "p1": [
        {
            "product_id": "p1",
            "product_name": "Perlast G75M",
            "supplier": "Perlast",
            "grade": "G75M",
            "material": "FFKM",
            "material_label": "全氟醚橡胶",
            "temp_range": "-20~230°C",
            "cs": "18%",
            "test_temp": "200",
            "test_duration": "70 h",
            "source": "perlast.pdf p.3",
            "confidence": 0.9,
        },
        {
            "product_id": "p1",
            "product_name": "Perlast G75M",
            "supplier": "Perlast",
            "grade": "G75M",
            "material": "FFKM",
            "material_label": "全氟醚橡胶",
            "temp_range": "-20~230°C",
            "cs": "18%",
            "test_temp": "200",
            "test_duration": "70 h",
            "source": "perlast.pdf p.3",
            "confidence": 0.9,
        },
    ],
    "p2": [
        {
            "product_id": "p2",
            "product_name": "Syensqo Tecnoflon P 959",
            "supplier": "Syensqo",
            "grade": "P 959",
            "material": "FKM",
            "temp_range": "-20~200°C",
            "cs": "25%",
            "test_temp": "175",
            "test_duration": "70 h",
            "source": "tecnoflon.pdf p.7",
            "confidence": 0.8,
        }
    ],
    "p3": [
        {
            "product_id": "p3",
            "product_name": "Daikin Industries DAI-EL G-701",
            "supplier": "Daikin Industries",
            "grade": "G-701",
            "material": "FKM",
            "temp_range": "-15~180°C",
            "cs": "30%",
            "source": "daikin.pdf p.2",
            "confidence": 0.8,
        }
    ],
}


@pytest.fixture(autouse=True)
def _fake_store(monkeypatch):
    monkeypatch.setattr(qe, "product_stats", lambda *a, **k: list(PRODUCTS))

    def fake_rows(product_ids, names):
        out = []
        for pid, metas in META.items():
            if str(pid) in product_ids or metas[0]["product_name"] in names:
                out.extend((dict(meta), "") for meta in metas)
        return out

    monkeypatch.setattr(qe, "_product_rows", fake_rows)


def test_resolve_by_material():
    out = qe.resolve_products("FFKM 材质是什么")
    assert out["reason"] == "material"
    assert [p["product_id"] for p in out["products"]] == ["p1"]


def test_resolve_by_supplier_alias():
    out = qe.resolve_products("索尔维有哪些氟橡胶牌号")
    assert out["reason"] == "supplier"
    assert [p["product_id"] for p in out["products"]] == ["p2"]


def test_resolve_by_grade_without_brand_prefix():
    out = qe.resolve_products("P 959 的压缩永久变形")
    assert out["reason"] == "grade"
    assert [p["product_id"] for p in out["products"]] == ["p2"]


def test_resolve_by_application():
    out = qe.resolve_products("半导体用 O形圈 有哪些")
    assert out["products"], out
    assert "p1" in [p["product_id"] for p in out["products"]]


def test_resolve_unknown_entity_is_empty():
    out = qe.resolve_products("今天天气怎么样")
    assert out == {"products": [], "reason": "none"}


def test_compare_dedupes_identical_rows():
    resolved = qe.resolve_products("FFKM 压缩永久变形")
    result = qe.compare_products(resolved["products"], "FFKM 压缩永久变形")
    assert len(result["rows"]) == 1
    assert [c["key"] for c in result["columns"]][:2] == ["cs", "test_temp"]


def test_answer_renders_table_with_citation():
    direct = qe.answer_by_products("FFKM 压缩永久变形")
    assert direct is not None
    assert "| 产品 |" in direct["text"]
    assert "[1]" in direct["text"]
    assert "18%" in direct["text"]
    assert direct["sources"][0]["label"] == "Perlast G75M"


def test_answer_conclusion_for_extreme_question():
    direct = qe.answer_by_products("FFKM 和 FKM 哪个耐温最高")
    assert direct is not None
    assert "结论:" in direct["text"]
    assert "230" in direct["text"]


def test_answer_skips_document_questions():
    assert qe.answer_by_products("FFKM 的原始资料是哪份") is None


def test_answer_skips_unknown_products():
    assert qe.answer_by_products("SKF 的轴承型号") is None
