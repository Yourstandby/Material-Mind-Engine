"""Material Mind Engine — Web API 薄壳（SSE 流式）。

启动: uvicorn server.main:app --reload --port 8000
"""
import json
import mimetypes
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import auth, embeddings
from core import vector_store as vs
from core.config import env, status as config_status
from core.query_engine import (
    CATALOG,
    build_sources,
    collection_stats,
    delete_source,
    doc_products,
    material_stats,
    product_stats,
    records_of,
    retrieve,
    sample_records,
    search_debug,
    source_stats,
    stream_tokens,
)
from ingest import repo
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
    collection: str = "catalog"
    material: str = "all"
    top_k: int = 5


class ReparseIn(BaseModel):
    collection: str = "materials"
    source: str = ""


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/bootstrap")
def bootstrap():
    materials = material_stats()
    return {
        "collections": materials,
        "materials": materials,
        "products": product_stats(),
        "catalog": CATALOG,
        "config": config_status(),
    }


@app.get("/api/products")
def api_products(material: str = "all"):
    return {"items": product_stats(None if material == "all" else material)}


@app.get("/api/health")
def health():
    return {**config_status(), "embedding": embeddings.status(), "chroma": vs.health()}


@app.post("/api/ingest")
async def ingest_file(
    file: UploadFile = File(...),
    collection: str = Form("catalog"),
    material: str = Form(""),
):
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
            for event in run_ingest(filename, data, collection, material):
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
            docs, metas = retrieve(question, body.collection, body.top_k, material=body.material)
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
    for event in run_ingest(path.name, path.read_bytes(), CATALOG):
        if event["type"] == "error":
            raise HTTPException(status_code=400, detail=event["message"])
        if event["type"] == "done":
            summary = event
    return summary


class DocPatch(BaseModel):
    title: str | None = None
    authors: str | None = None
    year: int | None = None
    doc_type: str | None = None
    publisher: str | None = None
    doi: str | None = None
    patent_no: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


def _library_item(doc: dict, products: dict) -> dict:
    path = Path(doc.get("path") or "")
    name = path.name or doc.get("canonical_name") or doc.get("original_name") or ""
    return {
        **doc,
        **(products.get(doc.get("doc_id"), {})),
        "source": name,
        "kind": (Path(name).suffix.lstrip(".") or "file").lower(),
    }


@app.get("/api/library")
def api_library(collection: str = "all", material: str = "all"):
    products = doc_products()
    items = [_library_item(doc, products) for doc in repo.all_docs()]
    if material and material != "all":
        items = [item for item in items if item.get("material") == material]
    if collection != "all":
        items = [item for item in items if item["collection"] == collection]
    return {"items": items}


@app.patch("/api/library/{doc_id}")
def api_doc_update(doc_id: str, body: DocPatch):
    fields = {key: value for key, value in body.model_dump().items() if value is not None}
    doc = repo.update(doc_id, fields)
    if not doc:
        raise HTTPException(status_code=404, detail="资料不存在")
    return _library_item(doc)


@app.post("/api/library/{doc_id}/rename")
def api_doc_rename(doc_id: str):
    try:
        result = repo.rename(doc_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="资料不存在")
    doc = repo.get(doc_id) or {}
    return {**result, "doc": _library_item(doc)}


@app.get("/api/files/{doc_id}")
def api_file(doc_id: str):
    """返回原始文件（PDF 可直接在浏览器里翻页、图片直接预览）。"""
    doc = repo.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="资料不存在")
    path = Path(doc.get("path") or "")
    if not path.exists():
        raise HTTPException(status_code=404, detail="原始文件不在磁盘上（可能已被移走）")
    resolved = path.resolve()
    if ROOT.resolve() not in resolved.parents:
        raise HTTPException(status_code=403, detail="路径越界")
    media = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    return FileResponse(resolved, media_type=media)


PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/register", "/api/auth/me", "/favicon.ico"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/assets"):
        return await call_next(request)
    if auth.resolve(request.cookies.get(auth.COOKIE)) is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "未登录"}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


class Credentials(BaseModel):
    username: str
    password: str
    invite: str = ""


@app.post("/api/auth/register")
def api_register(body: Credentials, response: Response):
    try:
        user = auth.register(body.username, body.password, body.invite)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response.set_cookie(
        auth.COOKIE, auth.issue(user["id"]), httponly=True, samesite="lax", max_age=auth.MAX_AGE
    )
    return {"user": user}


@app.post("/api/auth/login")
def api_login(body: Credentials, request: Request, response: Response):
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{body.username.strip().lower()}"

    wait = auth.locked_for(key)
    if wait:
        raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {wait} 秒后再试")

    user = auth.verify(body.username, body.password)
    if not user:
        auth.record_failure(key)
        left = auth.remaining(key)
        detail = "用户名或密码不正确"
        if left:
            detail += f"（再错 {left} 次锁定 5 分钟）"
        raise HTTPException(status_code=401, detail=detail)

    auth.clear_failures(key)
    response.set_cookie(
        auth.COOKIE, auth.issue(user["id"]), httponly=True, samesite="lax", max_age=auth.MAX_AGE
    )
    return {"user": user}


@app.post("/api/auth/logout")
def api_logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(request: Request):
    return {
        "user": auth.resolve(request.cookies.get(auth.COOKIE)),
        "has_users": auth.has_users(),
    }


class PasswordChange(BaseModel):
    old: str
    new: str


@app.post("/api/auth/password")
def api_password(body: PasswordChange, request: Request, response: Response):
    user = auth.resolve(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        auth.change_password(user["id"], body.old, body.new)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 改密会使旧会话全部失效，这里给当前浏览器换发新会话
    response.set_cookie(
        auth.COOKIE, auth.issue(user["id"]), httponly=True, samesite="lax", max_age=auth.MAX_AGE
    )
    return {"ok": True}


def _require_admin(request: Request) -> dict:
    user = auth.resolve(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@app.get("/api/users")
def api_users(request: Request):
    _require_admin(request)
    return {"items": auth.list_users()}


class ResetIn(BaseModel):
    password: str


@app.post("/api/users/{uid}/reset")
def api_user_reset(uid: int, body: ResetIn, request: Request):
    _require_admin(request)
    try:
        auth.reset_password(uid, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.get("/login")
def login_page():
    return FileResponse(WEB / "login.html")


app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
