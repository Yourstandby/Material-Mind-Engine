"""目标产品解析 + 结构化对比的回归测试（不联网、不碰 Chroma）。"""

import re

import pytest

from core import query_engine as qe
from core import values as value_parser

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


# ---- 卡片质量：碎片合并 / 单位补齐 / 同源标注 / 区间跨度 ----

FRAGMENT = {
    "product_id": "p1",
    "product_name": "Perlast G75M",
    "supplier": "Perlast",
    "grade": "G75M",
    "material": "FFKM",
    "test_temp": "232",
    "source": "a.pdf p.1",
}
FRAGMENT_2 = {
    "product_id": "p1",
    "product_name": "Perlast G75M",
    "supplier": "Perlast",
    "grade": "G75M",
    "material": "FFKM",
    "test_temp": "232",
    "test_duration": "168 h",
    "source": "a.pdf p.1",
}


def test_fragments_merge_into_one_record():
    """同一份资料被切成多个 chunk，卡片上应该只算一条记录。"""
    merged = qe._merge_fragments([FRAGMENT, FRAGMENT_2], ["test_temp", "test_duration"], [])
    assert len(merged) == 1
    assert set(merged[0]["cells"]) == {"test_temp", "test_duration"}
    assert len(merged[0]["metas"]) == 2


def test_conflicting_fragments_stay_apart():
    """重叠字段值不同 = 不同测试条件，不能并成伪冲突。"""
    other = dict(FRAGMENT_2, test_duration="24 h")
    merged = qe._merge_fragments([FRAGMENT_2, other], ["test_temp", "test_duration"], [])
    assert len(merged) == 2


def test_bare_number_gets_unit_from_field():
    assert value_parser.display("232", "test_temp") == "232 °C"
    assert value_parser.display("168", "test_duration") == "168 h"
    assert value_parser.parse("-20~230°C", "temp_range")["text"] == "-20 ~ 230 °C"


def test_unparsable_value_keeps_original_text():
    assert value_parser.display("室温", "test_temp") == "室温"
    assert value_parser.parse("室温", "test_temp")["numeric"] is False


def test_out_of_range_number_is_not_guessed():
    """越界数字不猜单位：5000 不可能是温度，宁可留裸值。"""
    assert value_parser.parse("5000", "test_temp")["unit"] is None


def test_range_echo_of_test_temp_is_dropped():
    cells = qe._drop_echo_cells({"temp_range": "232 °C", "test_temp": "232 °C"})
    assert "temp_range" not in cells
    assert cells["test_temp"] == "232 °C"


def test_real_range_is_kept():
    cells = qe._drop_echo_cells({"temp_range": "-20 ~ 230 °C", "test_temp": "232 °C"})
    assert "temp_range" in cells


def test_extreme_uses_range_bound_not_first_number():
    """-20~230°C 问最高耐温要答 230，不能答 -20。"""
    assert value_parser.extreme("-20~230°C", "temp_range", True) == 230


def test_conclusion_for_span_question():
    direct = qe.answer_by_products("FFKM 耐高温低温的差距")
    assert direct is not None
    assert "跨度" in direct["text"]


def test_payload_marks_shared_source():
    """数值完全相同的牌号要能识别为同源，而不是两家各自验证过。"""
    result = {
        "products": [{"product_id": "p1", "product_name": "A"}, {"product_id": "p2", "product_name": "B"}],
        "columns": [{"key": "test_temp", "label": "测试温度"}],
        "rows": [
            {"product": "A", "cells": {"test_temp": ["232 °C"]}, "metas": [{}]},
            {"product": "B", "cells": {"test_temp": ["232 °C"]}, "metas": [{}]},
        ],
        "reason": "material",
        "total": 2,
    }
    groups = qe.compare_payload(result)["groups"]
    assert groups[0]["shared_with"] == ["B"]
    assert groups[1]["shared_with"] == ["A"]


def test_single_product_has_no_shared_mark():
    result = {
        "products": [{"product_id": "p1", "product_name": "A"}],
        "columns": [{"key": "test_temp", "label": "测试温度"}],
        "rows": [{"product": "A", "cells": {"test_temp": ["232 °C"]}, "metas": [{}]}],
        "reason": "material",
        "total": 1,
    }
    assert qe.compare_payload(result)["groups"][0]["shared_with"] == []


def test_payload_cards_have_unit_everywhere():
    direct = qe.answer_by_products("FFKM 压缩永久变形")
    for group in direct["payload"]["groups"]:
        for record in group["records"]:
            for field in record["fields"]:
                assert field["label"]
                if field["key"] in ("temp_range", "test_temp", "test_duration", "cs"):
                    # 数值字段必须带单位，裸数字上卡片就是本轮要修的病
                    assert not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", field["value"].strip()), field
