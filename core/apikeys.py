"""模型接入密钥：读状态 / 改 .env / 连通性自检 / 网关下游 Key 管理。

四件事：
1. 读出当前生效的接入配置（.env + 进程环境变量），密钥一律脱敏；
2. 从网页改 .env 并立刻热更新进程环境变量（不用重启服务）；
3. 连通性自检：真打一次 /models 或 /embeddings，判断「Key 对不对、端点通不通」；
4. 网关下游 Key：发、停、看用量（直接读同一个 gateway.db，不依赖网关进程在线）。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

import httpx

from core.config import ROOT, env, mask, settings

ENV_FILE = ROOT / ".env"
ENDPOINTS_FILE = ROOT / "endpoints.json"

# 允许从网页改的键：只放接入相关，别让人从浏览器改端口 / 网关分发逻辑
EDITABLE = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "LLM_MODEL",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
)
SECRET = ("OPENAI_API_KEY", "EMBEDDING_API_KEY")
LABELS = {
    "OPENAI_BASE_URL": "LLM 端点",
    "OPENAI_API_KEY": "LLM Key",
    "LLM_MODEL": "对话模型",
    "EMBEDDING_BASE_URL": "Embedding 端点",
    "EMBEDDING_API_KEY": "Embedding Key",
    "EMBEDDING_MODEL": "向量模型",
    "EMBEDDING_DIM": "向量维度",
}

_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=(.*)$")
_HEADER = "# 由「模型接入」页面写入（保持人与脚本都能读）"


# ---------------------------------------------------------------- .env 读写
def _read_file() -> dict[str, str]:
    """按行解析 .env，只取值不看注释。"""
    if not ENV_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        match = _LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def _write_file(updates: dict[str, str]) -> None:
    """就地改行，保留原有注释、顺序与 BOM；缺的键补到文件末尾。"""
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    lines = text[len(bom) :].splitlines()

    done: set[str] = set()
    for index, line in enumerate(lines):
        match = _LINE.match(line)
        if not match or match.group(1) not in updates:
            continue
        key = match.group(1)
        lines[index] = f"{key}={updates[key]}"
        done.add(key)

    missing = [key for key in updates if key not in done]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [_HEADER] + [f"{key}={updates[key]}" for key in missing]

    tmp = ENV_FILE.with_name(".env.tmp")
    tmp.write_text(bom + "\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(ENV_FILE)


def _is_masked(value: str) -> bool:
    """前端把脱敏值原样回传时不要写回文件（否则真实 Key 被 ******** 覆盖）。"""
    return "*" * 4 in value


def apply(updates: dict[str, str]) -> dict:
    """写 .env + 立刻更新进程内环境变量，返回最新状态。"""
    clean: dict[str, str] = {}
    for key, value in (updates or {}).items():
        if key not in EDITABLE:
            raise ValueError(f"不允许修改 {key}")
        value = str(value or "").strip()
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} 不能包含换行")
        if key in SECRET and _is_masked(value):
            continue  # 没改就跳过
        clean[key] = value

    if clean:
        _write_file(clean)
        for key, value in clean.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
    return status()


# ---------------------------------------------------------------- 状态
def _field(name: str, file_values: dict[str, str]) -> dict:
    effective = os.environ.get(name, "")
    from_file = file_values.get(name, "")
    secret = name in SECRET
    return {
        "name": name,
        "label": LABELS.get(name, name),
        "secret": secret,
        "set": bool(effective),
        "value": mask(effective) if secret else effective,
        "file": mask(from_file) if secret else from_file,
        "overridden": bool(from_file and effective and from_file != effective),
    }


def status() -> dict:
    file_values = _read_file()
    cfg = settings()
    embedding = {
        "base_url": env("EMBEDDING_BASE_URL") or env("OPENAI_BASE_URL"),
        "api_key": env("EMBEDDING_API_KEY") or env("OPENAI_API_KEY"),
        "model": env("EMBEDDING_MODEL"),
        "dim": int(env("EMBEDDING_DIM", "0") or 0),
    }
    return {
        "fields": [_field(name, file_values) for name in EDITABLE],
        "env_file": str(ENV_FILE),
        "llm": {
            "ready": all(cfg.get(k) for k in ("api_key", "base_url", "model")),
            "provider": cfg.get("provider", ""),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "api_key": mask(cfg.get("api_key", "")),
            "gateway": "127.0.0.1" in (cfg.get("base_url") or ""),
        },
        "embedding": {
            "ready": bool(embedding["base_url"] and embedding["model"]),
            "base_url": embedding["base_url"] or "",
            "model": embedding["model"] or "",
            "api_key": mask(embedding["api_key"] or ""),
            "dim": embedding["dim"],
        },
        "endpoints": endpoints_state(),
    }


# ---------------------------------------------------------------- 连通性自检
def _friendly(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:240]


def _body(resp: httpx.Response) -> str:
    text = resp.text or ""
    try:
        data = resp.json()
        detail = data.get("error") or data.get("detail") or data
        text = detail if isinstance(detail, str) else str(detail)
    except Exception:
        pass
    return text.strip().replace("\n", " ")[:200]


def test_llm() -> dict:
    cfg = settings()
    return probe_llm(cfg.get("base_url", ""), cfg.get("api_key", ""), cfg.get("model", ""))


def probe_llm(base_url: str, api_key: str, model: str) -> dict:
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 OPENAI_BASE_URL"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{base}/models", headers=headers)
            if resp.status_code < 400:
                try:
                    names = sorted(
                        {m.get("id") for m in (resp.json().get("data") or []) if m.get("id")}
                    )
                except Exception:
                    names = []
                return {
                    "ok": True,
                    "status": resp.status_code,
                    "ms": int((time.perf_counter() - started) * 1000),
                    "models": names[:20],
                    "detail": f"/models 通，{len(names)} 个模型" if names else "/models 通",
                }
            if resp.status_code not in (401, 403, 404, 405):
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "detail": f"/models 返回 {resp.status_code}：{_body(resp)}",
                }
            if resp.status_code in (401, 403):
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "detail": f"Key 被拒（{resp.status_code}）：{_body(resp)}",
                }
            # 有些服务不实现 /models，退一步真发一次 1 token 的对话
            resp = client.post(
                f"{base}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
    except Exception as exc:  # 连接失败 / 超时 / 证书
        return {"ok": False, "error": _friendly(exc)}
    ok = resp.status_code < 400
    return {
        "ok": ok,
        "status": resp.status_code,
        "ms": int((time.perf_counter() - started) * 1000),
        "detail": "对话接口通" if ok else f"{resp.status_code}：{_body(resp)}",
    }


def test_embedding() -> dict:
    from core import embeddings

    cfg = embeddings.config()
    return probe_embedding(
        cfg.get("base_url", ""),
        cfg.get("api_key", ""),
        cfg.get("model", ""),
        int(env("EMBEDDING_DIM", "0") or 0),
    )


def probe_embedding(base_url: str, api_key: str, model: str, want: int = 0) -> dict:
    base = (base_url or "").rstrip("/")
    if not base or not model:
        return {"ok": False, "error": "未配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{base}/embeddings",
                headers=headers,
                json={"model": model, "input": ["ping"]},
            )
    except Exception as exc:
        return {"ok": False, "error": _friendly(exc)}
    ms = int((time.perf_counter() - started) * 1000)
    if resp.status_code >= 400:
        return {"ok": False, "status": resp.status_code, "ms": ms, "detail": _body(resp)}
    try:
        dim = len(resp.json()["data"][0]["embedding"])
    except Exception:
        return {"ok": False, "status": resp.status_code, "ms": ms, "detail": "返回里没有 embedding 向量"}
    want = int(want or 0)
    mismatch = bool(want and want != dim)
    return {
        "ok": not mismatch,
        "status": resp.status_code,
        "ms": ms,
        "dim": dim,
        "dim_expected": want,
        "detail": (
            f"维度 {dim} ≠ 配置 {want}，换维度需要重建向量库"
            if mismatch
            else f"{model} 通，维度 {dim}"
        ),
    }


def test(target: str) -> dict:
    target = (target or "llm").lower()
    result = test_embedding() if target == "embedding" else test_llm()
    result["target"] = "embedding" if target == "embedding" else "llm"
    result["at"] = time.strftime("%H:%M:%S")
    return result


# ---------------------------------------------------------------- 端点档案
# 一家公司与多个 OpenAI 兼容服务之间切换：档案存在 endpoints.json（明文密钥，已 gitignore），
# 「启用」= 把选中项写进 .env 并热生效，所以运行时行为与手工改 .env 完全一致。
PURPOSES = ("llm", "embedding", "both")


def _empty_db() -> dict:
    return {"active": {"llm": "", "embedding": ""}, "items": []}


def _seed_items() -> list[dict]:
    """首次使用：把当前 .env 里的 LLM / Embedding 各录一条档案，免得列表空空的。

    两个用途分开存：一个端点的模型名往往不同（对话用 qwen3，向量用 bge-m3），
    合在一条里启用时会把 Embedding 模型写成对话模型。
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "id": "env-llm",
            "name": "当前 .env 接入（LLM）",
            "base_url": env("OPENAI_BASE_URL"),
            "api_key": env("OPENAI_API_KEY"),
            "model": env("LLM_MODEL"),
            "dim": 0,
            "purpose": "llm",
            "created_at": stamp,
        },
        {
            "id": "env-embedding",
            "name": "当前 .env 接入（Embedding）",
            "base_url": env("EMBEDDING_BASE_URL") or env("OPENAI_BASE_URL"),
            "api_key": env("EMBEDDING_API_KEY") or env("OPENAI_API_KEY"),
            "model": env("EMBEDDING_MODEL"),
            "dim": int(env("EMBEDDING_DIM", "0") or 0),
            "purpose": "embedding",
            "created_at": stamp,
        },
    ]


def save_endpoints(data: dict) -> None:
    tmp = ENDPOINTS_FILE.with_name("endpoints.json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ENDPOINTS_FILE)


def load_endpoints() -> dict:
    if not ENDPOINTS_FILE.exists():
        data = _empty_db()
        data["items"] = _seed_items()
        data["active"] = {"llm": "env-llm", "embedding": "env-embedding"}
        save_endpoints(data)
        return data
    try:
        data = json.loads(ENDPOINTS_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        data = _empty_db()
    if not isinstance(data, dict):
        data = _empty_db()
    data.setdefault("active", {"llm": "", "embedding": ""})
    data.setdefault("items", [])
    return data


def endpoints_state() -> dict:
    data = load_endpoints()
    items = [
        {**it, "api_key": mask(it.get("api_key") or ""), "has_key": bool(it.get("api_key"))}
        for it in data["items"]
    ]
    return {"items": items, "active": data.get("active", {}), "file": str(ENDPOINTS_FILE)}


def _find(data: dict, endpoint_id: str) -> dict:
    for item in data["items"]:
        if item.get("id") == endpoint_id:
            return item
    raise ValueError("端点不存在")


def _purpose(value: str | None) -> str:
    purpose = (value or "both").strip().lower()
    if purpose not in PURPOSES:
        raise ValueError("purpose 只能是 llm / embedding / both")
    return purpose


def _dim(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        raise ValueError("dim 必须是整数")


def _fields(payload: dict, existing: dict | None = None) -> dict:
    """合并前端提交的字段；Key 收到脱敏值就当没改。"""
    current = existing or {}
    base_url = (payload.get("base_url") if payload.get("base_url") is not None else current.get("base_url", "")) or ""
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("端点要以 http:// 或 https:// 开头")
    api_key = payload.get("api_key")
    if api_key is None or _is_masked(str(api_key)):
        api_key = current.get("api_key", "")
    return {
        "name": (str(payload.get("name") or "").strip() or current.get("name") or base_url),
        "base_url": base_url,
        "api_key": str(api_key or "").strip(),
        "model": str(payload.get("model") if payload.get("model") is not None else current.get("model", "")).strip(),
        "dim": _dim(payload.get("dim") if payload.get("dim") is not None else current.get("dim", 0)),
        "purpose": _purpose(payload.get("purpose") or current.get("purpose") or "both"),
    }


def add_endpoint(payload: dict) -> dict:
    data = load_endpoints()
    item = {"id": "ep-" + secrets.token_hex(4), **_fields(payload), "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    data["items"].append(item)
    save_endpoints(data)
    return status()


def update_endpoint(endpoint_id: str, payload: dict) -> dict:
    data = load_endpoints()
    item = _find(data, endpoint_id)
    item.update(_fields(payload, item))
    save_endpoints(data)
    return status()


def delete_endpoint(endpoint_id: str) -> dict:
    data = load_endpoints()
    item = _find(data, endpoint_id)
    active = data.get("active", {})
    if endpoint_id in (active.get("llm"), active.get("embedding")):
        raise ValueError("正在使用中，先启用别的端点再删除")
    data["items"] = [it for it in data["items"] if it.get("id") != item["id"]]
    save_endpoints(data)
    return status()


def activate_endpoint(endpoint_id: str, target: str = "both") -> dict:
    target = (target or "both").strip().lower()
    if target not in PURPOSES:
        raise ValueError("target 只能是 llm / embedding / both")
    data = load_endpoints()
    item = _find(data, endpoint_id)
    purpose = item.get("purpose") or "both"
    if purpose != "both" and target == "both":
        raise ValueError(f"这个端点只用于 {purpose}，请单独启用")
    if purpose != "both" and target != purpose:
        raise ValueError(f"这个端点只用于 {purpose}")

    updates: dict[str, str] = {}
    if target in ("llm", "both"):
        updates.update(
            {
                "OPENAI_BASE_URL": item["base_url"],
                "OPENAI_API_KEY": item.get("api_key", ""),
                "LLM_MODEL": item.get("model", ""),
            }
        )
        data["active"]["llm"] = item["id"]
    if target in ("embedding", "both"):
        updates.update(
            {
                "EMBEDDING_BASE_URL": item["base_url"],
                "EMBEDDING_API_KEY": item.get("api_key", ""),
                "EMBEDDING_MODEL": item.get("model", ""),
            }
        )
        if item.get("dim"):
            updates["EMBEDDING_DIM"] = str(item["dim"])
        data["active"]["embedding"] = item["id"]
    save_endpoints(data)
    apply(updates)
    return status()


def test_endpoint(endpoint_id: str, target: str = "llm") -> dict:
    item = _find(load_endpoints(), endpoint_id)
    kind = "embedding" if (target or "llm").lower() == "embedding" else "llm"
    if kind == "embedding":
        result = probe_embedding(item.get("base_url", ""), item.get("api_key", ""), item.get("model", ""), _dim(item.get("dim")))
    else:
        result = probe_llm(item.get("base_url", ""), item.get("api_key", ""), item.get("model", ""))
    result.update({"target": kind, "endpoint": item.get("name") or item["id"], "at": time.strftime("%H:%M:%S")})
    return result


# ---------------------------------------------------------------- 网关下游 Key
def _gateway_store():
    """直接用同一份 gateway.db；网关进程跑没跑都能管 Key。"""
    from gateway import store

    db = env("GATEWAY_DB", "./gateway.db")
    path = Path(db) if os.path.isabs(db) else (ROOT / db)
    if Path(store.DB).resolve() != path.resolve():
        store.DB = path
    return store


def gateway_state(hours: int = 24) -> dict:
    store = _gateway_store()
    usage = {row["id"]: row for row in store.usage_by_key(hours)}
    items = []
    for key in store.list_keys():
        stat = usage.get(key["id"], {})
        items.append(
            {
                **key,
                "active": bool(key["active"]),
                "calls": int(stat.get("calls") or 0),
                "tokens": int(stat.get("tokens") or 0),
                "errors": int(stat.get("errors") or 0),
            }
        )
    return {"hours": hours, "items": items, "db": str(store.DB), "total": len(items)}


def create_gateway_key(name: str, rpm: int = 120, apply_scope: str = "") -> dict:
    name = (name or "").strip() or "未命名"
    try:
        rpm = max(1, min(int(rpm or 120), 100000))
    except (TypeError, ValueError):
        raise ValueError("rpm 必须是整数")
    store = _gateway_store()
    token = store.create_key(name, rpm)
    applied: list[str] = []
    scope = (apply_scope or "").strip().lower()
    if scope in ("llm", "both"):
        apply({"OPENAI_API_KEY": token})
        applied.append("OPENAI_API_KEY")
    if scope in ("embedding", "both"):
        apply({"EMBEDDING_API_KEY": token})
        applied.append("EMBEDDING_API_KEY")
    return {"key": token, "name": name, "rpm": rpm, "applied": applied}


def revoke_gateway_key(key_id: str) -> bool:
    key_id = str(key_id or "").strip()
    if not key_id.isdigit():
        raise ValueError("key id 必须是数字")
    return _gateway_store().revoke(key_id)
