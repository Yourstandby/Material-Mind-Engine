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


def image_to_lines(data: bytes, filename: str = "page.png") -> list[dict]:
    """把一张图片交给 OCR 服务，返回带 bbox 与置信度的行。失败时抛异常，由调用方降级。

    保留坐标是关键：下游 layout.render() 靠几何位置还原表格行列，
    只拿纯文本会让表格塔成一维、数值串行。
    """
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
    lines = payload.get("lines")
    if isinstance(lines, list) and lines:
        return [line for line in lines if isinstance(line, dict)]
    # 旧版服务只回 text：降级成无坐标的单行列表，调用方会退回纯文本路径
    text = str(payload.get("text") or "").strip()
    return [{"text": line, "score": None, "box": None} for line in text.splitlines() if line.strip()]


def image_to_text(data: bytes, filename: str = "page.png") -> str:
    """把一张图片交给 OCR 服务，返回重建后的文本（表格会还原成 Markdown）。"""
    from ingest import layout

    return render_lines(image_to_lines(data, filename))


def render_lines(lines: list[dict]) -> str:
    """把 OCR 行还原成可读文本：能识别出表格就输出 Markdown 表格。"""
    from ingest import layout

    if not lines:
        return ""
    width = _page_width(lines)
    text, _is_table = layout.render(lines, width)
    return text.strip()


def _page_width(lines: list[dict]) -> float:
    right = 0.0
    for line in lines or []:
        box = line.get("box") or []
        try:
            right = max(right, max(float(p[0]) for p in box))
        except (TypeError, ValueError, IndexError):
            continue
    return right or 1000.0
