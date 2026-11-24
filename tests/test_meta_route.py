"""能力型/元问题的确定性路由：不该进检索，也不该花一次 LLM 调用。"""

from core.query_engine import CAPABILITY_TEXT, asks_self, meta_reply


def test_capability_question_routes():
    for question in ("你能作什么?", "你能做什么", "你可以干什么", "你能帮我什么"):
        assert meta_reply(question) == CAPABILITY_TEXT


def test_identity_and_usage_questions_route():
    for question in ("你是谁", "你叫什么", "怎么用", "使用说明", "帮助", "help"):
        assert meta_reply(question) == CAPABILITY_TEXT


def test_colloquial_prefix_still_routes():
    """“那你现在是谁”带口语前缀，之前漏过路由，被当成产品问题出了表格。"""
    for question in ("那你现在是谁？", "那你是谁", "所以你现在是谁", "请问你是谁", "你到底是什么东西"):
        assert meta_reply(question) == CAPABILITY_TEXT


def test_real_domain_question_does_not_route():
    for question in (
        "FFKM 耐温多少",
        "PEEK 的密度",
        "你能查 FFKM 的耐温范围吗",
        "NOK 和 Solvay 对比",
        "这个产品的耐温范围是多少？",
        "你有哪些供应商",
    ):
        assert meta_reply(question) is None


def test_long_question_does_not_route():
    assert meta_reply("你能做什么" + "以及" * 20) is None


def test_empty_question_does_not_route():
    assert meta_reply("") is None
    assert meta_reply("   ") is None


def test_asks_self_blocks_product_table():
    """自我指涉的问题必须走兜底/固定文案，不能被解析成产品查询。"""
    for question in ("那你现在是谁？", "你是谁", "你叫什么", "你能作什么"):
        assert asks_self(question) is True
    for question in ("FFKM 耐温多少", "NOK 有哪些产品", "PEEK 的密度"):
        assert asks_self(question) is False
