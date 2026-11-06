"""厂商 / 牌号识别：静态词表、库内动态词表、牌号形态。"""

from core import query_engine as q


def test_named_grades_pulls_grade_tokens():
    got = q.named_grades("CHEMRAZ 640 和 Tecnoflon P 959 的压缩永久变形，哪个好？")
    assert got == ["CHEMRAZ 640", "Tecnoflon P 959"]


def test_named_grades_ignores_plain_words():
    assert q.named_grades("氟橡胶和全氟醚橡胶有什么区别？") == []
    assert q.named_grades("") == []


def test_named_suppliers_static_vocabulary():
    assert q.named_suppliers("NOK 的 S-6 和苏威的 P 959 密度对比") == ["NOK", "Syensqo"]
    # 英文按词边界：knock 里的 nok 不算
    assert q.named_suppliers("knock out 那个型号") == []


def test_named_suppliers_uses_library_vocabulary(monkeypatch):
    """库内新供应商没进静态词表也能认出来 —— 这是「待补资料」清单准确的前提。"""
    monkeypatch.setattr(q, "known_suppliers", lambda *a, **k: ["HUTCHINSON", "Zeon Corporation"])
    assert q.named_suppliers("hutchinson 的 O 型圈耐 plasma 吗") == ["HUTCHINSON"]
    # 带公司后缀的库内名，用户只写短名也要命中
    assert q.named_suppliers("ZEON 的 FKM 型号有哪些") == ["Zeon Corporation"]


def test_known_suppliers_survives_broken_store(monkeypatch):
    """Chroma 挂了要退回空词表，不能让问答链路跟着崩。"""
    monkeypatch.setattr(q, "_SUPPLIER_CACHE", (0.0, []))

    def boom(*a, **k):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(q.vs, "collection", boom)
    assert q.known_suppliers() == []
