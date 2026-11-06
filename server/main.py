"""Material Mind Engine — Web API 薄壳（SSE 流式）。

启动: uvicorn server.main:app --reload --port 8000
"""
import json
import logging
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import auth, embeddings
from core import applications
from core import failures
from core import vector_store as vs
from core.config import env, status as config_status
from core.query_engine import (
    CATALOG,
    answer_by_products,
    application_facets,
    build_sources,
    delete_source,
    doc_products,
    named_suppliers,
    product_options,
    product_stats,
    records_of,
    resolve_collection,
    retrieve,
    retrieve_scored,
    sample_records,
    search_debug,
    source_stats,
    stream_tokens,
)
from ingest import repo

log = logging.getLogger("mme.server")
from ingest.pipeline import ingest as run_ingest
from server import jobs

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
    product: str = "all"
    industry: str = ""
    part: str = ""
    medium: str = ""
    top_k: int = 5


class ReparseIn(BaseModel):
    collection: str = "catalog"
    source: str = ""


class FolderIn(BaseModel):
    name: str


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/bootstrap")
def bootstrap():
    products = product_options()
    return {
        "collections": products,
        "products": products,
        "applications": application_facets(),
        "folders": repo.folders(),
        "catalog": CATALOG,
        "config": config_status(),
    }


@app.get("/api/products")
def api_products(industry: str = "", part: str = "", medium: str = ""):
    return {"items": product_stats(industry=industry, part=part, medium=medium)}


@app.get("/api/applications")
def api_applications():
    """应用分布：行业 / 部件 / 介质 / 认证（供筛选栏与统计）。"""
    return {"facets": application_facets()}


@app.get("/api/health")
def health():
    return {**config_status(), "embedding": embeddings.status(), "chroma": vs.health()}


@app.post("/api/ingest")
async def ingest_file(
    file: UploadFile = File(...),
    collection: str = Form("catalog"),
    material: str = Form(""),
    folder: str = Form(""),
):
    data = await file.read()
    filename = file.filename or "upload"

    # 前端可能把「材质筛选」（all / FFKM / FKM …）当成库名传上来，
    # 统一归一到真实的产品库；否则资料会写进一个没人读的集合（历史上出现过）。
    collection = resolve_collection(collection)

    # 保存原始文件，供「重新解析」与排查使用
    saved = Path("inbox") / "_done" / collection
    try:
        saved.mkdir(parents=True, exist_ok=True)
        (saved / Path(filename).name).write_bytes(data)
    except OSError:
        pass

    # 后台线程跑抽取，HTTP 立即返回任务号；刷新页面后按任务号恢复进度
    job = jobs.start(filename, data, collection, run_ingest, material, folder, with_cancel=True)
    return {"job_id": job["id"], "filename": filename, "collection": collection}


@app.get("/api/ingest/jobs")
def api_ingest_jobs(limit: int = 10):
    """最近的入库任务（含运行中）：刷新页面后据此恢复进度。"""
    return {"items": jobs.recent(limit)}


@app.get("/api/ingest/jobs/{job_id}")
def api_ingest_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@app.post("/api/ingest/jobs/{job_id}/cancel")
def api_ingest_job_cancel(job_id: str):
    """取消投喂：当前块跑完后停下，已抽到的记录照常入库。"""
    job = jobs.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@app.get("/api/folders")
def api_folders():
    return {"items": repo.folders()}


@app.post("/api/folders")
def api_folder_create(body: FolderIn):
    try:
        return repo.create_folder(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/folders/{fid}")
def api_folder_rename(fid: str, body: FolderIn):
    try:
        return repo.rename_folder(fid, body.name)
    except KeyError:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/folders/{fid}")
def api_folder_delete(fid: str):
    try:
        return repo.delete_folder(fid)
    except KeyError:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/records")
def records(collection: str = "catalog", limit: int = 30):
    return {"items": sample_records(collection, limit)}


REFUSAL_WORDS = ("资料不足", "没有相关", "未提供", "无相关数据")


def _capture_failure(question: str, answer: str, sources: list[str], top_score, username: str) -> None:
    """自动识别失败并落库：拒答 / 检索分过低 / 无引用。"""
    trigger = None
    if any(word in answer for word in REFUSAL_WORDS):
        trigger = "refusal"
    elif top_score is not None and top_score < float(env("FAIL_MIN_SCORE", "0.35")):
        trigger = "low_score"
    elif sources and not re.search(r"\[\d+\]", answer):
        trigger = "no_citation"
    if not trigger:
        return
    try:
        failures.record(
            question=question,
            answer=answer,
            username=username,
            sources=sources,
            top_score=top_score,
            trigger=trigger,
        )
    except Exception:
        pass


@app.post("/api/chat")
def chat(body: Ask, request: Request):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    user = auth.resolve(request.cookies.get(auth.COOKIE)) or {}
    username = str(user.get("username") or "")

    def gen():
        started = time.time()
        parts: list[str] = []
        sources: list[str] = []
        top_score = None
        try:
            # 先解析目标产品：命中就出确定性对比表（数值来自库，不让模型复述），
            # 一个产品都解析不出来才走 RAG + LLM 兜底。
            direct = None
            try:
                direct = answer_by_products(question, body.collection, product=body.product)
            except Exception as exc:  # noqa: BLE001 - 对比层出错退回原链路，别让问答整体失败
                log.warning("product compare failed: %s", exc)
            if direct:
                yield sse({"type": "meta", "hits": len(direct["sources"]), "collection": body.collection,
                           "mode": "table"})
                yield sse({"type": "sources", "items": direct["sources"]})
                text = direct["text"]
                for at in range(0, len(text), 24):
                    yield sse({"type": "token", "text": text[at:at + 24]})
                yield sse({"type": "done", "mode": "table",
                           "elapsed_ms": int((time.time() - started) * 1000)})
                return
            docs, metas, scores = retrieve_scored(
                question,
                body.collection,
                body.top_k,
                product=body.product,
                industry=body.industry,
                part=body.part,
                medium=body.medium,
            )
            if not docs:
                names = named_suppliers(question)
                message = (
                    f"库里没有 {names[0]} 的资料，别家厂商的产品不能拿来代替"
                    if names
                    else "该知识库未检索到相关数据，换个问法或换库试试"
                )
                # 自动落库：未命中本身就是「该补什么资料」的信号，不用等用户点
                try:
                    failures.record_miss(question, username, trigger="no_hit")
                except Exception as exc:  # noqa: BLE001 - 记录失败不能影响问答
                    log.warning("record miss failed: %s", exc)
                _capture_failure(question, "", [], None, username)
                yield sse({"type": "error", "message": message, "miss": True})
                yield sse({"type": "done"})
                return
            sources = [str((meta or {}).get("source") or "") for meta in metas]
            valid = [score for score in scores if score is not None]
            top_score = max(valid) if valid else None
            yield sse({"type": "meta", "hits": len(docs), "collection": body.collection})
            yield sse({"type": "sources", "items": build_sources(metas)})
            tokens = stream_tokens(question, docs, metas)
            try:
                for token in tokens:
                    parts.append(token)
                    yield sse({"type": "token", "text": token})
            finally:
                # 前端断开时 Starlette 可能直接丢掉生成器而不跑 GC，
                # 这里显式 close，保证上游流被关、网关并发槽位释放。
                tokens.close()
            _capture_failure(question, "".join(parts), sources, top_score, username)
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


class FeedbackIn(BaseModel):
    question: str
    answer: str = ""
    verdict: str | None = None
    expected: str | None = None
    trigger: str = ""


@app.post("/api/failures")
def api_failure(body: FeedbackIn, request: Request):
    user = auth.resolve(request.cookies.get(auth.COOKIE)) or {}
    username = str(user.get("username") or "")
    # 用户点「需要这个资料」：走未命中通道，同一问题合并计数，后台聚合成待补清单
    if body.trigger in failures.MISS_TRIGGERS:
        result = failures.record_miss(body.question, username, trigger="manual_no_hit")
        return result
    fid = failures.record(
        question=body.question,
        answer=body.answer,
        username=username,
        trigger=body.trigger or "manual",
        verdict=body.verdict,
        expected=body.expected,
    )
    return {"id": fid}


@app.get("/api/failures")
def api_failures(request: Request, status: str = "open", limit: int = 100):
    _require_admin(request)
    return {"items": failures.list_items(status, limit), "stats": failures.stats()}


GRADE_RE = re.compile(r"(?<![a-z0-9])([A-Z][A-Za-z0-9]*[-\s]?\d[A-Za-z0-9\-.]*)")


def _miss_wanted(items: list[dict], limit: int = 30) -> list[dict]:
    """把未命中问题里的厂商 / 牌号词抽出来，聚合成「该补哪些资料」的清单。"""
    buckets: dict[str, dict] = {}
    for item in items:
        question = item.get("question") or ""
        keys: list[tuple[str, str]] = [("supplier", name) for name in named_suppliers(question)]
        keys += [("grade", m.group(1).strip()) for m in GRADE_RE.finditer(question)]
        seen = set()
        for kind, word in keys:
            token = (kind, word.lower())
            if token in seen:
                continue
            seen.add(token)
            entry = buckets.setdefault(f"{kind}:{word.lower()}", {
                "kind": kind,
                "word": word,
                "asks": 0,
                "questions": [],
                "last_ts": "",
            })
            entry["asks"] += int(item.get("hits") or 1)
            if len(entry["questions"]) < 3:
                entry["questions"].append(item.get("question") or "")
            entry["last_ts"] = max(entry["last_ts"], item.get("last_ts") or "")
    wanted = sorted(buckets.values(), key=lambda x: (-x["asks"], x["word"]))
    return wanted[:limit]


@app.get("/api/failures/misses")
def api_failure_misses(request: Request, limit: int = 100, days: int = 365):
    """后台看板：哪些问题库里没有资料 / 用户催了哪些资料。"""
    _require_admin(request)
    items = failures.misses(limit=limit, days=days)
    return {
        "items": items,
        "wanted": _miss_wanted(items),
        "stats": failures.stats(),
    }


@app.get("/api/sources")
def api_sources(collection: str = "all"):
    return {"items": source_stats(collection)}


@app.get("/api/sources/records")
def api_source_records(collection: str = "catalog", source: str = "", limit: int = 200):
    if not source:
        raise HTTPException(status_code=400, detail="source 不能为空")
    return {"items": records_of(collection, source, limit)}


@app.delete("/api/sources")
def api_delete_source(collection: str = "catalog", source: str = ""):
    if not source:
        raise HTTPException(status_code=400, detail="source 不能为空")
    return {"deleted": delete_source(collection, source)}


@app.get("/api/search")
def api_search(q: str, collection: str = "catalog", top_k: int = 8):
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
    folder_id: str | None = None
    title: str | None = None
    authors: str | None = None
    year: int | None = None
    doc_type: str | None = None
    publisher: str | None = None
    doi: str | None = None
    patent_no: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


def _library_item(doc: dict, products: dict, folder_names: dict | None = None) -> dict:
    path = Path(doc.get("path") or "")
    name = path.name or doc.get("canonical_name") or doc.get("original_name") or ""
    folder_id = doc.get("folder_id") or "unfiled"
    names = folder_names if folder_names is not None else repo.folder_map()
    return {
        **doc,
        **(products.get(doc.get("doc_id"), {})),
        "folder_id": folder_id,
        "folder_name": names.get(folder_id, "未分类"),
        "source": name,
        "kind": (Path(name).suffix.lstrip(".") or "file").lower(),
    }


@app.get("/api/library")
def api_library(
    collection: str = "all",
    product: str = "all",
    folder: str = "all",
    industry: str = "",
    part: str = "",
    medium: str = "",
):
    products = doc_products()
    folder_names = repo.folder_map()
    items = [_library_item(doc, products, folder_names) for doc in repo.all_docs()]
    if product and product != "all":
        items = [item for item in items if item.get("product_id") == product]
    if collection != "all":
        items = [item for item in items if item["collection"] == collection]
    if folder and folder != "all":
        items = [item for item in items if item.get("folder_id") == folder]
    if any(x and x != "all" for x in (industry, part, medium)):
        items = [item for item in items if applications.matches(item, industry, part, medium)]
    return {"items": items}


@app.patch("/api/library/{doc_id}")
def api_doc_update(doc_id: str, body: DocPatch):
    fields = {key: value for key, value in body.model_dump().items() if value is not None}
    folder_id = fields.pop("folder_id", None)
    doc = repo.update(doc_id, fields)
    if not doc:
        raise HTTPException(status_code=404, detail="资料不存在")
    if folder_id:
        try:
            doc = repo.move_doc(doc_id, folder_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
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


PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/register", "/api/auth/me", "/api/health", "/favicon.ico"}


def _client_ip(request: Request) -> str:
    """真实来源 IP：反代后取 X-Forwarded-For 首段（白名单按它比）。"""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.client.host if request.client else "") or ""


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/assets"):
        return await call_next(request)
    # 只读三件套（chat / search / bootstrap）可按开关或 IP 白名单匿名调用；
    # 上传、删除、/api/auth/*、资料库写操作照旧要登录。
    if auth.anonymous_allowed(path, _client_ip(request)):
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
