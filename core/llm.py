"""LLM 客户端（OpenAI 兼容），问答与抽取共���。"""

from openai import OpenAI

from core.config import settings


def client() -> OpenAI:
    cfg = settings()
    if not cfg["base_url"]:
        raise RuntimeError("未配置 OPENAI_BASE_URL")
    return OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"] or "not-needed")


def model_name() -> str:
    model = settings()["model"]
    if not model:
        raise RuntimeError("未配置 LLM_MODEL")
    return model


def chat(prompt: str, system: str | None = None, temperature: float = 0.1) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    res = client().chat.completions.create(
        model=model_name(), messages=messages, temperature=temperature
    )
    return res.choices[0].message.content or ""
