"""混合检索层（BM25 + RRF + 重排降级）的单元测试。"""

import importlib

import pytest


@pytest.fixture()
def hybrid(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_DB", str(tmp_path / "hybrid.db"))
    monkeypatch.setenv("HYBRID_ENABLED", "1")
    monkeypatch.delenv("HYBRID_RRF_K", raising=False)
    monkeypatch.delenv("HYBRID_VECTOR_WEIGHT", raising=False)
    monkeypatch.delenv("HYBRID_BM25_WEIGHT", raising=False)
    import core.hybrid as module

    module = importlib.reload(module)
    module._CONN = None
    return module


def test_tokenize_keeps_english_and_cjk_bigrams(hybrid):
    tokens = hybrid.tokenize("PEEK 耐温 260°C")
    assert "peek" in tokens
    assert "耐温" in tokens
    assert "260" in tokens


def test_index_and_bm25_roundtrip(hybrid):
    hybrid.index(
        "catalog",
        [
            ("a", "FFKM 全氟醚橡胶 耐温 320°C 耐化学", {"grade": "X6055B", "doc_id": "d1"}),
            ("b", "PEEK 聚醚醚酮 密度 1.3 拉伸强度", {"grade": "450G", "doc_id": "d2"}),
            ("c", "FKM 氟橡胶 耐油", {"grade": "G-551", "doc_id": "d3"}),
        ],
    )
    hits = hybrid.bm25("catalog", "全氟醚橡胶的耐温", limit=5)
    assert hits
    assert hits[0]["id"] == "a"
    assert hits[0]["meta"]["doc_id"] == "d1"
    # 集合隔离
    assert hybrid.bm25("other", "全氟醚橡胶", limit=5) == []


def test_index_is_idempotent_and_delete_works(hybrid):
    hybrid.index("catalog", [("a", "氟橡胶", {})])
    hybrid.index("catalog", [("a", "氟橡胶 耐油 更新", {})])
    assert hybrid.count("catalog") == 1
    assert hybrid.bm25("catalog", "耐油", limit=3)
    hybrid.delete("catalog", ["a"])
    assert hybrid.count("catalog") == 0
    assert hybrid.bm25("catalog", "耐油", limit=3) == []


def test_rrf_prefers_document_in_both_lists(hybrid):
    fused = hybrid.rrf([["a", "b", "c"], ["c", "d"]], k=60, weights=[1.0, 1.0])
    assert fused[0][0] == "c"
    ranked = [rid for rid, _ in fused]
    assert set(ranked) == {"a", "b", "c", "d"}


def test_rrf_respects_weights(hybrid):
    fused = dict(hybrid.rrf([["a"], ["b"]], k=60, weights=[0.0, 1.0]))
    assert "a" not in fused and "b" in fused


def test_rerank_disabled_returns_none(hybrid, monkeypatch):
    monkeypatch.delenv("RERANK_BASE_URL", raising=False)
    monkeypatch.setenv("RERANK_ENABLED", "0")
    assert hybrid.rerank_enabled() is False
    assert hybrid.rerank_scores("q", ["a"]) is None


def test_rerank_rows_degrades_to_original_order(hybrid, monkeypatch):
    monkeypatch.setenv("RERANK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("RERANK_ENABLED", "1")
    monkeypatch.setenv("RERANK_TIMEOUT", "0.2")
    rows = [("doc1", {"doc_id": "1"}, 0.5, 0), ("doc2", {"doc_id": "2"}, 0.4, 1)]
    assert hybrid.rerank_rows("q", rows) == rows


def test_rerank_rows_applies_scores(hybrid, monkeypatch):
    monkeypatch.setattr(hybrid, "rerank_scores", lambda query, texts: [0.1, 0.9])
    rows = [("doc1", {"doc_id": "1"}, 0.5, 0), ("doc2", {"doc_id": "2"}, 0.4, 1)]
    out = hybrid.rerank_rows("q", rows)
    assert [row[0] for row in out] == ["doc2", "doc1"]
    assert [row[3] for row in out] == [0, 1]


def test_status_reports_index(hybrid):
    hybrid.index("catalog", [("a", "氟橡胶", {})])
    info = hybrid.status()
    assert info["ok"] is True
    assert info["enabled"] is True
    assert info["docs"] == 1
    assert info["rrf_k"] == 60
