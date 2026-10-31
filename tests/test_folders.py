import pytest

from ingest import repo


@pytest.fixture(autouse=True)
def isolated_library(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "ROOT", tmp_path)
    monkeypatch.setattr(repo, "INDEX", tmp_path / "index.json")


def _doc(folder_id="", name="a.pdf", data=b"data"):
    return repo.register(data, name, "catalog", folder_id=folder_id)


def test_default_folders_seeded():
    names = {item["name"] for item in repo.folders()}
    assert "未分类" in names
    assert {"氟橡胶", "全氟醚橡胶", "聚醚醚酮"} <= names


def test_create_and_rename_folder():
    folder = repo.create_folder("半导体")
    assert folder["id"]
    with pytest.raises(ValueError):
        repo.create_folder("半导体")
    renamed = repo.rename_folder(folder["id"], "半导体密封")
    assert renamed["name"] == "半导体密封"
    with pytest.raises(ValueError):
        repo.rename_folder("unfiled", "改名")


def test_register_and_move_doc():
    folder = repo.create_folder("汽车")
    doc = _doc(folder["id"])
    assert doc["folder_id"] == folder["id"]
    assert repo.folders()[0]["count"] >= 1 or any(f["count"] == 1 for f in repo.folders())

    moved = repo.move_doc(doc["doc_id"], "peek")
    assert moved["folder_id"] == "peek"
    with pytest.raises(ValueError):
        repo.move_doc(doc["doc_id"], "missing")


def test_delete_folder_moves_docs_to_unfiled():
    folder = repo.create_folder("能源")
    doc = _doc(folder["id"])
    result = repo.delete_folder(folder["id"])
    assert result["moved"] == 1
    assert repo.get(doc["doc_id"])["folder_id"] == "unfiled"
    assert all(f["id"] != folder["id"] for f in repo.folders())
    with pytest.raises(ValueError):
        repo.delete_folder("unfiled")


def test_ensure_folder_is_idempotent():
    first = repo.ensure_folder("氟橡胶", "fkm")
    second = repo.ensure_folder("氟橡胶", "fkm")
    assert first == second == "fkm"
    new = repo.ensure_folder("航空航天", "aerospace")
    assert new == "aerospace"


def test_auto_folder_by_dominant_material(monkeypatch):
    from ingest import pipeline

    calls = []

    def fake_ensure(name, fid=""):
        calls.append((name, fid))
        return fid

    monkeypatch.setattr(pipeline.repo, "ensure_folder", fake_ensure)
    records = [{"material": "FKM"}, {"material": "FKM"}, {"material": "FFKM"}]
    assert pipeline.auto_folder(records) == "fkm"
    assert calls == [("氟橡胶", "fkm")]
    assert pipeline.auto_folder([{"material": "unknown-material"}]) == "unfiled"
