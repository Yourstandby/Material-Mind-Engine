"""资料可见级别（游客 / 登录用户 / 管理员）的单元测试。"""

import json

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
    b = repo.register(b"doc-b", "c.pdf", "catalog")
    return a["doc_id"], b["doc_id"]


def test_new_doc_defaults_to_admin_only(lib):
    a, _b = _docs()
    assert repo.tier_of(repo.get(a)) == access.LEVEL_ADMIN
    # 默认只有管理员读得到
    assert access.visible_doc_ids("guest") == set()
    assert access.visible_doc_ids("member") == set()
    assert access.visible_doc_ids("admin") is None


def test_legacy_docs_are_guest_visible(lib):
    """历史资料没有 tier 字段 → 按游客可见，对所有人放开。"""
    _docs()
    store = json.loads(repo.INDEX.read_text(encoding="utf-8"))
    for doc in store["docs"].values():
        doc.pop("tier", None)
    repo.INDEX.write_text(json.dumps(store), encoding="utf-8")

    ids = {doc["doc_id"] for doc in repo.all_docs()}
    assert access.visible_doc_ids("guest") == ids
    assert access.visible_doc_ids("member") == ids
    assert access.level_of_doc({}) == access.LEVEL_GUEST


def test_level_is_cumulative(lib):
    a, b = _docs()
    repo.set_tier(a, "guest")
    repo.set_tier(b, "member")
    assert access.visible_doc_ids("guest") == {a}
    assert access.visible_doc_ids("member") == {a, b}
    assert access.visible_doc_ids("admin") is None

    # 单文档判定：管理员什么都能读，游客只读 guest 级
    doc_guest = {"doc_id": a, "tier": "guest"}
    doc_member = {"doc_id": b, "tier": "member"}
    assert access.can_read_doc("guest", doc_guest) is True
    assert access.can_read_doc("guest", doc_member) is False
    assert access.can_read_doc("member", doc_guest) is True
    assert access.can_read_doc("member", doc_member) is True
    assert access.can_read_doc("admin", doc_member) is True


def test_where_sentinel_and_merge(lib):
    assert access.where(None) is None
    assert access.where(set()) == {"doc_id": {"$in": [access.NONE_ID]}}
    scope = {"doc_id": {"$in": ["x"]}}
    base = {"product_id": "solvay__p-100"}
    assert access.merge_where(base, scope) == {"$and": [base, scope]}
    assert access.merge_where(base, None) == base
    assert access.merge_where(None, scope) == scope
    assert access.merge_where(None, None) is None


def test_meta_allowed_denies_unknown_doc(lib):
    assert access.meta_allowed({"doc_id": "a"}, {"a"}) is True
    assert access.meta_allowed({"doc_id": "b"}, {"a"}) is False
    assert access.meta_allowed({"doc_id": ""}, {"a"}) is False
    assert access.meta_allowed({}, None) is True


def test_folder_bulk_level(lib):
    a, b = _docs()
    fid = next(f["id"] for f in repo.folders() if f["id"] == "fkm")
    repo.move_doc(a, fid)
    assert repo.set_folder_tier(fid, "member") == 1
    assert repo.tier_of(repo.get(a)) == access.LEVEL_MEMBER
    assert repo.tier_of(repo.get(b)) == access.LEVEL_ADMIN


def test_reingest_keeps_manual_level(lib):
    a, _b = _docs()
    repo.set_tier(a, "guest")
    again = repo.register(b"doc-a", "a.pdf", "catalog")
    assert again["tier"] == access.LEVEL_GUEST


def test_reingest_legacy_doc_stays_guest(lib):
    a, _b = _docs()
    store = json.loads(repo.INDEX.read_text(encoding="utf-8"))
    store["docs"][a].pop("tier", None)
    repo.INDEX.write_text(json.dumps(store), encoding="utf-8")
    again = repo.register(b"doc-a", "a.pdf", "catalog")
    assert again["tier"] == access.LEVEL_GUEST


def test_legacy_names_map_to_roles(lib):
    assert access.normalize_level("public") == access.LEVEL_GUEST
    assert access.normalize_level("internal") == access.LEVEL_MEMBER
    assert access.normalize_level("confidential") == access.LEVEL_ADMIN
    assert access.normalize_level("GUEST") == access.LEVEL_GUEST
    assert access.normalize_level("whatever") == access.default_level()


def test_default_level_env_and_policy(lib, monkeypatch):
    assert access.default_level() == access.LEVEL_ADMIN
    monkeypatch.setattr(
        access, "env", lambda key, default="": "member" if key == "MME_DEFAULT_VISIBILITY" else default
    )
    assert access.default_level() == access.LEVEL_MEMBER
    monkeypatch.setattr(access, "env", lambda key, default="": "")
    assert access.save_policy("guest")["default_level"] == access.LEVEL_GUEST
    with pytest.raises(ValueError):
        access.save_policy("nope")
