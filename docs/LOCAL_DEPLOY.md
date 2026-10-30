# MME 本机部署手册（Windows + 2×L20 48G）

> 这份文档描述**本台机器实际跑起来的那一套**，以及怎么原样搬到另一家公司。
> 通用部署（Ollama / 纯 CPU）见 [deploy/DEPLOY.md](../deploy/DEPLOY.md)；模型方案原理见 [SELFHOST_MODELS.md](SELFHOST_MODELS.md)。

---

## 1. 架构与端口

```
浏览器 ──► MME Web            0.0.0.0:8300   （内网同事用）
              │
              ├─► 网关        0.0.0.0:8100     API Key / 限流 / 并发闸门 / 用量统计
              │     ├─► LLM      127.0.0.1:8086  llama.cpp + qwen3-30b（4 槽 ×4K）
              │     ├─► Embed    127.0.0.1:8002  llama.cpp + bge-m3
              │     └─► OCR      127.0.0.1:8101  RapidOCR（CPU）
              │
              └─► Chroma server 127.0.0.1:8001  （向量库，独立进程）
```

**只有 8300（Web）和 8100（网关）对外。** 模型服务与向量库一律绑 127.0.0.1，外部系统只能拿到网关发的 `mme-xxx` key。

| 端口 | 服务 | 绑定 | 说明 |
|---|---|---|---|
| 8300 | MME Web | 0.0.0.0 | 给同事用 |
| 8100 | 网关 | 0.0.0.0 | 给别的系统接模型用 |
| 8086 | LLM（llama.cpp + Qwen3-30B-A3B） | 0.0.0.0 | 只给网关用 |
| 8002 | Embedding | 127.0.0.1 | 只给网关 |
| 8101 | OCR | 127.0.0.1 | 只给网关 / MME |
| 8001 | Chroma server | 127.0.0.1 | 只给 MME 本机进程 |

> 本机 8000 被既有 reagent-app（Docker）映射占用，新 LLM 服务固定用 8086。
> 如果 8000 空出来，把 `.env` 的 `MME_PORT_LLM` 和 `UPSTREAM_LLM` 一起改回 8000 即可。

> **2026-10-20 已完成切换**：原 8000 端口上的 llama-server（PID 38168）已停止，
> LLM 由 `start-llm.ps1` 在 **8086** 上提供（4 槽 ×4K、q8_0 KV）。
> 8000 端口已归还给 Docker 的 reagent-app。
>
> 要把模型服务给别的项目用，见 [LAN_ACCESS.md](LAN_ACCESS.md)。


---

## 2. 模型清单（本机实测）

| 组件 | 模型 | 文件 | 显存/内存 | 备注 |
|---|---|---|---|---|
| LLM | Qwen3-30B-A3B-Instruct-2507 UD-Q4_K_XL | `D:\models\gguf\Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf`（17.7 GB） | 单卡 24 GB 即可 | 4 槽 ×4K；MoE 每步只激活约 3B，单路比 72B 稠密快得多 |
| Embedding | bge-m3 FP16 | `D:\models\bge-m3\bge-m3-FP16.gguf`（1.16 GB） | ~2 GB | 1024 维，8192 上下文，**必须 CLS pooling** |
| OCR | RapidOCR（PP-OCRv4 ONNX） | 随 pip 包装 | CPU | 中英文，300 DPI 渲染 |
| 运行时 | llama.cpp `0.7.0-dev` build 11696 | `D:\models\llamacpp\bin` | CUDA 12.8 | 驱动 572.83 |
| 向量库 | Chroma 1.4.4（server 模式） | `./chroma_db` + `127.0.0.1:8001` | 磁盘 | 独立进程统一调度 |

显存账：Qwen3-30B-A3B Q4 权重约 17.7 GB；4 槽 ×4K 的 KV（q8_0）只有几百 MB，另加计算缓冲；bge-m3 约 2 GB。单卡 24 GB 足够，显存富余就把 `.env` 的 `MME_LLM_CTX` 调大换更长上下文。

---

## 3. 首次部署（可复现步骤）

前置：Python 3.12（本机装在 `%LOCALAPPDATA%\Programs\Python\Python312`）、模型文件、llama.cpp。

```powershell
cd D:\OC\MME
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-web.txt -r requirements-local.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 写配置（从 .env.example 复制后改）
copy .env.example .env
notepad .env        # 参照第 5 节填

# 起服务
.\deploy\local\start-all.ps1 -WithLlm
.\deploy\local\status.ps1
.\.venv\Scripts\python deploy\local\smoke_test.py
```

首次启动后浏览器打开 `http://<本机IP>:8300`，注册的第一个账号自动成为管理员（注册码见 `.env` 的 `INVITE_CODE`）。

### 扫描版 PDF 怎么处理

`ingest/parse.py` 已接好 OCR：

- PDF 有文本层 → `pypdf` 直接抽，**不浪费 OCR**；
- 某页抽不出文字 → 用 `pypdfium2` 按 `OCR_DPI`（默认 300）渲染成图 → 调 `OCR_BASE_URL/ocr/base64`；
- 图片（png/jpg/tif/webp）可直接投喂，走同一个 OCR。

每页的 OCR 结果会作为 `warn` 事件出现在投喂进度里，方便核对。

---

## 4. 日常启停

```powershell
.\deploy\local\start-all.ps1          # 起 Embedding / OCR / 网关 / Chroma / Web
.\deploy\local\start-all.ps1 -WithLlm -Force   # 连 LLM 一起（会重启模型）
.\deploy\local\start-chroma.ps1 -Force         # 单独重启向量库服务
.\deploy\local\status.ps1             # 体检：六个组件 + 鉴权 + 维度
.\deploy\local\stop-all.ps1           # 按 logs\*.pid 停
```

日志都在 `logs\`：`llm.err.log`、`embed.err.log`、`ocr.err.log`、`gateway.err.log`、`chroma.err.log`、`web.err.log`。

### 自检脚本

```powershell
.\.venv\Scripts\python deploy\local\smoke_test.py          # 7 项：网关/鉴权/维度/对话/OCR/Web
.\.venv\Scripts\python deploy\local\check_embed.py         # 单看 embedding 的语义区分度
.\.venv\Scripts\python deploy\local\e2e_test.py --scanned  # 投喂 DOCX + 扫描版 PDF → 抽取入库 → 问答
```

`e2e_test.py` 会**写入测试数据**（几份 `mme-selftest-*` / `mme-scanned-*`）。
第一次跑还会创建 `admin / Mme@2026` 账号。正式使用前建议清一次：

```powershell
.\deploy\local\stop-all.ps1
Remove-Item -Recurse -Force chroma_db, library
Remove-Item -Force auth.db, auth.db-wal, auth.db-shm, auth.secret
Remove-Item -Recurse -Force inbox\_done\*
.\deploy\local\start-all.ps1
```

开机自启（需要管理员）见 [deploy/DEPLOY.md](../deploy/DEPLOY.md) 的 NSSM 一节；本机没管理员权限时，
可以只用「计划任务 → 登录时运行 `start-all.ps1`」。

---

## 5. 关键配置（.env）

```ini
# MME 侧：全部走网关
OPENAI_BASE_URL=http://127.0.0.1:8100/v1
OPENAI_API_KEY=mme-xxxx
LLM_MODEL=qwen3-30b

EMBEDDING_BASE_URL=http://127.0.0.1:8100/v1
EMBEDDING_API_KEY=mme-xxxx          # 可以和上面同一把
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024

OCR_BASE_URL=http://127.0.0.1:8101  # 留空 = 不启用 OCR

# 网关侧：上游真实模型服务
UPSTREAM_LLM=http://127.0.0.1:8086/v1
UPSTREAM_EMBED=http://127.0.0.1:8002/v1
UPSTREAM_OCR=http://127.0.0.1:8101
GATEWAY_ADMIN_TOKEN=随机串
GATEWAY_KEY_CONCURRENCY=2     # 同一 Key 最多几个在途请求，0=不限

# 向量库：server 模式（独立进程统一调度，避免多进程抢文件锁）
CHROMA_MODE=server
CHROMA_HOST=127.0.0.1
CHROMA_PORT=8001
CHROMA_PATH=./chroma_db

# 本机路径/端口（换机器只改这些）
MME_PORT_WEB=8300
MME_PORT_GATEWAY=8100
MME_PORT_OCR=8101
MME_PORT_EMBED=8002
MME_PORT_LLM=8086
MME_PORT_CHROMA=8001
MME_LLM_BIN=D:\models\llamacpp\bin\llama-server.exe
MME_LLM_GGUF=D:\models\qwen3-30b-q4_k_m-00001-of-00012.gguf
MME_LLM_NP=4                  # 并发槽
MME_LLM_CTX=16384            # 总上下文（每槽 = CTX/NP）
MME_EMBED_GGUF=D:\models\bge-m3\bge-m3-FP16.gguf
```

**改 embedding 模型 = 必须重建向量库**（维度不同，旧向量作废）：

```powershell
.\deploy\local\stop-all.ps1
Rename-Item chroma_db chroma_db.bak
.\deploy\local\start-all.ps1
# 把 inbox\_done 里的原件重新投喂，或用 scripts\migrate_library.py
```

---

## 6. 给别的系统发 API Key

```powershell
.\.venv\Scripts\python -m scripts.make_key --name erp --rpm 120
.\.venv\Scripts\python -m scripts.make_key --list
.\.venv\Scripts\python -m scripts.make_key --revoke 2
```

对方这样用（OpenAI SDK 不用改代码，只改 base_url）：

```python
from openai import OpenAI
client = OpenAI(base_url="http://<服务器IP>:8100/v1", api_key="mme-xxxx")
client.chat.completions.create(model="qwen3-30b", messages=[...])
client.embeddings.create(model="bge-m3", input=["..."])   # 1024 维
```

用量与错误率：`GET http://<IP>:8100/admin/usage?hours=24`，请求头 `X-Admin-Token: <GATEWAY_ADMIN_TOKEN>`。

---

## 7. 搬到另一家公司：清单与时间

| 步骤 | 要做的事 | 时间 |
|---|---|---|
| 1 | 装 Python 3.12、拷代码（不含 `.env`、`chroma_db`、`inbox`、`library`、`*.db`） | 20 分钟 |
| 2 | 装 llama.cpp 运行时（CUDA 版，和显卡驱动匹配） | 20 分钟 |
| 3 | 拷模型：LLM gguf + bge-m3 gguf | 看带宽（44 GB + 1.2 GB） |
| 4 | 改 `.env`：三处路径、五个端口、LLM 名字 | 10 分钟 |
| 5 | `pip install -r requirements*.txt` | 20 分钟（离线用 `pip download` 打包） |
| 6 | `start-all.ps1` → `status.ps1` → `smoke_test.py` | 15 分钟 |

**换显卡怎么选模型**（llama.cpp 下估算：Q4 约 0.6 GB / 10 亿参数 + 上下文）：

| 显存 | 建议 LLM | 并发建议 |
|---|---|---|
| 24 GB | Qwen2.5-32B Q4（20 GB） | `-np 2`，`-c 32768` |
| 2×24 GB | Qwen2.5-72B Q4（44 GB） | `-np 2`，`-c 65536` |
| 2×48 GB（本机） | 同上，余量可再挂 embedding/OCR | `-np 4` 也可 |
| 4×48 GB（补齐后） | Qwen3-235B-A22B Q4 或 72B FP8 + 128K | `-np 4` |

没有 GPU 也能跑：LLM 换 `qwen2.5-7b` 放 CPU（2–5 tok/s，只适合演示），Embedding/OCR 是 CPU 友好的。

**完全离线安装**（客户内网）：

```powershell
# 有网机器
pip download -r requirements.txt -r requirements-web.txt -r requirements-local.txt -d wheels
# 目标机器
pip install --no-index --find-links wheels -r requirements.txt -r requirements-web.txt -r requirements-local.txt
```

模型文件直接拷目录，`.env` 里改成新路径即可。

---

## 8. 性能与调参

LLM（`start-llm.ps1`）：

| 参数 | 本机值 | 作用 |
|---|---|---|
| `-ngl 99` | 全量上卡 | 少一层都会掉到 1 tok/s |
| `-np` | 4 | 并发槽。多槽会让单路变慢，按人数调（.env `MME_LLM_NP`） |
| `-c` | 16384（4×4K） | 每槽 4K；Qwen3-30B-A3B 原生支持 262144，显存允许就把 `.env` 的 `MME_LLM_CTX` 调大 |
| `--cache-type-k/v q8_0` | 开 | KV 显存减半，配合 `-fa on` |
| `-t 32` | 32 | 双路 Xeon 6230R（52 线程）留余量给 Web/OCR |

实测（4 槽，Qwen3-30B-A3B Q4）：MoE 每步只激活约 3B 参数，单路吞吐远高于原 72B 稠密模型；首字延迟仍随 prompt 长度增长。
4 路并发同时跑时每路约 3 s 出短答，总吞吐约 25–30 tok/s。
一次投喂里 LLM 要逐块抽取，**30 个分块约 8–12 分钟**，属于正常；批量导入建议放着跑。

Embedding：bge-m3 单条约 10–30 ms（GPU），48 条一批约 1 s。
OCR：A4 300 DPI 约 2–4 s/页（CPU），扫描件多时是瓶颈，可按需加大 `OCR_DPI` 或换 GPU 版 onnxruntime。

---

## 9. 排查

| 现象 | 处理 |
|---|---|
| `该知识库由 xxx 构建` | 换过 embedding 模型，重建 `chroma_db` |
| 网关 401 | key 没填 / 已吊销，`scripts.make_key --list` |
| 网关 429 | 超过 `--rpm`，调大或换 key |
| LLM 502 / 连接被拒 | `UPSTREAM_LLM` 指错端口；`status.ps1` 看哪个挂了 |
| 问答特别慢 | 确认 `-ngl 99` 生效（`nvidia-smi` 显存应占满 ~30 GB/卡） |
| 扫描件入库失败 | `OCR_BASE_URL` 没配 / OCR 服务没起；投喂进度里会有 `第 N 页 OCR 失败` |
| 中文乱码 | PowerShell 先 `chcp 65001`；日志按 UTF-8 打开 |
| 网关 429（`并发超限`） | 同一个 key 的在途请求超过 `GATEWAY_KEY_CONCURRENCY`；重试或给该项目单独发 key |
| `DuplicateIDError: Expected IDs to be unique` | 文档里有完全重复的记录（跨页重复表格行），旧版本会撞 ID；已修复（`ad554a0`），重启 Web 后重新投喂 |
| 问答答「资料不足」但下面有引用 | 检索到了记录，但记录里没有材质/供应商这类能把问题和数据对上的字段（例如问「索尔维有哪些 FFKM」，而记录只有牌号+数值）；新版本入库时会按页面标题补材质、按文档正文补供应商，旧数据重新投喂一次即可 |
| `database is locked` | 向量库仍是 embedded 模式；跑 `start-chroma.ps1` 并在 `.env` 设 `CHROMA_MODE=server` |
| 日志里刷 `ConnectionResetError: [WinError 10054]` | Windows 上 asyncio 的已知噪音（客户端/健康检查直接断开连接），不影响功能，可忽略 |
| 日语资料入库后是乱码 / 答不出日文内容 | ① 识别模型要换成 PP-OCRv5（自带假名），见下方「日语资料」；② 文本层缺字的页会自动整页 OCR；③ 旧数据重新投喂一次 |
| 扫描版日语 PDF 识别不出假名 | 确认 `ocr_service/models/ch_PP-OCRv5_rec_mobile.onnx` 存在，或设 `OCR_REC_MODEL` 指向模型；`/health` 的 `model` 字段能看到当前加载的模型 |

### 日语资料

- 识别模型用 **PP-OCRv5**（同一个模型覆盖中文、英文、日文假名）；老的 PP-OCRv4 中文模型认不出假名，会把「Oリング」识别成「O ング」。
- 模型文件（约 16 MB）不入 git，放在 `ocr_service/models/ch_PP-OCRv5_rec_mobile.onnx`，下载：

  ```powershell
  Invoke-WebRequest "https://modelscope.cn/api/v1/models/RapidAI/RapidOCR/repo?Revision=master&FilePath=onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx" -OutFile ocr_service\models\ch_PP-OCRv5_rec_mobile.onnx
  ```

  换模型后重启 OCR：`start-ocr.ps1 -Force`（首次调用才加载，`/health` 里 `model` 显示实际路径）。
- 日文 PDF 的字体经常没有 ToUnicode，文本层会缺字（`�`）：这类页面会自动渲染后整页 OCR（`OCR_DPI` 默认 300，可降到 220 提速）。
- 入库和展示都会把日语词换成中文（`core/normalize.py` 的 `JA_TERMS`：`潤滑油→润滑油`、`72時間→72 h` 等），牌号保持原文；回答统一中文输出。

---

## 10. 安全基线

- 模型服务只绑 127.0.0.1，对外只开 8300 /（可选）8100；
- 防火墙只放行 Domain / Private，不要用 Public；
- `GATEWAY_ADMIN_TOKEN` 定期换；给外部系统的 key 一人一把，别共用；
- `.env`、`auth.db`、`gateway.db`、`chroma_db`、`inbox/_done` 要备份（模型权重不用备份）；
- AGPL-3.0：对外提供 SaaS 需要开源或买商业授权，见 `COMMERCIAL.md`。

---

## 11. 备份与恢复

一键备份（完整提交历史 + `.env`，单文件）：

```powershell
.\deploy\local\backup.ps1                 # 默认存 D:\OC\backup
.\deploy\local\backup.ps1 -Dir E:\备份 -Keep 20
```

产物：

| 文件 | 内容 |
|---|---|
| `MME-<时间>.bundle` | 全部提交历史（约 0.4 MB），`git clone` 这个文件就能还原仓库 |
| `env-<时间>.txt` | `.env` 副本，**含 API Key**，别外传 |

恢复：

```powershell
git clone D:\OC\backup\MME-20261021-1313.bundle MME-restore
cd MME-restore
# 再照第 3 节装依赖、拷模型文件、把 env-*.txt 改回 .env
```

要推到 GitHub / 内网 Git 时：

```powershell
git remote add myrepo https://github.com/<你的账号>/<仓库>.git
git push -u myrepo master
```

> 上游 `origin`（Yourstandby/Material-Mind-Engine）只用于 fetch 同步，**不要往里 push**。
> 真怕手滑可以执行：`git remote set-url --push origin DISABLED`。
