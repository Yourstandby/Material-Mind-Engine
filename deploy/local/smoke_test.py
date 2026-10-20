"""全链路自检：网关鉴权 → LLM → Embedding → OCR → Web。

用法:
    .venv\\Scripts\\python deploy/local/smoke_test.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

GATEWAY = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8100/v1").rstrip("/")
GATEWAY_ROOT = GATEWAY[: -len("/v1")] if GATEWAY.endswith("/v1") else GATEWAY
KEY = os.environ.get("OPENAI_API_KEY", "")
LLM = os.environ.get("LLM_MODEL", "")
EMBED = os.environ.get("EMBEDDING_MODEL", "bge-m3")
DIM = int(os.environ.get("EMBEDDING_DIM", "0") or 0)
OCR = (os.environ.get("OCR_BASE_URL") or "").rstrip("/")
WEB = f"http://127.0.0.1:{os.environ.get('MME_PORT_WEB', '8300')}"

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def call(url: str, payload: dict | None = None, key: str = "", timeout: int = 180):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}  {detail}")


def check_gateway_health() -> bool:
    try:
        status, body = call(f"{GATEWAY_ROOT}/health")
        record("网关存活", status == 200, f"{body.get('upstream')} | embed={body.get('embed')}")
        return True
    except Exception as exc:  # noqa: BLE001
        record("网关存活", False, str(exc))
        return False


def check_auth() -> bool:
    try:
        call(f"{GATEWAY}/models")
        record("网关鉴权", False, "不带 key 竟然返回 200")
        return False
    except urllib.error.HTTPError as exc:
        record("网关鉴权", exc.code == 401, f"无 key 返回 {exc.code}")
        return exc.code == 401
    except Exception as exc:  # noqa: BLE001
        record("网关鉴权", False, str(exc))
        return False


def check_models() -> bool:
    if not KEY:
        record("API Key", False, "OPENAI_API_KEY 为空")
        return False
    try:
        status, body = call(f"{GATEWAY}/models", key=KEY)
        ids = [item["id"] for item in body.get("data", [])]
        record("API Key + 模型列表", status == 200 and bool(ids), f"{ids}")
        return LLM in ids
    except Exception as exc:  # noqa: BLE001
        record("API Key + 模型列表", False, str(exc))
        return False


def check_embedding() -> bool:
    try:
        status, body = call(f"{GATEWAY}/embeddings", {"model": EMBED, "input": ["FFKM 压缩永久变形", "PEEK 熔点"]}, KEY, 120)
        vector = body["data"][0]["embedding"]
        dim = len(vector)
        ok = status == 200 and (not DIM or dim == DIM)
        record("Embedding 维度", ok, f"{EMBED} dim={dim} 期望={DIM or '未设'}")
        return ok
    except Exception as exc:  # noqa: BLE001
        record("Embedding 维度", False, str(exc))
        return False


def check_llm() -> bool:
    try:
        payload = {
            "model": LLM,
            "messages": [{"role": "user", "content": "只回答两个字：收到"}],
            "temperature": 0,
            "max_tokens": 16,
        }
        status, body = call(f"{GATEWAY}/chat/completions", payload, KEY, 180)
        text = (body["choices"][0]["message"]["content"] or "").strip()
        record("LLM 对话", status == 200 and bool(text), text[:30])
        return bool(text)
    except Exception as exc:  # noqa: BLE001
        record("LLM 对话", False, str(exc))
        return False


def check_ocr() -> bool:
    if not OCR:
        record("OCR", False, "OCR_BASE_URL 未配置")
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont

        font_path = "C:/Windows/Fonts/msyh.ttc"
        font = ImageFont.truetype(font_path, 40) if os.path.exists(font_path) else ImageFont.load_default()
        image = Image.new("RGB", (760, 180), "white")
        draw = ImageDraw.Draw(image)
        draw.text((30, 30), "FFKM 压缩永久变形测试", fill="black", font=font)
        draw.text((30, 100), "Compression set 200C x 24h", fill="black", font=font)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = json.dumps({"image": base64.b64encode(buffer.getvalue()).decode()}).encode("utf-8")
        req = urllib.request.Request(
            f"{OCR}/ocr/base64", data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body.get("text", "")
        ok = "FFKM" in text.upper()
        record("OCR 识别", ok, text.replace("\n", " / ")[:60])
        return ok
    except Exception as exc:  # noqa: BLE001
        record("OCR 识别", False, str(exc))
        return False


def check_web() -> bool:
    try:
        status, body = call(f"{WEB}/api/health")
        chroma = body.get("chroma", {})
        embed = body.get("embedding", {})
        ok = status == 200 and chroma.get("ok") and embed.get("ready")
        record("Web 健康", bool(ok), f"chroma={chroma.get('ok')} embedding={embed.get('ready')} model={embed.get('model')}")
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        record("Web 健康", False, str(exc))
        return False


def main() -> int:
    print(f"网关 {GATEWAY}")
    print(f"Web  {WEB}")
    print(f"OCR  {OCR or '(未配置)'}\n")

    if not check_gateway_health():
        print("\n网关没起来，先跑 start-all.ps1")
        return 1
    check_auth()
    models_ok = check_models()
    embed_ok = check_embedding()
    llm_ok = check_llm() if models_ok else False
    ocr_ok = check_ocr()
    web_ok = check_web()

    failed = [name for state, name, _ in results if state == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("未通过：" + "、".join(failed))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
