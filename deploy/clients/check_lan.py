"""在「另一台机器」上验证能不能用本机的模型服务（只用标准库，不用装依赖）。

用法:
    python check_lan.py
    python check_lan.py --base-url http://172.16.1.210:8100/v1 --key mme-xxxx
    python check_lan.py --image 测试.png          # 额外验证 OCR

也可以先 set MME_BASE_URL / MME_API_KEY 再直接跑。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_BASE = os.environ.get("MME_BASE_URL", "http://172.16.1.210:8100/v1")
DEFAULT_KEY = os.environ.get("MME_API_KEY", "")
DEFAULT_MODEL = os.environ.get("MME_LLM_MODEL", "qwen2.5-72b-instruct")
DEFAULT_EMBED = os.environ.get("MME_EMBED_MODEL", "bge-m3")


def post(url: str, payload: dict | None = None, raw: bytes | None = None, ctype: str = "application/json", key: str = "", timeout: int = 180):
    data = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    headers = {"Content-Type": ctype} if data else {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(url: str, key: str = "", timeout: int = 30):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_env_file(path: str) -> dict:
    """读同目录下的 mme-client.env（可选），省得每次手敲 key。"""
    values = {}
    if not os.path.exists(path):
        return values
    for line in open(path, encoding="utf-8-sig"):
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, _, value = text.partition("=")
        values[name.strip()] = value.strip().strip("'\"")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--key", default=DEFAULT_KEY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED)
    parser.add_argument("--image", help="可选：一张含文字的图片，用来测 OCR")
    args = parser.parse_args()

    # 没显式给 key 时，尝试读同目录的 mme-client.env
    if not args.key:
        local = read_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mme-client.env"))
        args.key = local.get("MME_API_KEY", "")
        if args.base_url == DEFAULT_BASE and local.get("MME_BASE_URL"):
            args.base_url = local["MME_BASE_URL"]
        args.model = local.get("MME_LLM_MODEL") or args.model
        args.embed_model = local.get("MME_EMBED_MODEL") or args.embed_model

    base = args.base_url.rstrip("/")
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    key = args.key
    failed = []

    def ok(name, detail=""):
        print(f"  [PASS] {name}  {detail}")

    def bad(name, detail=""):
        print(f"  [FAIL] {name}  {detail}")
        failed.append(name)

    print(f"目标 {base}\n")

    # 1. 鉴权
    try:
        get(f"{base}/models")
        bad("鉴权", "不带 key 竟然成功了")
    except urllib.error.HTTPError as exc:
        ok("鉴权", f"无 key 返回 {exc.code}") if exc.code == 401 else bad("鉴权", f"返回 {exc.code}")

    if not key:
        print("\n没有提供 --key 或 MME_API_KEY，后面跳过")
        return 1

    # 2. 模型列表
    try:
        ids = [item["id"] for item in get(f"{base}/models", key).get("data", [])]
        ok("模型列表", str(ids)) if args.model in ids else bad("模型列表", f"{ids} 里没有 {args.model}")
    except Exception as exc:  # noqa: BLE001
        bad("模型列表", repr(exc))

    # 3. 对话
    try:
        started = time.time()
        body = post(f"{base}/chat/completions", {
            "model": args.model,
            "messages": [{"role": "user", "content": "只回答两个字：收到"}],
            "max_tokens": 16,
        }, key=key)
        text = (body["choices"][0]["message"]["content"] or "").strip()
        ok("对话", f"{(time.time() - started):.1f}s  {text[:20]}") if text else bad("对话", "返回空")
    except Exception as exc:  # noqa: BLE001
        bad("对话", repr(exc))

    # 4. 向量
    try:
        body = post(f"{base}/embeddings", {"model": args.embed_model, "input": ["FFKM 压缩永久变形", "PEEK 熔点"]}, key=key)
        dim = len(body["data"][0]["embedding"])
        ok("向量", f"{args.embed_model} dim={dim}") if dim == 1024 else bad("向量", f"dim={dim}，期望 1024")
    except Exception as exc:  # noqa: BLE001
        bad("向量", repr(exc))

    # 5. OCR（可选）
    if args.image:
        try:
            data = open(args.image, "rb").read()
            boundary = "----mme" + uuid.uuid4().hex
            ctype = mimetypes.guess_type(args.image)[0] or "application/octet-stream"
            name = os.path.basename(args.image)
            raw = b"".join([
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode(),
                data,
                f"\r\n--{boundary}--\r\n".encode(),
            ])
            body = post(f"{root}/ocr", raw=raw, ctype=f"multipart/form-data; boundary={boundary}", key=key, timeout=300)
            text = (body.get("text") or "").replace("\n", " / ")
            ok("OCR", text[:50]) if text else bad("OCR", "没识别出文字")
        except Exception as exc:  # noqa: BLE001
            bad("OCR", repr(exc))

    print()
    if failed:
        print("未通过：" + "、".join(failed))
        print("排查：确认本机 8100 端口可达（防火墙 Private 放行）、key 未吊销、模型名一致")
        return 2
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
