"""核心纯函数的单元测试。

用法: python -m pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_cleaner import normalize
from extract import parse_json
from query import expand_query


def test_normalize_temperature():
    assert normalize("100°F") == "37.8°C"


def test_normalize_pressure():
    assert normalize("100psi") == "0.689 MPa"


def test_expand_query_adds_temperature_terms():
    assert "temperature range" in expand_query("PEEK 的耐温范围是多少？")


def test_expand_query_keeps_unrelated_question():
    assert expand_query("密度是多少？") == "密度是多少？"


def test_parse_json_plain_array():
    assert parse_json('[{"a": 1}]') == [{"a": 1}]


def test_parse_json_invalid_returns_empty():
    assert parse_json("no json") == []
