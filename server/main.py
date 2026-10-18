"""Material Mind Engine — Web API 薄壳（SSE 流式）。

启动: uvicorn server.main:app --reload --port 8000
"""
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import embeddings
from core.config import env, status as config_status
from core.query_engine import (
    build_sources,
    collection_stats,
    delete_source,
    records_of,
    retrieve,
    sample_records,
    search_debug,
    source_stats,
    stream_tokens,
)
from ingest.pipeline import ingest as run_ingest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

@asynccontextmanager
async def lifespan(_app):
    if env("WATCH_INBOX", "1") != "0":
        from ingest.watcher import start as start_watcher

        start_watcher()
    yield


app = FastAPI(title="Material Mind Engine", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)


class Ask(BaseModel):
    question: str
    collection: str = "materials"
    top_k: int = 5


class ReparseIn(BaseModel):
    collection: str = "materials"
    source: str = ""


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/bootstrap")
def bootstrap():
    return {"collections": collection_stats(), "config": config_status()}


@app.get("/api/health")
def health():
    return {**config_status(), "embedding": embeddings.status()}


@app.post("/api/ingest")
async def ingest_file(file: UploadFile = File(...), collection: str = Form("materials")):
    data = await file.read()
    filename = file.filename or "upload"

    # 保存原始文件，供「重新解析」与排查使用
    saved = Path("inbox") / "_done" / collection
    try:
        saved.mkdir(parents=True, exist_ok=True)
        (saved / Path(filename).name).write_bytes(data)
    except OSError:
        pass

    def gen():
        try:
            for event in run_ingest(filename, data, collection):
                yield sse(event)
        except Exception as exc:
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/records")
def records(collection: str = "materials", limit: int = 30):
    return {"items": sample_records(collection, limit)}


@app.post("/api/chat")
def chat(body: Ask):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    def gen():
        started = time.time()
        try:
            docs, metas = retrieve(question, body.collection, body.top_k)
            if not docs:
                yield sse({"type": "error", "message": "该知识库未检索到相关数据，换个问法或换库试试"})
                yield sse({"type": "done"})
                return
            yield sse({"type": "meta", "hits": len(docs), "collection": body.collection})
            yield sse({"type": "sources", "items": build_sources(metas)})
            for token in stream_tokens(question, docs, metas):
                yield sse({"type": "token", "text": token})
            yield sse({"type": "done", "elapsed_ms": int((time.time() - started) * 1000)})
        except KeyError as exc:
            yield sse({"type": "error", "message": f"缺少环境变量 {exc}，请检查 .env"})
        except Exception as exc:  # 前端需要拿到可读原因
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/sources")
def api_sources(collection: str = "all"):
    return {"items": source_stats(collection)}


@app.get("/api/sources/records")
def api_source_records(collection: str = "materials", source: str = "", limit: int = 200):
    if not source:
        raise HTTPException(status_code=400, detail="source 不能为空")
    return {"items": records_of(collection, source, limit)}


@app.delete("/api/sources")
def api_delete_source(collection: str = "materials", source: str = ""):
    if not source:
        raise HTTPException(status_code=400, detail="source 不能为空")
    return {"deleted": delete_source(collection, source)}


@app.get("/api/search")
def api_search(q: str, collection: str = "materials", top_k: int = 8):
    return {"items": search_debug(q, collection, top_k)}


@app.post("/api/sources/reparse")
def api_reparse(body: ReparseIn):
    if not body.source:
        raise HTTPException(status_code=400, detail="source 不能为空")

    root = Path("inbox")
    candidates = [
        root / "_done" / body.collection / body.source,
        root / "_done" / body.source,
        root / body.source,
    ]
    path = next((item for item in candidates if item.exists()), None)
    if path is None:
        raise HTTPException(status_code=404, detail="找不到原始文件（inbox/_done 中不存在）")

    summary = {"added": 0, "skipped": 0, "quarantined": 0}
    for event in run_ingest(path.name, path.read_bytes(), body.collection):
        if event["type"] == "error":
            raise HTTPException(status_code=400, detail=event["message"])
        if event["type"] == "done":
            summary = event
    return summary


app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
