"""Embedding：走 OpenAI 兼容 /embeddings，不下载本地模型。

环境变量: EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL / EMBEDDING_DIM
"""

import time

from core.config import env, mask

BATCH = 48


def config() -> dict:
    return {
        "base_url": env("EMBEDDING_BASE_URL") or env("OPENAI_BASE_URL"),
        "api_key": env("EMBEDDING_API_KEY") or env("OPENAI_API_KEY"),
        "model": env("EMBEDDING_MODEL"),
        "dim": int(env("EMBEDDING_DIM", "0") or 0),
    }


def ready() -> bool:
    cfg = config()
    return bool(cfg["base_url"] and cfg["model"])


def status() -> dict:
    cfg = config()
    return {
        "ready": ready(),
        "model": cfg["model"] or "",
        "base_url": cfg["base_url"] or "",
        "api_key": mask(cfg["api_key"]),
        "dim": cfg["dim"],
    }


def embed(texts: list[str]) -> list[list[float]]:
    cfg = config()
    if not ready():
        raise RuntimeError("未配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL，无法向量化")

    from openai import OpenAI

    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"] or "not-needed")
    vectors: list[list[float]] = []

    for start in range(0, len(texts), BATCH):
        batch = [t[:4000] for t in texts[start : start + BATCH]]
        for attempt in range(3):
            try:
                res = client.embeddings.create(model=cfg["model"], input=batch)
                vectors.extend(item.embedding for item in sorted(res.data, key=lambda d: d.index))
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))

    if cfg["dim"] and vectors and len(vectors[0]) != cfg["dim"]:
        raise RuntimeError(f"embedding 维度 {len(vectors[0])} 与 EMBEDDING_DIM={cfg['dim']} 不一致")
    return vectors


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
