"""模型接入密钥：读状态 / 改 .env / 连通性自检 / 网关下游 Key 管理。

四件事：
1. 读出当前生效的接入配置（.env + 进程环境变量），密钥一律脱敏；
2. 从网页改 .env 并立刻热更新进程环境变量（不用重启服务）；
3. 连通性自检：真打一次 /models 或 /embeddings，判断「Key 对不对、端点通不通」；
4. 网关下游 Key：发、停、看用量（直接读同一个 gateway.db，不依赖网关进程在线）。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import httpx

from core.config import ROOT, env, mask, settings

ENV_FILE = ROOT / ".env"

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
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 OPENAI_BASE_URL"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
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
                    "model": cfg.get("model"),
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
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base or not cfg.get("model"):
        return {"ok": False, "error": "未配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{base}/embeddings",
                headers=headers,
                json={"model": cfg["model"], "input": ["ping"]},
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
    want = int(env("EMBEDDING_DIM", "0") or 0)
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
            else f"{cfg['model']} 通，维度 {dim}"
        ),
    }


def test(target: str) -> dict:
    target = (target or "llm").lower()
    result = test_embedding() if target == "embedding" else test_llm()
    result["target"] = "embedding" if target == "embedding" else "llm"
    result["at"] = time.strftime("%H:%M:%S")
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
