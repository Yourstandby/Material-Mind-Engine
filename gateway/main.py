"""内网模型网关：统一入口 + API Key + 限流 + 用量审计。

上游（任一 OpenAI 兼容服务，如公司服务器上的 Ollama / vLLM / Xinference）：
    UPSTREAM_LLM        http://192.168.1.10:11434/v1
    UPSTREAM_LLM_KEY    （可空）
    UPSTREAM_EMBED      默认同 UPSTREAM_LLM
    UPSTREAM_EMBED_KEY  （可空）
    UPSTREAM_OCR        （可空，需自己起 OCR 服务）

对外接口：
    POST /v1/chat/completions   支持流式
    POST /v1/embeddings
    GET  /v1/models
    POST /ocr                   转发到 OCR 服务
    GET  /health                公共
    GET  /admin/usage           需要 GATEWAY_ADMIN_TOKEN
    GET  /admin/keys            需要 GATEWAY_ADMIN_TOKEN
"""

from __future__ import annotations

import json
import os
import time

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway import store

app = FastAPI(title="MME Gateway", docs_url="/api/docs", redoc_url=None)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _llm_base() -> str:
    return _env("UPSTREAM_LLM", "http://127.0.0.1:11434/v1").rstrip("/")


def _embed_base() -> str:
    return (_env("UPSTREAM_EMBED") or _llm_base()).rstrip("/")


def _headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def require_key(request: Request) -> dict:
    raw = request.headers.get("authorization", "")
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="缺少 API Key（Authorization: Bearer <key>）")
    key = store.find(token)
    if not key:
        raise HTTPException(status_code=401, detail="API Key 无效或已停用")
    if store.recent(key["id"]) >= key["rpm"]:
        raise HTTPException(status_code=429, detail=f"超过速率限制（{key['rpm']} 次/分钟）")
    request.state.key = key
    return key


def _tokens(resp: httpx.Response) -> int:
    try:
        return int(resp.json().get("usage", {}).get("total_tokens", 0))
    except Exception:
        return 0


def _log(request: Request, path: str, status: int, tokens: int, started: float) -> None:
    key = getattr(request.state, "key", None)
    if key:
        store.log(key["id"], path, status, tokens, int((time.time() - started) * 1000))


@app.get("/health")
def health():
    return {"ok": True, "upstream": _llm_base(), "embed": _embed_base()}


@app.get("/v1/models")
async def models(request: Request, key: dict = Depends(require_key)):
    started, status, tokens = time.time(), 500, 0
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{_llm_base()}/models", headers=_headers(_env("UPSTREAM_LLM_KEY"))
            )
        status = resp.status_code
        return JSONResponse(resp.json(), status_code=status)
    finally:
        _log(request, "/v1/models", status, tokens, started)


@app.post("/v1/embeddings")
async def embeddings(request: Request, key: dict = Depends(require_key)):
    body = await request.body()
    started, status, tokens = time.time(), 500, 0
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_embed_base()}/embeddings",
                content=body,
                headers=_headers(_env("UPSTREAM_EMBED_KEY") or _env("UPSTREAM_LLM_KEY")),
            )
        status = resp.status_code
        return JSONResponse(resp.json(), status_code=status)
    finally:
        _log(request, "/v1/embeddings", status, tokens, started)


@app.post("/v1/chat/completions")
async def chat(request: Request, key: dict = Depends(require_key)):
    body = await request.body()
    stream = False
    try:
        stream = bool(json.loads(body or b"{}").get("stream"))
    except json.JSONDecodeError:
        pass

    url = f"{_llm_base()}/chat/completions"
    headers = _headers(_env("UPSTREAM_LLM_KEY"))
    started = time.time()

    if not stream:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(url, content=body, headers=headers)
        _log(request, "/v1/chat/completions", resp.status_code, _tokens(resp), started)
        return JSONResponse(
            resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
            status_code=resp.status_code,
        )

    async def relay():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, content=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        detail = await resp.aread()
                        yield b'data: {"error": "' + detail[:200] + b'"}\n\n'
                        return
                    async for chunk in resp.aiter_raw():
                        yield chunk
        finally:
            _log(request, "/v1/chat/completions", 200, 0, started)

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/ocr")
async def ocr(request: Request, key: dict = Depends(require_key)):
    upstream = _env("UPSTREAM_OCR")
    if not upstream:
        raise HTTPException(status_code=501, detail="未配置 UPSTREAM_OCR")
    body = await request.body()
    started = time.time()
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{upstream.rstrip('/')}/ocr", content=body, headers=headers_of(request))
    _log(request, "/ocr", resp.status_code, 0, started)
    return JSONResponse(resp.json(), status_code=resp.status_code)


def headers_of(request: Request) -> dict:
    keep = {"content-type", "accept"}
    return {k: v for k, v in request.headers.items() if k.lower() in keep}


def _admin(token: str | None) -> None:
    expect = _env("GATEWAY_ADMIN_TOKEN")
    if not expect or token != expect:
        raise HTTPException(status_code=403, detail="需要管理员令牌")


@app.get("/admin/usage")
def admin_usage(hours: int = 24, limit: int = 50, x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    return store.report(hours, limit)


@app.get("/admin/keys")
def admin_keys(x_admin_token: str = Header(default="")):
    _admin(x_admin_token)
    return {"items": store.list_keys()}
