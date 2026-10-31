"""入库任务的可取消性与断点续投语义。"""

import time

import pytest

from server import jobs


@pytest.fixture(autouse=True)
def temp_jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path / "ingest_jobs")
    yield


def test_cancel_marks_running_job():
    job = jobs.create("a.pdf", "catalog")
    assert job["status"] == "running"
    assert jobs.is_cancelled(job["id"]) is False

    updated = jobs.cancel(job["id"])
    assert updated["cancel"] is True
    assert jobs.is_cancelled(job["id"]) is True


def test_cancel_ignores_finished_job():
    job = jobs.create("b.pdf", "catalog")
    jobs.update(job["id"], status="done")
    after = jobs.cancel(job["id"])
    assert after["status"] == "done"
    # 已结束的任务不该因为取消被改写
    assert not after.get("cancel")


def test_is_cancelled_true_when_job_not_running():
    job = jobs.create("c.pdf", "catalog")
    jobs.update(job["id"], status="error")
    assert jobs.is_cancelled(job["id"]) is True


def test_start_passes_cancel_callback_and_stops_early():
    seen = {"calls": 0, "stopped_before_finish": False}

    def runner(filename, data, collection, should_cancel):
        for index in range(5):
            seen["calls"] += 1
            if should_cancel():
                seen["stopped_before_finish"] = True
                yield {"type": "done", "added": 0, "cancelled": True, "message": "已取消"}
                return
            yield {"type": "progress", "done": index + 1, "total": 5}
        yield {"type": "done", "added": 1, "total": 1}

    job = jobs.start("d.pdf", b"x", "catalog", runner, with_cancel=True)
    jobs.cancel(job["id"])

    for _ in range(60):
        current = jobs.get(job["id"])
        if current.get("status") != "running":
            break
        time.sleep(0.05)

    final = jobs.get(job["id"])
    assert final["status"] == "done"
    assert final["cancelled"] is True
    assert seen["stopped_before_finish"] is True
    assert seen["calls"] < 5  # 不必跑满 5 块；取消标记落盘有几毫秒延迟


def test_runner_without_cancel_still_works():
    def runner(filename, data, collection):
        yield {"type": "done", "added": 2, "total": 2}

    job = jobs.start("e.pdf", b"x", "catalog", runner)
    for _ in range(60):
        current = jobs.get(job["id"])
        if current.get("status") != "running":
            break
        time.sleep(0.05)
    assert jobs.get(job["id"])["status"] == "done"
