# Material-Mind-Engine

任意材料的数据自动化与 RAG 问答：抓取 → LLM 抽取 → 清洗 → 向量库 → 带来源问答。

## 架构

```
.firecrawl/ (index.md)  →  extract.py  →  raw_json/{profile}/merged.json  →  data_cleaner.py  →  cleaned.json
                                                                                                    ↓
   app.py / query.py  ←  chromadb  ←  vector_store.py  ←  feedback.py  ←  feedback.jsonl
```

## 快速开始

```bash
pip install -r requirements.txt
python extract.py --profile profiles/peek.yaml
python query.py "PEEK 的耐温范围是多少？" --collection peek
```

GUI：`python app.py` → 打开 `http://127.0.0.1:8000`

前端依赖：`pip install -r requirements-web.txt`

## 新增一种材料

1. 建 profile：`profiles/<name>.yaml`，定义 `field_map`（11 字段）、`extraction_prompt`、`keywords`（数据源相关性关键词）。
2. 提取 + 清洗：
   `python extract.py --profile profiles/<name>.yaml`
   `python data_cleaner.py raw_json/<name>/merged.json cleaned_<name>.json --profile profiles/<name>.yaml`
3. 入库 + 问答：
   `python vector_store.py cleaned_<name>.json --collection <name> --reset`
   `python query.py "..." --collection <name>`

## 当前支持的 profile

| profile | 材料 | collection | 当前条数 |
| --- | --- | --- | --- |
| profiles/ffkm.yaml | FFKM（全氟醚橡胶） | materials | 31 |
| profiles/fkm.yaml | FKM（氟橡胶） | fkm | 62 |
| profiles/peek.yaml | PEEK（聚醚醚酮） | peek | 61 |

## 测试

```bash
python -m pytest tests/
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| extract.py | 按 profile 用 LLM 从 `.firecrawl` 的 index.md 抽取结构化 JSON，并自动合并出 merged.json |
| data_cleaner.py | 字段映射、单位统一（℃ / MPa）、置信度过滤 |
| vector_store.py | 清洗结果序列化后嵌入并写入 Chroma（`--reset` 重建，可免传 input 自动定位 merged.json） |
| query.py | 检索 Chroma 并由 LLM 生成带来源的回答 |
| app.py | 启动 Web 服务（FastAPI + SSE） |
| server/main.py | HTTP 接口：`/api/bootstrap` `/api/chat`（SSE 流式）`/api/records` |
| web/ | 前端：原生 HTML/CSS/JS，无构建步骤 |
| feedback.py | 把 feedback.jsonl 的人工纠错 upsert 进同一 collection（`--interactive` 交互式录入） |
| main.py | 项目入口占位 |
| profiles/*.yaml | 材料 profile：字段映射 + 抽取提示词 + 关键词 |

## 环境变量（.env）

| 键 | 说明 |
| --- | --- |
| OPENAI_API_KEY | LLM API 密钥 |
| OPENAI_BASE_URL | LLM 兼容接口地址 |
| LLM_MODEL | 模型名（如 deepseek-chat） |

## 许可证

本项目采用 **GNU Affero General Public License v3.0**（见 [LICENSE](LICENSE)）。

- 内部使用、二次开发、分发：遵循 AGPL-3.0
- 闭源集成、作为 SaaS 对外提供且不愿公开源码：需购买商业授权，见 [COMMERCIAL.md](COMMERCIAL.md)
- 提交贡献前请阅读 [CLA.md](CLA.md)，提交 PR 即视为同意

Copyright (c) 2026 Material-Mind-Engine Contributors
