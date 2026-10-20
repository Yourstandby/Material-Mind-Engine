# 自托管模型部署方案（LLM / Embedding / OCR）

目标：模型全部跑在公司内网，数据不出网；MME 通过统一网关调用。

```
MME (8000) ──► Gateway (8100) ──► LLM        Ollama / vLLM      :11434
                    │        ──► Embedding  bge-m3 等          :11434
                    └────────► OCR        RapidOCR 服务       :8101
```

网关负责 API Key、限速、用量统计；模型服务不直接对外。

---

## 1. 资源需求

| 组件 | 推荐模型 | 磁盘 | 内存 | GPU |
|---|---|---|---|---|
| LLM | Qwen2.5-7B-Instruct（Q4） | ~5 GB | 8–16 GB | 8 GB 显存；纯 CPU 很慢 |
| Embedding | bge-m3 | ~2.3 GB | 2–4 GB | 不需要 |
| OCR | RapidOCR（PP-OCRv4 ONNX） | ~120 MB | 0.3–0.6 GB | 不需要 |

合计：约 8 GB 磁盘、10 GB 内存；有 GPU 则 LLM 体验好很多。

> 没有 GPU 时建议：LLM 继续用云端 API，只自建 Embedding + OCR（约 2.5 GB 磁盘、4 GB 内存）。

---

## 2. LLM

以 Ollama 为例（Windows 安装包，装完自动注册为服务）：

```powershell
ollama pull qwen2.5:7b
ollama list                     # 记下模型名
curl http://127.0.0.1:11434/v1/models
```

Ollama 自带 OpenAI 兼容层，路径就是 `/v1`。

**验证一次对话**：

```powershell
curl http://127.0.0.1:11434/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"你好"}]}'
```

如果用的是 vLLM / Xinference / LM Studio，地址同样是 `http://IP:端口/v1`，换成对应模型名即可。

---

## 3. Embedding

优先用 Ollama 拉 embedding 模型（同一端口，省一个服务）：

```powershell
ollama pull bge-m3
curl http://127.0.0.1:11434/v1/embeddings `
  -H "Content-Type: application/json" `
  -d '{"model":"bge-m3","input":["全氟醚橡胶"]}'
```

**必须记住维度**：

| 模型 | 维度 |
|---|---|
| bge-m3 | 1024 |
| bge-large-zh-v1.5 | 1024 |
| 智谱 embedding-3（当前线上） | 2048 |

**换 embedding 模型 = 必须重建向量库**，不同模型的向量不可混用。重建方式：

```bash
# 备份后删除旧库，重新投喂
move chroma_db chroma_db.bak
# 重启服务，把 inbox/_done 里的文件重新拖入或批量重跑 migrate_library
```

---

## 4. OCR 服务

RapidOCR 本身是库，不是服务。包一层 FastAPI（约 60 行）：

```bash
pip install rapidocr_onnxruntime fastapi uvicorn pillow numpy python-multipart
```

```python
# ocr_service/main.py
import io

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

app = FastAPI(title="MME OCR")
engine = RapidOCR()


@app.get("/health")
def health():
    return {"ok": True, "engine": "rapidocr"}


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    data = await file.read()
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法读取图片: {exc}")

    result, _elapse = engine(np.array(image))
    lines = [
        {"text": text, "score": round(float(score), 3), "box": box}
        for box, text, score in (result or [])
    ]
    return {"text": "\n".join(line["text"] for line in lines), "lines": lines}
```

启动：

```powershell
.\.venv\Scripts\python -m uvicorn ocr_service.main:app --host 0.0.0.0 --port 8101
```

**扫描版 PDF** 需要先转图片再送 OCR（`pypdfium2` 或 `PyMuPDF` 渲染每页为 PNG），这部分尚未接入 `ingest/parse.py`，属于下一步工作：

```python
# ingest/parse.py 的 _pdf 中，页文本为空时：
#   text = ocr_service.extract(page_image)
# 配置项：OCR_BASE_URL=http://127.0.0.1:8100，经网关鉴权后调用 /ocr
```

---

## 5. 通过网关统一暴露

`.env`（网关所在目录）：

```ini
UPSTREAM_LLM=http://127.0.0.1:11434/v1
UPSTREAM_LLM_KEY=
UPSTREAM_EMBED=http://127.0.0.1:11434/v1
UPSTREAM_EMBED_KEY=
UPSTREAM_OCR=http://127.0.0.1:8101
GATEWAY_ADMIN_TOKEN=换成一串随机字符
```

启动网关并发 key：

```powershell
.\.venv\Scripts\python -m uvicorn gateway.main:app --host 0.0.0.0 --port 8100
.\.venv\Scripts\python -m scripts.make_key --name mme --rpm 600
```

---

## 6. MME 配置

```ini
OPENAI_BASE_URL=http://127.0.0.1:8100/v1
OPENAI_API_KEY=mme-xxx
LLM_MODEL=qwen2.5:7b

EMBEDDING_BASE_URL=http://127.0.0.1:8100/v1
EMBEDDING_API_KEY=mme-xxx
EMBEDDING_MODEL=bge-m3
```

---

## 7. 验证清单

| # | 检查 | 命令 | 期望 |
|---|---|---|---|
| 1 | LLM 直连 | `curl $UPSTREAM_LLM/models` | 返回模型列表 |
| 2 | Embedding 维度 | 调 `/v1/embeddings` 看 `len(embedding)` | 与 `EMBEDDING_DIM` 一致 |
| 3 | 网关鉴权 | 不带 key 调 `/v1/models` | 401 |
| 4 | 网关限流 | 超过 `--rpm` 连续请求 | 429 |
| 5 | OCR | `curl -F file=@test.png localhost:8101/ocr` | 返回 `text` |
| 6 | MME 问答 | 浏览器提一个问题 | 正常返回带引用 |
| 7 | MME 投喂 | 拖入 PDF | 解析 → 抽取 → 入库完成 |

---

## 8. 运维

**Windows 服务**（NSSM）：

```powershell
nssm install MME-OCR D:\MME\.venv\Scripts\python.exe -m uvicorn ocr_service.main:app --host 0.0.0.0 --port 8101
nssm set MME-OCR AppDirectory D:\MME
nssm set MME-OCR Start SERVICE_AUTO_START
nssm start MME-OCR
```

LLM 若是 Ollama，安装时已注册为 `OllamaService`，无需额外处理。

**监控**：`GET /health`（网关、OCR）、`GET /api/health`（MME，含 chroma 状态）。

**备份**：模型权重不必备份（可重新拉取），需要备份的是 `.env`、`auth.db`、`gateway.db`、`chroma_db`、`library`、`inbox/_done`。

**升级模型**：换 LLM 只改 `LLM_MODEL` 并重启；换 embedding 必须重建向量库。

---

## 9. 常见问题

**Q：embedding 换模型后入库报「该知识库由 xxx 构建」**
向量维度不同。删除 `chroma_db/` 重新投喂，或临时改 `CATALOG_COLLECTION` 建新库。

**Q：LLM 回答很慢，CPU 跑满**
7B 模型纯 CPU 通常只有 2–5 token/s。要么加 GPU，要么 LLM 继续用云 API、只自建 embedding + OCR。

**Q：网关 401 / 429**
401 是 key 缺失或已吊销（`scripts.make_key --list` 查）；429 是超过每分钟限额，调 `--rpm` 或加 key。

**Q：OCR 中文识别差**
RapidOCR 对清晰扫描件效果不错；模糊或低分辨率建议先 300 DPI 渲染，或换 PaddleOCR（准确率更高，体积约 1 GB）。

**Q：内网不能上外网，模型怎么拉**
在能上网的机器上用 `ollama pull` 后拷贝 `~/.ollama/models`，或使用 `ollama create` 导入 GGUF 文件。
