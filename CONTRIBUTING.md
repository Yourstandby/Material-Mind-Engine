# 贡献指南

## 目录结构

```
core/            纯逻辑层，不依赖任何 Web 框架
  config.py        环境变量解析（.env → 系统变量 → 注册表兜底）
  llm.py           对话客户端
  embeddings.py    向量化客户端
  query_engine.py  检索、上下文构建、流式生成、资料库聚合
  catalog.py       产品身份与材质归类
  auth.py          账号、会话、登录限流
  vector_store.py  向量库访问层（embedded / server 双模式、跨进程写锁）
  normalize.py     牌号、供应商、单位归一化

ingest/          数据入库
  parse.py         PDF / DOCX / XLSX / HTML / MD 解析
  chunk.py         分块
  extract.py       LLM 结构化抽取
  metadata.py      文献元数据（标题/作者/年份/类型/DOI/专利号）
  pipeline.py      编排：解析 → 抽取 → 归一化 → 去重 → 向量化 → 登记
  repo.py          文献索引 library/index.json 与规范命名
  watcher.py       inbox 目录守护（文件锁，多进程只跑一份）

server/main.py   FastAPI 薄壳：鉴权中间件 + HTTP/SSE 接口
gateway/         内网模型网关：API Key、限速、用量、流式代理
web/             原生前端，无构建步骤（index.html / login.html / assets/）
scripts/         运维脚本：发 key、重置密码、历史迁移
deploy/          服务器安装脚本与部署手册
docs/            架构与自托管模型方案
tests/           单元测试
```

**分层规则**：`core/` 不 import `server/` 或 `web/`；`ingest/` 不碰 HTTP；`server/` 只做参数解析、鉴权和事件转发，业务逻辑放在 `core/`。

## 开发环境

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt -r requirements-web.txt

# 本地启动（网关可选，直连模型也能跑）
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload
python -m uvicorn gateway.main:app --host 127.0.0.1 --port 8100   # 需要网关时
```

依赖安装慢时用阿里云镜像：`-i https://mirrors.aliyun.com/pypi/simple`。

## 数据与密钥红线

以下内容**绝不允许提交**，`.gitignore` 已覆盖，提交前请用 `git status` 自查：

```
.env                密钥与配置
auth.db             账号
gateway.db          API Key 与用量
chroma_db/          向量库
library/            文献索引（含本地路径）
inbox/              原始资料
*.lock, logs/       运行时产物
```

提交示例数据时，请使用脱敏的虚构内容，不要复制真实厂商 datasheet。

## 提交规范

采用 Conventional Commits，与历史提交保持一致：

```
<type>: <一句话说明>

<可选正文：为什么改、影响范围>
```

| type | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 文档 |
| `refactor` | 不改变行为的重构 |
| `test` | 测试 |
| `chore` | 构建、依赖、配置 |

一次提交只做一件事。改 schema、改 prompt、改依赖应分开提交。

## 提 PR

1. Fork 并新建分支：`git checkout -b feat/<short-name>`
2. 自测：
   ```bash
   python -m pytest tests/
   python -m compileall core ingest server gateway
   ```
3. PR 描述写清：动机、改动点、验证方式（命令 + 实际输出）
4. 涉及接口变更或数据模型的，同步更新 `README.md` 与 `CHANGELOG.md`

## 新增一种材料或产品

**不需要新建知识库**。当前模型是「产品 = 供应商 + 牌号」，材质是产品属性。

1. 把 datasheet 放进 `inbox/`（或网页拖入），自动解析、抽取、入库
2. 材质识别规则在 `core/catalog.py` 的 `MATERIAL_RULES`，出现新材质时补一条关键词映射
3. 抽取字段在 `ingest/extract.py`，新增字段需同步 `core/query_engine.py` 的 `FIELDS`
4. 改动抽取提示词后，用 `scripts/migrate_library.py` 重跑历史资料验证

## 报告 Bug

- 复现步骤（精确到命令行与参数）
- 期望结果 vs 实际结果
- 完整 traceback，不要截断
- 环境信息：Python 版本、操作系统、相关包版本
- 相关配置片段（`.env` 请先脱敏）

## 贡献者许可协议

本项目采用 AGPL-3.0 并保留商业双授权。提交 PR 前请阅读 [CLA.md](CLA.md)，提交即视为同意其中的版权与专利许可条款（含再许可权）。
