"""LLM 客户端（OpenAI 兼容），问答与抽取共用。

重试策略：429 / 5xx / 连接超时都是「等一下就好」的错误，指数退避重试；
服务端给了 Retry-After 就按它等（网关的「同一 API Key 最多 N 个在途」就是这种）。
非瞬时错误（400 参数错、401 鉴权失败）立刻抛出，不浪费时间。
"""

import random
import threading
import time

from openai import OpenAI

from core.config import env, settings

ATTEMPTS = max(1, int(env("LLM_MAX_ATTEMPTS", "4") or 4))
BACKOFF_CAP = float(env("LLM_BACKOFF_CAP", "30") or 30)
RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)
RETRY_TYPES = {
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
}

_client_lock = threading.Lock()
_clients: dict[tuple[str, str], OpenAI] = {}


def client() -> OpenAI:
    """按 (base_url, key) 复用客户端：抽取是多线程的，每次新建连接池纯属浪费。"""
    cfg = settings()
    if not cfg["base_url"]:
        raise RuntimeError("未配置 OPENAI_BASE_URL")
    key = (cfg["base_url"], cfg["api_key"] or "not-needed")
    with _client_lock:
        cached = _clients.get(key)
        if cached is None:
            cached = OpenAI(base_url=key[0], api_key=key[1])
            _clients[key] = cached
        return cached


def model_name() -> str:
    model = settings()["model"]
    if not model:
        raise RuntimeError("未配置 LLM_MODEL")
    return model


def reasoning_effort() -> str:
    """默认思考强度。minimal / low / medium / high；留空 = 不发该参数。"""
    value = str(env("LLM_REASONING", "minimal") or "").strip().lower()
    return value if value in ("minimal", "low", "medium", "high") else ""


def _retry_after(exc: Exception) -> float:
    """服务端 Retry-After（秒）；没有就返回 0。"""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    try:
        return max(0.0, float((headers or {}).get("retry-after")))
    except (TypeError, ValueError):
        return 0.0


def _transient(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) in RETRY_STATUS:
        return True
    if type(exc).__name__ in RETRY_TYPES:
        return True
    text = str(exc).lower()
    return any(
        hint in text
        for hint in ("429", "并发超限", "rate limit", "too many", "timeout", "timed out", "connection")
    )


def chat(prompt: str, system: str | None = None, temperature: float = 0.1) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            res = client().chat.completions.create(
                model=model_name(), messages=messages, temperature=temperature
            )
            return res.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - 由 _transient 决定要不要重试
            last = exc
            if attempt == ATTEMPTS - 1 or not _transient(exc):
                raise
            delay = _retry_after(exc) or min(BACKOFF_CAP, 2.0 * (2**attempt))
            time.sleep(delay + random.uniform(0, 0.4))
    raise last if last else RuntimeError("LLM 调用失败")
