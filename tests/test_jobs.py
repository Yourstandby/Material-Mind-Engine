import time

from server import jobs


def _wait(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.get(job_id)
        if job and job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_job_runs_in_background_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)

    def runner(filename, data, collection):
        yield {"type": "stage", "stage": "parse"}
        yield {"type": "stage", "stage": "extract", "chunks": 3}
        yield {"type": "progress", "done": 1, "total": 3, "found": 2}
        yield {"type": "record", "record": {}}
        yield {"type": "warn", "message": "重复行已合并"}
        yield {"type": "done", "added": 5, "skipped": 1, "quarantined": 0, "duplicates": 1}

    job = jobs.start("demo.pdf", b"x", "catalog", runner)
    assert job["status"] == "running"

    done = _wait(job["id"])
    assert done["status"] == "done"
    assert done["added"] == 5
    assert done["skipped"] == 1
    assert done["chunks"] == 3
    assert done["warns"] == ["重复行已合并"]

    # 刷新页面后按 id 仍能读到，且出现在最近任务里
    assert jobs.get(job["id"])["status"] == "done"
    assert any(item["id"] == job["id"] for item in jobs.recent())


def test_job_error_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)

    def runner(filename, data, collection):
        yield {"type": "stage", "stage": "parse"}
        yield {"type": "error", "message": "解析失败: 扫描件"}

    job = jobs.start("bad.pdf", b"x", "catalog", runner)
    done = _wait(job["id"])
    assert done["status"] == "error"
    assert "扫描件" in done["error"]
