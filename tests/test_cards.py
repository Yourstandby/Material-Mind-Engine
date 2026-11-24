"""卡片质量门禁的断言自测：断言本身错了，门禁就成了摆设。"""

from eval.cards import check_payload


def card(fields, product="A", refs=(1,), shared=None):
    group = {"product": product, "records": [{"fields": fields, "refs": list(refs)}]}
    if shared is not None:
        group["shared_with"] = shared
    return {
        "title": "命中 1 个产品 · 1 条记录",
        "records": 1,
        "groups": [group],
        "conclusion": "",
        "conclusion_refs": [],
    }


GOOD = [
    {"key": "test_temp", "label": "测试温度", "value": "232 °C"},
    {"key": "test_duration", "label": "测试时长", "value": "168 h"},
]


def test_good_card_passes():
    assert check_payload(card(GOOD)) == []


def test_bare_number_fails():
    problems = check_payload(card([{"key": "test_temp", "label": "测试温度", "value": "232"}]))
    assert any("缺单位" in problem for problem in problems)


def test_duplicate_record_in_same_product_fails():
    payload = card(GOOD)
    payload["groups"][0]["records"].append({"fields": GOOD, "refs": [1]})
    payload["records"] = 2
    assert any("完全相同" in problem for problem in check_payload(payload))


def test_record_count_mismatch_fails():
    payload = card(GOOD)
    payload["records"] = 3
    assert any("实际" in problem for problem in check_payload(payload))


def test_missing_label_fails():
    problems = check_payload(card([{"key": "test_temp", "label": "", "value": "232 °C"}]))
    assert any("缺 label" in problem for problem in problems)


def test_conclusion_without_refs_fails():
    payload = card(GOOD)
    payload["conclusion"] = "结论: A 温度范围 20 ~ 230 °C，跨度 210 °C"
    assert any("引用编号" in problem for problem in check_payload(payload))


def test_conclusion_with_refs_passes():
    payload = card(GOOD)
    payload["conclusion"] = "结论: A 测试温度 232 °C"
    payload["conclusion_refs"] = [1]
    assert check_payload(payload) == []


def test_identical_cards_must_be_marked_shared():
    payload = card(GOOD, product="A")
    second = {"product": "B", "records": [{"fields": GOOD, "refs": [2]}], "shared_with": []}
    payload["groups"].append(second)
    payload["records"] = 2
    assert any("shared_with" in problem for problem in check_payload(payload))

    second["shared_with"] = ["A"]
    assert check_payload(payload) == []


def test_empty_payload_fails():
    assert check_payload({}) == ["payload 为空"]
    assert check_payload({"groups": []}) == ["payload 没有任何产品卡片"]
