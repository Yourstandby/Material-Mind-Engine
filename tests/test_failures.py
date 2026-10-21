import pytest

from core import failures


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(failures, "DB", tmp_path / "failures.db")
    yield tmp_path / "failures.db"
    for suffix in ("", "-wal", "-shm"):
        path = tmp_path / f"failures.db{suffix}"
        if path.exists():
            path.unlink()


def test_record_and_list(temp_db):
    fid = failures.record(
        question="CHEMRAZ 640 的密度？",
        answer="资料不足",
        username="luowei",
        sources=["a.pdf p.1", "b.pdf p.2"],
        top_score=0.42,
        trigger="refusal",
    )
    items = failures.list_items("open")
    assert [x["id"] for x in items] == [fid]
    assert items[0]["username"] == "luowei"
    assert items[0]["sources"] == ["a.pdf p.1", "b.pdf p.2"]
    assert items[0]["trigger"] == "refusal"


def test_classify_and_export(temp_db):
    fid = failures.record(question="q", answer="a", username="u", trigger="manual")
    assert failures.to_eval_line(fid) is None  # 未归类不能导出

    assert failures.classify(fid, "B", expected="315")
    line = failures.to_eval_line(fid)
    assert line is not None
    assert "315" in line and "失败案例" in line


def test_classify_rejects_unknown_verdict(temp_db):
    fid = failures.record(question="q", answer="a")
    with pytest.raises(ValueError):
        failures.classify(fid, "Z")


def test_stats_and_resolve(temp_db):
    fid = failures.record(question="q", answer="a")
    failures.classify(fid, "A")
    failures.resolve(fid)
    stats = failures.stats()
    assert stats["status"].get("fixed") == 1
    assert stats["verdict"].get("A") == 1
