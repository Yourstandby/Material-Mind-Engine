"""卡片视图配置：默认值、合并校验、查询层落地。"""

import json

import pytest

from core import cardview


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_VIEW_PATH", str(tmp_path / "card_view.json"))
    monkeypatch.setattr(cardview, "_CACHE", (0.0, {}))
    yield


def test_defaults_cover_every_field():
    view = cardview.defaults()
    assert all(field["visible"] for field in view["fields"])
    assert "temp_range" in cardview.visible_keys(view)


def test_save_then_load_roundtrip():
    view = cardview.load()
    view["fields"][0]["label"] = "牌号（自定义）"
    view["columns"] = 2
    cardview.save(view)
    again = cardview.load()
    assert again["fields"][0]["label"] == "牌号（自定义）"
    assert again["columns"] == 2


def test_hidden_field_removed_from_visible_keys():
    view = cardview.load()
    view["fields"][0]["visible"] = False
    cardview.save(view)
    assert view["fields"][0]["key"] not in cardview.visible_keys(cardview.load())


def test_unknown_field_key_ignored():
    view = cardview.load()
    view["fields"].insert(0, {"key": "not_a_field", "label": "幽灵", "visible": True})
    saved = cardview.save(view)
    assert "not_a_field" not in [field["key"] for field in saved["fields"]]


def test_missing_field_is_appended():
    """新增字段（代码里加了新 key）必须自动出现在配置末尾，否则永远显示不出来。"""
    view = cardview.load()
    view["fields"] = [field for field in view["fields"] if field["key"] != "cs"]
    saved = cardview.save(view)
    assert saved["fields"][-1]["key"] == "cs"


def test_numbers_are_clamped():
    view = cardview.save({"records_per_product": 99, "columns": 0})
    assert view["records_per_product"] == 8
    assert view["columns"] == 1


def test_bad_types_fall_back_to_defaults():
    view = cardview.save({"records_per_product": "abc", "columns": None})
    assert view["records_per_product"] == 3
    assert view["columns"] == 3


def test_show_toggles_merge_partially():
    view = cardview.save({"show": {"refs": False}})
    assert view["show"]["refs"] is False
    assert view["show"]["conclusion"] is True  # 没传的保持默认，不能被冲掉


def test_reset_removes_file():
    cardview.save({"columns": 1})
    assert cardview.load()["columns"] == 1
    cardview.reset()
    assert cardview.load()["columns"] == 3


def test_empty_label_falls_back_to_catalog():
    view = cardview.save({"fields": [{"key": "temp_range", "label": "   "}]})
    labels = cardview.labels(view)
    assert labels["temp_range"] == "温度范围"


def test_compare_products_honours_view(monkeypatch):
    from core import query_engine as qe

    products = [{"product_id": "p1", "product_name": "A"}]
    rows = [
        (
            {
                "product_id": "p1",
                "product_name": "A",
                "temp_range": "-20~230°C",
                "test_temp": "200",
                "cs": "18%",
                "source": "a.pdf p.1",
            },
            "",
        )
    ]
    view = cardview.defaults()
    for field in view["fields"]:
        if field["key"] == "cs":
            field["visible"] = False
    view["fields"] = [{"key": "test_temp", "label": "实测温度", "visible": True}] + [
        field for field in view["fields"] if field["key"] != "test_temp"
    ]

    result = qe.compare_products(products, "耐温多少", rows=rows, view=view)
    keys = [column["key"] for column in result["columns"]]
    assert "cs" not in keys
    labels = {column["key"]: column["label"] for column in result["columns"]}
    assert labels["test_temp"] == "实测温度"
    assert keys.index("test_temp") < keys.index("temp_range")
