"""Embedding 服务体检：维度、归一化、中文/英文语义区分度。

用法:
    python deploy/local/check_embed.py --base http://127.0.0.1:8002/v1 --model bge-m3
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request

PAIRS = [
    ("全氟醚橡胶 FFKM 的压缩永久变形", "FFKM 压缩永久变形率 200℃×24h", "相关"),
    ("全氟醚橡胶 FFKM 的压缩永久变形", "今天天气不错，适合钓鱼", "无关"),
    ("PEEK 聚醚醚酮 熔点 343 摄氏度", "PEEK melting point 343 C", "相关"),
    ("PEEK 聚醚醚酮 熔点 343 摄氏度", "氟橡胶 FKM 的耐油性能", "无关"),
]


def embed(base: str, model: str, texts: list[str]) -> list[list[float]]:
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]


def cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb or 1.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8002/v1")
    parser.add_argument("--model", default="bge-m3")
    args = parser.parse_args()

    texts, labels = [], []
    for left, right, kind in PAIRS:
        texts.extend([left, right])
        labels.append(kind)

    vectors = embed(args.base, args.model, texts)
    dim = len(vectors[0])
    print(f"model={args.model} dim={dim} count={len(vectors)}")
    print(f"norm(text0)={math.sqrt(sum(x * x for x in vectors[0])):.4f}")

    ok = True
    related, unrelated = [], []
    for index, kind in enumerate(labels):
        score = cos(vectors[index * 2], vectors[index * 2 + 1])
        (related if kind == "相关" else unrelated).append(score)
        print(f"  [{kind}] {score:.4f}  {PAIRS[index][0][:18]}  vs  {PAIRS[index][1][:18]}")

    if related and unrelated:
        gap = min(related) - max(unrelated)
        print(f"\nmin(相关)={min(related):.4f}  max(无关)={max(unrelated):.4f}  gap={gap:+.4f}")
        if gap <= 0.05:
            ok = False
            print("!! 区分度不足：相关文本得分不高于无关文本，检查 --pooling 与模型是否匹配")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
