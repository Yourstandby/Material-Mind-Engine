# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- 数据分级 + 游客分层读取：资料按文档打 `public` / `internal`（默认）/ `confidential` 三级，策略文件 `library/access.json` 决定游客能读哪几级（`MME_GUEST_TIERS` / `MME_DEFAULT_TIER` 可覆盖），管理页 `/admin` 可逐份调、按文件夹批量调；游客侧在 Chroma `where` 里带 `doc_id` 白名单（`core/access.visible_doc_ids` → `retrieve_scored` / `product_stats` / `search_debug` / `bootstrap`），索引里查不到 `doc_id` 的旧数据对游客一律拒绝。实测：游客可读级别为空时检索 0 命中、问答返回「未检索到相关数据」，不越过分级
- 只读接口可按开关或 IP 白名单放行匿名调用：`MME_ALLOW_ANONYMOUS=1` 全员可匿名读，`MME_ANONYMOUS_IPS=172.16.1.121,...` 只放行指定来源；放行范围仅 `/api/chat`、`/api/search`、`/api/bootstrap`，上传/删除/`/api/auth/*`/资料库写操作照旧要登录（`core/auth.anonymous_allowed` + 中间件，反代后按 `X-Forwarded-For` 首段取真实 IP）
- 问答分两层：先把问题里的实体解析成库内目标产品（牌号 > 供应商 > 材质 > 应用），命中就直接出确定性对比表（`resolve_products` / `compare_products` / `render_compare`），数值全部来自库、不经过模型；一个产品都解析不出来才走 RAG + LLM 兜底。修掉了「FFKM 材质」回答「资料不足」、单问「FFKM」回答「库中没有该品牌」这类误判（材质/品牌名被当成了产品）
- 求极值（最高/最低）由代码计算并附引用编号，不再交给模型复述
- 材质代号（FKM / FFKM / PEEK …）不再被当成牌号匹配，库里 `grade` 写成材质名的脏数据也不进入对比表

- `scripts/pdf_ocr.py`：扫描版 PDF 的离线 OCR 预处理。有文本层的页直接沿用、只 OCR 缺字页（OCR 走多进程并行，`--parallel`，自动给每个 ONNX 会话分窄线程带），每页结果实时落盘（`logs/pdf_ocr_cache/`），中断重跑只补未完成的页；跑完投喂原文件，解析走缓存。实测 103 页混合文档：68s → 解析 0.03s；8 页纯扫描专利：3m35s → 0.004s
- 解析按文件头识别 PDF：`.crx` 等伪装后缀（绕 DLP 的常见做法）不再报「不支持的格式」
- 投喂改用离线文本层时会在文献警告里注明来源，缓存不完整（未写完成标记）一律不采用

### 性能

- 投喂抽取改为按块并发（`INGEST_CONCURRENCY`，默认 2，与网关 `GATEWAY_KEY_CONCURRENCY` 对齐）：一块一次 LLM 调用的串行瓶颈从 ≈13s/块 压到 ≈2 块并行，大文件时长接近减半
- 断点续跑：每抽完一块即追加到 `logs/ingest_cache/<doc_id>.jsonl`，取消 / 服务重启 / 崩溃后重新投喂只补没抽过的块，不再整份重跑；14 天未用的缓存自动清理
- LLM / embedding 调用加指数退避重试，识别 429「并发超限」与 5xx，并按 `Retry-After` 等待；此前 429 会把整份文件的块全部打掉（实测 68/68 失败、0 条入库）
- 抽取并发生效后仍按块序装配记录，同一份文件重投生成的 ID 保持稳定，不会出现重复行

### 新增

- 入库文件夹：资料可归入用户自建文件夹（默认 未分类 / 氟橡胶 / 全氟醚橡胶 / 聚醚醚酮），支持新建、重命名、删除；删除文件夹时资料移到未分类；投喂可选目标文件夹，不选则按主材质自动归类；资料可在抽屉里移动
- 投喂进度可恢复：入库改为后台线程 + 落盘任务（`logs/ingest_jobs/`），刷新/重开页面会自动恢复运行中的队列并继续显示进度；`GET /api/ingest/jobs` 可查最近任务
- 应用维度（以「用在哪」为主）：行业 / 部件 / 介质 / 认证 四类标签，随抽取自动归一化并写入向量元数据；问答与资料库可按应用筛选，英语资料通过查询扩展命中
- `core/applications.py`：应用分类法、别名匹配、元数据打包与分布统计；新增 `GET /api/applications`
- `scripts/backfill_applications.py`：存量数据回填应用字段，`--reembed` 可同步重算向量
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
