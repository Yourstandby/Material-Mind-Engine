"""OCR 服务：RapidOCR + FastAPI。

安装依赖：
    pip install -r requirements-ocr.txt

启动：
    python -m uvicorn ocr_service.main:app --host 0.0.0.0 --port 8101

接口：
    GET  /health
    POST /ocr   multipart/form-data, 字段 file
"""

from __future__ import annotations

import io

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

app = FastAPI(title="MME OCR", docs_url="/api/docs", redoc_url=None)
engine = RapidOCR()


@app.get("/health")
def health():
    return {"ok": True, "engine": "rapidocr"}


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}")

    result, _elapse = engine(np.array(image))
    lines = [
        {"text": text, "score": round(float(score), 3), "box": box}
        for box, text, score in (result or [])
        if str(text).strip()
    ]
    return {"text": "\n".join(line["text"] for line in lines), "lines": lines}
