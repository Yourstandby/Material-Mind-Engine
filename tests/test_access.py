"""数据分级 + 游客可见范围的单元测试。"""

import pytest

from core import access
from ingest import repo


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "ROOT", tmp_path)
    monkeypatch.setattr(repo, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(access, "POLICY_FILE", tmp_path / "access.json")
    monkeypatch.setattr(access, "env", lambda key, default="": "")
    return tmp_path


def _docs():
    a = repo.register(b"doc-a", "a.pdf", "catalog")
    b = repo.register(b"doc-b", "b.pdf", "catalog")
    return a["doc_id"], b["doc_id"]


def test_new_doc_defaults_to_internal(lib):
    a, b = _docs()
    assert repo.tier_of(repo.get(a)) == access.TIER_INTERNAL
    # 默认游客只能读 public，所以什么都看不到
    assert access.visible_doc_ids("guest") == set()
    assert access.visible_doc_ids("member") is None
    assert access.visible_doc_ids("admin") is None


def test_guest_reads_only_selected_tier(lib):
    a, b = _docs()
    repo.set_tier(a, "public")
    assert access.visible_doc_ids("guest") == {a}

    where = access.where(access.visible_doc_ids("guest"))
    assert where == {"doc_id": {"$in": [a]}}
    assert access.meta_allowed({"doc_id": a}, {a}) is True
    assert access.meta_allowed({"doc_id": b}, {a}) is False
    assert access.meta_allowed({"doc_id": ""}, {a}) is False
    # 未限制（登录账号）时不做任何过滤
    assert access.meta_allowed({}, None) is True


def test_policy_can_open_multiple_tiers(lib):
    a, b = _docs()
    repo.set_tier(a, "public")
    access.save_policy(["public", "internal"])
    assert access.visible_doc_ids("guest") == {a, b}
    access.save_policy(["confidential"])
    assert access.visible_doc_ids("guest") == set()
    # 空集合也要生成一个必定为空的 where，不能退化成「不过滤」
    assert access.where(set()) == {"doc_id": {"$in": [access.NONE_ID]}}


def test_env_override_opens_everything(lib, monkeypatch):
    a, b = _docs()
    monkeypatch.setattr(access, "env", lambda key, default="": "all" if key == "MME_GUEST_TIERS" else default)
    # all 仍然只放开索引里登记过的资料，向量库里的孤儿 chunk 不会漏
    assert access.visible_doc_ids("guest") == {a, b}


def test_folder_bulk_tier(lib):
    a, b = _docs()
    fid = next(f["id"] for f in repo.folders() if f["id"] == "fkm")
    repo.move_doc(a, fid)
    assert repo.set_folder_tier(fid, "public") == 1
    assert repo.tier_of(repo.get(a)) == access.TIER_PUBLIC
    assert repo.tier_of(repo.get(b)) == access.TIER_INTERNAL


def test_reingest_keeps_manual_tier(lib):
    a, _b = _docs()
    repo.set_tier(a, "confidential")
    again = repo.register(b"doc-a", "a.pdf", "catalog")
    assert again["tier"] == access.TIER_CONFIDENTIAL


def test_unknown_tier_falls_back_to_default(lib):
    a, _b = _docs()
    repo.set_tier(a, "whatever")
    assert repo.tier_of(repo.get(a)) == access.TIER_INTERNAL
    assert access.normalize_tier("PUBLIC") == access.TIER_PUBLIC


def test_merge_where(lib):
    base = {"product_id": "solvay__p-100"}
    scope = {"doc_id": {"$in": ["x"]}}
    assert access.merge_where(base, scope) == {"$and": [base, scope]}
    assert access.merge_where(base, None) == base
    assert access.merge_where(None, scope) == scope
    assert access.merge_where(None, None) is None
