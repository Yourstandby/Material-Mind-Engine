# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- 扫描件 OCR：无文本层页面自动渲染并调用 OCR 服务，支持网关鉴权与页数上限
- `ocr_service/`：RapidOCR + FastAPI 服务，依赖独立在 `requirements-ocr.txt`
- CI：GitHub Actions 跑语法检查与单元测试（`tests/` 增至 23 项）
- 失败案例收集：自动捕获拒答 / 低分 / 无引用，支持点踩归类（A 检索未召回 / B 答错 / C 数据缺失 / D 超纲），存 SQLite 并记录用户名
- `scripts/failure_review.py`：列出未归类案例、打标、一键转成评测题
- 评测框架 `eval/`：42 题评测集（单产品 / 条件 / 对比 / 聚合 / 开放 / 无答案）与跑分脚本，输出检索命中率 / 数值准确率 / 引用有效率
- 检索增强：牌号优先排序（支持省略品牌前缀）、同源最多 2 条、聚合类扩大召回

## [2.1.0] - 2026-10-20

### 新增

- 产品化数据模型：以「产品 = 供应商 + 牌号」为一级单位，知识库合并为单一 `catalog`，材质降为筛选属性
- 文献元数据抽取：自动识别标题、作者、年份、类型、DOI、专利号，支持规范命名与人工修正
- 内部账号体系：注册、登录、改密、管理员重置、命令行重置
- 登录限流：按 IP + 用户名计数，5 次失败锁定 5 分钟，SQLite 共享，多进程一致
- 模型网关 `gateway/`：OpenAI 兼容代理，API Key、限速、用量统计、流式透传
- 向量库访问层 `core/vector_store.py`：embedded / server 双模式、跨进程写锁、退避重试
- 部署物料：`deploy/install.ps1` 一键安装、`deploy/DEPLOY.md` 部署手册
- 自托管方案：`docs/SELFHOST_MODELS.md`（LLM / Embedding / OCR）

### 变更

- 移除 `feedback.py`，人工纠错并入失败反馈流程
- 原文接口 `/api/files/{doc_id}` 纳入登录鉴权，未登录返回 401
- 资料库列表按产品分组，材质作为筛选项而非独立知识库
- 会话携带 `token_version`，改密或重置后旧会话立即失效
- `ingest/pipeline.py` 默认写入 `catalog`，并附带产品与材质字段
- watcher 改用文件锁，多 worker 下只由一个进程守护 inbox

### 修复

- 多 worker 下限流计数各自为政
- 重置密码后账号仍被锁定 5 分钟
- 登录页缺少注册入口，首次使用无法创建账号
- PDF 抽取文本中的乱码与孤立代理字符导致 JSON 序列化失败
- 产品身份脏数据：域名被识别为供应商、牌号重复前缀、材质代号误判
- Perlast G75M 缺失温度范围，按原文补为 -15°C ~ +260°C

## [2.0.0] - 2026-10-18

### 新增

- 自动化投喂流水线：解析 → 分块 → 抽取 → 归一化 → 去重 → 向量化，含 inbox 目录守护
- 资料库管理：按文件聚合、批量删除、JSON 导出、重新解析、检索测试
- 引用可追溯：答案内 `[n]` 角标可点击，展开来源字段与原始片段
- 内联 SVG 图标系统与 160–240ms 动效，支持系统减弱动效偏好

### 变更

- 移除 Chainlit / Gradio / Streamlit 三套前端，改为 FastAPI + 原生 HTML/CSS/JS
- 许可证由 MIT 改为 AGPL-3.0，新增商业双授权与 CLA
- 重写 git 历史，移除误提交的数据文件

### 修复

- 来源卡片因同名函数被覆盖而无法渲染
- 投喂进度面板挂载到隐藏容器，用户看不到反馈
- 入库 `+0 条` 时未展示后端返回的原因

[未发布]: https://github.com/Yourstandby/Material-Mind-Engine/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/Yourstandby/Material-Mind-Engine/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/Yourstandby/Material-Mind-Engine/releases/tag/v2.0.0
