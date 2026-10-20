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
| ingest/ | 投喂流水线：解析 → 分块 → 抽取 → 归一化 → 去重 → 入库（含 inbox 目录守护） |
| core/catalog.py | 产品身份与材质归类：产品 = 供应商 + 牌号，材质是属性 |
| core/auth.py | 内部账号：注册 / 登录 / 改密 / 重置 / 登录限流 |
| core/vector_store.py | 向量库访问层：embedded / server 双模式、跨进程写锁、退避重试 |
| gateway/ | 内网模型网关：OpenAI 兼容代理，API Key + 限速 + 用量统计 |
| scripts/ | 运维脚本：发 API Key、重置密码、历史数据迁移 |
| deploy/ | 服务器安装脚本与部署手册 |
| docs/ | 自托管模型（LLM / Embedding / OCR）方案 |

## 环境变量（.env）

| 键 | 说明 |
| --- | --- |
| OPENAI_API_KEY | LLM API 密钥 |
| OPENAI_BASE_URL | LLM 兼容接口地址 |
| LLM_MODEL | 模型名（如 deepseek-chat） |
| EMBEDDING_BASE_URL | 向量化接口地址（OpenAI 兼容） |
| EMBEDDING_API_KEY | 向量化密钥 |
| EMBEDDING_MODEL | 向量化模型名（如 bge-m3） |
| CHROMA_MODE | `embedded`（默认）或 `server` |
| CHROMA_HOST / CHROMA_PORT | server 模式下的 Chroma 地址 |
| INVITE_CODE | 注册邀请码（首个账号免码） |
| UPSTREAM_LLM | 网关的上游模型地址 |
| GATEWAY_ADMIN_TOKEN | 网关管理接口令牌 |

## 部署

- 服务器安装：[deploy/DEPLOY.md](deploy/DEPLOY.md)（一键脚本 `deploy/install.ps1`）
- 自托管模型（LLM / Embedding / OCR）：[docs/SELFHOST_MODELS.md](docs/SELFHOST_MODELS.md)
- 模型统一经 `gateway/` 暴露：API Key + 限速 + 用量统计

## 版本记录

完整更新日志见 [CHANGELOG.md](CHANGELOG.md)，以下为摘要。

### v2.1.0（2026-10-20）

- **产品为中心**：知识库合并为单一 `catalog`，产品 = 供应商 + 牌号，材质降为筛选属性
- **文献管理**：自动抽取标题 / 作者 / 年份 / 类型 / DOI / 专利号，支持规范命名与人工修正
- **账号与权限**：注册、登录、改密、管理员重置、CLI 重置；原文接口纳入鉴权
- **并发**：向量库访问层（跨进程写锁 + 重试 + server 模式）；watcher 文件锁保证单进程守护
- **模型网关**：`gateway/` 提供 OpenAI 兼容代理，API Key、限速、用量统计、流式透传
- **部署**：`deploy/install.ps1` 一键安装；`docs/SELFHOST_MODELS.md` 自托管方案
- 修复：多 worker 下限流不共享、重置密码后仍被锁定、登录页缺少注册入口

### v2.0.0（2026-10-18）

- 移除 Chainlit / Gradio / Streamlit，改为 FastAPI + 原生 HTML/CSS/JS
- 自动化投喂流水线（inbox 守护）、资料库管理、引用可追溯
- 重新授权：AGPL-3.0 + 商业双授权

## 许可证

本项目采用 **GNU Affero General Public License v3.0**（见 [LICENSE](LICENSE)）。

- 内部使用、二次开发、分发：遵循 AGPL-3.0
- 闭源集成、作为 SaaS 对外提供且不愿公开源码：需购买商业授权，见 [COMMERCIAL.md](COMMERCIAL.md)
- 提交贡献前请阅读 [CLA.md](CLA.md)，提交 PR 即视为同意

Copyright (c) 2026 Material-Mind-Engine Contributors
