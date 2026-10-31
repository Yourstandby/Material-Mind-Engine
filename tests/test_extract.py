"""抽取双输出（长表记录 + 原文片段）的回归测试。"""

import json

import pytest

from ingest import extract as ex


def _fake(monkeypatch, payload):
    def fake_chat(prompt, system=None, temperature=0.1):
        return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(ex, "chat", fake_chat)


def test_extract_all_splits_records_and_passages(monkeypatch):
    _fake(
        monkeypatch,
        {
            "records": [
                {
                    "grade": "P 959",
                    "supplier": "Solvay",
                    "material": "FKM",
                    "cs": "18",
                    "test_temp": "200",
                    "test_duration": "70 h",
                    "confidence": 0.9,
                }
            ],
            "passages": [
                {
                    "text": "该牌号适用于半导体干法刻蚀设备的门阀密封，长期耐等离子体侵蚀。",
                    "kind": "应用",
                    "confidence": 0.8,
                }
            ],
        },
    )
    records, passages = ex.extract_all("资料片段", "ffkm.pdf p.3")
    assert len(records) == 1
    assert records[0]["grade"] == "P 959"
    assert records[0]["source"] == "ffkm.pdf p.3"
    assert len(passages) == 1
    assert passages[0]["kind"] == "应用"
    assert passages[0]["source"] == "ffkm.pdf p.3"


def test_extract_all_accepts_legacy_array(monkeypatch):
    _fake(monkeypatch, [{"grade": "G-101", "confidence": 0.7}])
    records, passages = ex.extract_all("资料", "a.pdf p.1")
    assert len(records) == 1
    assert passages == []


def test_extract_all_survives_chatty_output(monkeypatch):
    body = {
        "records": [{"grade": "X 6055B", "confidence": 0.6}],
        "passages": [{"text": "建议在含胺类添加剂的油品中降低使用温度上限。", "kind": "说明"}],
    }
    _fake(monkeypatch, "结果如下：\n" + json.dumps(body, ensure_ascii=False) + "\n以上。")
    records, passages = ex.extract_all("资料", "b.pdf p.2")
    assert [r["grade"] for r in records] == ["X 6055B"]
    assert len(passages) == 1


def test_short_passages_are_dropped(monkeypatch):
    _fake(monkeypatch, {"records": [], "passages": [{"text": "页脚 3"}, {"text": "版权"}]})
    records, passages = ex.extract_all("资料", "c.pdf p.4")
    assert records == [] and passages == []


def test_unknown_kind_falls_back_to_other(monkeypatch):
    _fake(monkeypatch, {"records": [], "passages": [{"text": "这是一种全氟醚橡胶的通用说明文字。", "kind": "乱写"}]})
    _records_out, passages = ex.extract_all("资料", "d.pdf p.5")
    assert passages[0]["kind"] == "其他"


def test_extract_backward_compatible(monkeypatch):
    _fake(monkeypatch, {"records": [{"grade": "G-1", "confidence": 0.5}], "passages": []})
    assert [r["grade"] for r in ex.extract("资料", "e.pdf p.6")] == ["G-1"]
