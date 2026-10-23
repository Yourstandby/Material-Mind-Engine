"""MME 本地 OCR 服务（RapidOCR / PP-OCRv4 ONNX，CPU 即可）。

MME 主程序不直接依赖它：通过 OCR_BASE_URL 调用，或经网关 /ocr 转发。

启动:
    python -m uvicorn ocr_service.main:app --host 127.0.0.1 --port 8101

接口:
    GET  /health        -> {"ok":true,"engine":"rapidocr","lang":...}
    POST /ocr           -> multipart file=<图片>   {"text":"...","lines":[...]}
    POST /ocr/base64    -> {"image":"<base64>"}    {"text":"...","lines":[...]}
"""

from __future__ import annotations

import base64
import io
import os
import time

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image, ImageOps

MAX_BYTES = int(os.environ.get("OCR_MAX_MB", "20")) * 1024 * 1024

app = FastAPI(title="MME OCR", docs_url="/api/docs", redoc_url=None)
_ENGINE = None
_ENGINE_MODEL = ""


def engine():
    """懒加载：第一次调用时再载模型，服务秒起。

    识别模型默认用 PP-OCRv5（中文 + 英文 + 日文假名一块支持），
    老版 PP-OCRv4 的中文模型认不出假名，日语资料会整段变成乱码。
    """
    global _ENGINE, _ENGINE_MODEL
    if _ENGINE is None:

        from pathlib import Path

        from rapidocr_onnxruntime import RapidOCR

        model = os.environ.get("OCR_REC_MODEL") or ""
        if not model:
            candidate = Path(__file__).resolve().parent / "models" / "ch_PP-OCRv5_rec_mobile.onnx"
            model = str(candidate) if candidate.exists() else ""
        _ENGINE = RapidOCR(rec_model_path=model) if model else RapidOCR()
        _ENGINE_MODEL = model or "ch_PP-OCRv4 (built-in)"
    return _ENGINE


def _run(image: Image.Image) -> dict:
    started = time.time()
    result, _elapse = engine()(np.array(image.convert("RGB")))
    lines = [
        {"text": str(text), "score": round(float(score), 3), "box": box}
        for box, text, score in (result or [])
    ]
    return {
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _open(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        return image
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}")


@app.get("/health")
def health():
    return {
        "ok": True,
        "engine": "rapidocr",
        "model": _ENGINE_MODEL or "pending",
        "loaded": _ENGINE is not None,
        "max_mb": MAX_BYTES // 1024 // 1024,
    }


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"图片超过 {MAX_BYTES // 1024 // 1024} MB")
    return _run(_open(data))


@app.post("/ocr/base64")
async def ocr_base64(body: dict):
    raw = body.get("image") or ""
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"base64 解码失败: {exc}")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="图片过大")
    return _run(_open(data))
