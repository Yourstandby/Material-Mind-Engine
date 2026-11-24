"""能力型/元问题的确定性路由：不该进检索，也不该花一次 LLM 调用。"""

from core.query_engine import CAPABILITY_TEXT, meta_reply


def test_capability_question_routes():
    for question in ("你能作什么?", "你能做什么", "你可以干什么", "你能帮我什么"):
        assert meta_reply(question) == CAPABILITY_TEXT


def test_identity_and_usage_questions_route():
    for question in ("你是谁", "怎么用", "使用说明", "帮助", "help"):
        assert meta_reply(question) == CAPABILITY_TEXT


def test_real_domain_question_does_not_route():
    for question in (
        "FFKM 耐温多少",
        "PEEK 的密度",
        "你能查 FFKM 的耐温范围吗",
        "NOK 和 Solvay 对比",
        "这个产品的耐温范围是多少？",
    ):
        assert meta_reply(question) is None


def test_long_question_does_not_route():
    assert meta_reply("你能做什么" + "以及" * 20) is None


def test_empty_question_does_not_route():
    assert meta_reply("") is None
    assert meta_reply("   ") is None
