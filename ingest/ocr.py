"""OCR 客户端：调用内网 OCR 服务（可直连，也可经网关）。

配置：
    OCR_BASE_URL   http://127.0.0.1:8101   或 http://127.0.0.1:8100（经网关）
    OCR_API_KEY    走网关时填 API Key
    OCR_MAX_PAGES  单份文档最多 OCR 页数（默认 20，防止大文件拖垮）
"""

from __future__ import annotations

import httpx

from core.config import env


def base_url() -> str:
    return env("OCR_BASE_URL", "").strip().rstrip("/")


def available() -> bool:
    return bool(base_url())


def max_pages() -> int:
    try:
        return int(env("OCR_MAX_PAGES", "20"))
    except ValueError:
        return 20


def image_to_text(data: bytes, filename: str = "page.png") -> str:
    """把一张图片交给 OCR 服务，返回纯文本。失败时抛异常，由调用方降级。"""
    base = base_url()
    if not base:
        raise RuntimeError("未配置 OCR_BASE_URL")

    headers = {}
    api_key = env("OCR_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with httpx.Client(timeout=180) as client:
        response = client.post(
            f"{base}/ocr",
            files={"file": (filename, data, "image/png")},
            headers=headers,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"OCR 服务返回 {response.status_code}: {response.text[:160]}")
    payload = response.json()
    return str(payload.get("text") or "").strip()
