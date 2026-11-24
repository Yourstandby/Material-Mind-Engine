"""数据守卫的规则回归：这条防线以后每次入库都会跑，规则错了会污染整库。"""

from ingest import guard


def codes(issues):
    return {issue["code"] for issue in issues}


def test_single_value_range_downgrades_to_test_temp():
    """232 填进「温度范围」= 错列，值不能丢，要降到测试温度。"""
    fixed, issues = guard.audit({"temp_range": "232"})
    assert fixed["temp_range"] is None
    assert fixed["test_temp"] == "232"
    assert "single_value_range" in codes(issues)


def test_single_value_range_dropped_when_test_temp_present():
    fixed, issues = guard.audit({"temp_range": "232", "test_temp": "180"})
    assert fixed["temp_range"] is None
    assert fixed["test_temp"] == "180"
    assert "single_value_range" in codes(issues)


def test_real_range_kept():
    fixed, issues = guard.audit({"temp_range": "-20~230°C"})
    assert fixed["temp_range"] == "-20~230°C"
    assert "single_value_range" not in codes(issues)


def test_out_of_bounds_value_is_voided():
    """抽错单位的 5000 不能进卡片，也不能拿去比大小。"""
    fixed, issues = guard.audit({"test_temp": "5000"})
    assert fixed["test_temp"] is None
    assert any(issue["severity"] == "error" for issue in issues)


def test_unusual_but_possible_value_kept():
    """压缩永久变形 220% 量级异常，但不能删——极端工况真的存在，只留痕。"""
    fixed, issues = guard.audit({"cs": "220%"})
    assert fixed["cs"] == "220%"
    assert "unusual_value" in codes(issues)


def test_minutes_converted_to_hours():
    fixed, issues = guard.audit({"test_duration": "68 min"})
    assert fixed["test_duration"] == "1.13 h"
    assert "duration_unit" in codes(issues)


def test_hours_kept_as_is():
    fixed, issues = guard.audit({"test_duration": "70 h"})
    assert fixed["test_duration"] == "70 h"
    assert not issues


def test_missing_unit_warns_but_keeps_value():
    fixed, issues = guard.audit({"test_temp": "abc"})
    assert fixed["test_temp"] == "abc"
    assert "non_numeric" in codes(issues)


def test_qualitative_value_survives():
    fixed, issues = guard.audit({"chem_resistance": "优"})
    assert fixed["chem_resistance"] == "优"
    assert "non_numeric" in codes(issues)


def test_empty_values_normalized_to_none():
    fixed, _ = guard.audit({"test_temp": "", "cs": None})
    assert fixed["test_temp"] is None
    assert fixed["cs"] is None


def test_audit_does_not_mutate_input():
    record = {"temp_range": "232"}
    guard.audit(record)
    assert record == {"temp_range": "232"}


def test_summarize_groups_by_code():
    _, issues = guard.audit({"temp_range": "232", "test_temp": "5000"})
    stats = guard.summarize(issues)
    assert stats["single_value_range"]["count"] == 1
    assert stats["out_of_bounds"]["severity"] == "error"


def test_extract_attaches_issues_to_record(monkeypatch):
    """守卫必须挂在入库卡点上，不是只在测试里能跑。"""
    from ingest import extract

    monkeypatch.setattr(extract, "chat", lambda prompt: '{"records": [{"grade": "G75M", "temp_range": "232"}]}')
    records = extract.extract("dummy", "a.pdf p.1")
    assert records[0]["temp_range"] is None
    assert records[0]["test_temp"] == "232"
    assert codes(records[0]["_issues"]) == {"single_value_range"}
