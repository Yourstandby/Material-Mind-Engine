# 局域网接入：把本机模型服务给其他项目用

> 适用：别的项目/别的机器要调本机的 LLM、Embedding、OCR。
> 只走**网关 8100**，不要直连 8010 / 8002 / 8101（那三个只绑 127.0.0.1，外面本来也连不上）。

---

## 0. 另一台电脑接入：五步

### 第一步：确认这台机器放行了端口（只需做一次）

Windows 防火墙默认拦入站。在**本机**用**管理员** PowerShell 跑一次：

```powershell
cd D:\OC\MME
.\deploy\local\open-firewall.ps1
```

只放行 `8100`（网关）和 `8300`（Web），且仅限同网段。
模型端口 `8001 / 8002 / 8010 / 8101` 不要放行，它们绑的是 127.0.0.1，放行也没用。

### 第二步：在另一台电脑上探一下通不通

```powershell
Test-NetConnection 172.16.1.210 -Port 8100      # TcpTestSucceeded 要是 True
Test-NetConnection 172.16.1.210 -Port 8300
```

- `False` → 回到第一步（或本机 IP 变了：`ipconfig` 看 `NIC1`）
- `True` → 继续

### 第三步：拷三个文件过去

```
deploy/clients/mme-client.env      # 真实地址 + Key（别外传）
deploy/clients/check_lan.py        # 自检脚本，只用标准库
```

放在同一个目录下即可，`check_lan.py` 会自动读旁边的 `mme-client.env`。

### 第四步：跑自检

```powershell
python check_lan.py
```

期望输出：

```
  [PASS] 鉴权  无 key 返回 401
  [PASS] 模型列表  ['qwen2.5-72b-instruct']
  [PASS] 对话  0.6s  收到
  [PASS] 向量  bge-m3 dim=1024

全部通过
```

顺手测 OCR（可选）：`python check_lan.py --image 任意带字的图片.png`

### 第五步：填进目标项目的配置

照第 3 节的填法（OpenAI 兼容四要素）：

```ini
base_url = http://172.16.1.210:8100/v1
api_key  = mme-xxxx
chat 模型 = qwen2.5-72b-instruct
embedding 模型 = bge-m3（维度 1024）
```

**给这台新电脑单独发一个 key**（别共用，方便限速和排查）：

```powershell
cd D:\OC\MME
.\.venv\Scripts\python -m scripts.make_key --name 设计部电脑 --rpm 120
```

### 连不上时对着这张表看

| 现象 | 原因 | 处理 |
|---|---|---|
| `Test-NetConnection` 为 False / 超时 | 防火墙没放行，或不在同一网段 | 第一步 `open-firewall.ps1`；确认 IP 是 `172.16.1.210` |
| 401 | key 错 / 已吊销 / 少了 `Bearer ` 前缀 | `scripts.make_key --list` 核对 |
| 429 `并发超限` | 同一 key 同时在途超过 2 个 | 客户端串行化，或给项目单独发 key |
| 429 其他 | 超过该 key 的每分钟请求数 | 调大 `--rpm` |
| 404 `model not found` | 模型名写错 | 用 `/v1/models` 返回的名字 |
| 建库时报维度错 | embedding 维度没填 1024 | 填 1024 |
| 本机能通、别的机器超时 | 服务只绑了 127.0.0.1 | 只可能是 8100/8300，别试 8010/8002/8101 |

---

## 1. 三个能力，一个入口

| 能力 | 地址 | 模型名 | 说明 |
|---|---|---|---|
| 对话 | `http://172.16.1.210:8100/v1/chat/completions` | `qwen2.5-72b-instruct` | 全机 4 个并发槽，每槽 32K；**单 Key 最多 2 个在途** |
| 向量 | `http://172.16.1.210:8100/v1/embeddings` | `bge-m3` | **固定 1024 维**，8192 上下文 |
| OCR | `http://172.16.1.210:8100/ocr` | — | multipart 上传图片，返回 `{text, lines, elapsed_ms}` |
| 模型列表 | `GET /v1/models` | — | 用来确认模型名 |
| 健康 | `GET /health` | — | 不需要 key |
| 用量 | `GET /admin/usage?hours=24` | — | 需 `X-Admin-Token` 头 |
| 在途数 | `GET /admin/inflight` | — | 需 `X-Admin-Token` 头 |

鉴权：`Authorization: Bearer mme-xxxx`（除 `/health` 外都要）。

超限返回：`429`。两种原因——超过每分钟请求数（rpm），或**同一 Key 的在途请求数超限**（默认 2）。

---

## 2. 直接能用的配置

`deploy/clients/mme-client.env`（含真实 key，不提交）内容就是这些：

```ini
MME_BASE_URL=http://172.16.1.210:8100/v1
MME_API_KEY=mme-xxxx
MME_LLM_MODEL=qwen2.5-72b-instruct
MME_EMBED_MODEL=bge-m3
MME_EMBED_DIM=1024
MME_OCR_URL=http://172.16.1.210:8100/ocr
```

先把 `deploy/clients/check_lan.py` 拷到目标机器跑一遍，四项全 PASS 再往下接：

```powershell
python check_lan.py --base-url http://172.16.1.210:8100/v1 --key mme-xxxx
python check_lan.py --image 测试.png        # 顺带测 OCR
```

---

## 3. 各框架怎么填

### OpenAI SDK（Python）

```python
from openai import OpenAI
client = OpenAI(base_url="http://172.16.1.210:8100/v1", api_key="mme-xxxx")

client.chat.completions.create(model="qwen2.5-72b-instruct",
                               messages=[{"role": "user", "content": "你好"}])
client.embeddings.create(model="bge-m3", input=["FFKM 压缩永久变形"])   # 1024 维
```

### OpenAI SDK（JS / Node）

```js
const client = new OpenAI({ baseURL: "http://172.16.1.210:8100/v1", apiKey: "mme-xxxx" });
```

### LangChain

```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
llm = ChatOpenAI(base_url="http://172.16.1.210:8100/v1", api_key="mme-xxxx",
                 model="qwen2.5-72b-instruct", temperature=0.3)
emb = OpenAIEmbeddings(base_url="http://172.16.1.210:8100/v1", api_key="mme-xxxx",
                       model="bge-m3", dimensions=1024)
```

### curl

```bash
curl http://172.16.1.210:8100/v1/chat/completions \
  -H "Authorization: Bearer mme-xxxx" -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5-72b-instruct","messages":[{"role":"user","content":"你好"}]}'

curl http://172.16.1.210:8100/ocr \
  -H "Authorization: Bearer mme-xxxx" -F "file=@page.png"
```

### Dify / FastGPT / AnythingLLM / Open WebUI / Cherry Studio / Chatbox

这些工具都是"OpenAI 兼容"填法，填四处：

| 字段 | 值 |
|---|---|
| API Base / Base URL | `http://172.16.1.210:8100/v1` |
| API Key | `mme-xxxx` |
| 模型名 | `qwen2.5-72b-instruct` |
| Embedding 模型 | `bge-m3`（维度填 **1024**） |

> 注意：这些工具里选 embedding 时如果能填"维度"，一定填 1024，填错会在建库时直接报错。
> 工具里如果要求"模型列表自动获取"，走的也是 `/v1/models`，能拉到就说明配对了。

---

## 4. 配额：一个项目一把 key

```powershell
cd D:\OC\MME
.\.venv\Scripts\python -m scripts.make_key --name erp --rpm 120     # 生成
.\.venv\Scripts\python -m scripts.make_key --list                   # 查看
.\.venv\Scripts\python -m scripts.make_key --revoke 2               # 吊销
```

看用量与在途数：

```bash
curl "http://172.16.1.210:8100/admin/usage?hours=24" -H "X-Admin-Token: <GATEWAY_ADMIN_TOKEN>"
curl "http://172.16.1.210:8100/admin/inflight"      -H "X-Admin-Token: <GATEWAY_ADMIN_TOKEN>"
```

**别让所有项目共用一把 key**——rpm 和并发上限都是按 key 算的，共用的话 A 项目跑批会把 B 项目一起打成 429，而且分不清是谁用的。

并发上限在 `.env` 里：`GATEWAY_KEY_CONCURRENCY=2`（0 = 不限）。改完重启网关生效：`.\deploy\local\start-gateway.ps1 -Force`。

---

## 5. 会不会互相干扰？会，但可以逐条掐掉

### 五个真实的干扰点

| # | 干扰 | 现象 | 怎么防 |
|---|---|---|---|
| 1 | **端口抢占** | 新项目也监听 8100/8300 → 起不来，或把请求接走 | 用下面的端口台账，禁止项目自带模型服务 |
| 2 | **显存争抢** | 新项目自己起 Ollama/vLLM → OOM，或把 LLM 挤到 CPU | 只允许走网关；本机不再给第二个模型进程 |
| 3 | **算力排队** | A 项目一次发 10 个请求，占满并发槽，B 项目全部卡住 | 一项目一 key + rpm + **每 key 并发闸门**；重活错峰 |
| 4 | **向量库文件锁** | 两个进程同时开 `chroma_db` → `database is locked` / 数据错乱 | **已改 server 模式**，见第 6 节 |
| 5 | **数据混库** | A 项目的数据被 B 项目检索到 | 一项目一 collection，或一项目一库 |

### 现在的隔离边界（已经生效）

- 模型服务只绑 `127.0.0.1`，局域网**物理上只能访问 8100 和 8300**；
- 网关有 key 鉴权 + 每 key 的 rpm 限流 + **每 key 并发闸门** + 用量审计；
- 并发闸门：同一 Key 最多 `GATEWAY_KEY_CONCURRENCY`（默认 2）个在途请求，超了立刻 429，不会排队占槽；
- 全机 4 个 LLM 并发槽，两个项目各 2 路可以同时满速跑；
- 每个请求的用量记在 `gateway.db`，出问题能按 key 定位。

### 已经落实的三件事

1. ✅ **并发闸门**：`gateway/main.py` 里的 `acquire_slot/release_slot`，配置项 `GATEWAY_KEY_CONCURRENCY`（0 = 不限）。
   实测：同一 key 同时发 3 个 → 2 个成功、1 个 0.9 秒内 429；两个 key 各发 2 个 → 4 个同时跑完（3.2 秒）。
2. ✅ **加槽**：`-np 4`（每槽 32K，总上下文 131072），显存 68.7 GB / 92 GB。改 `.env` 的 `MME_LLM_NP` / `MME_LLM_CTX` 即可。
3. ✅ **向量库改 server 模式**：独立进程 `127.0.0.1:8001`，见第 6 节。

### 还可以做（按需）

- **embedding 挪到 CPU**：bge-m3 在双路 52 线程 Xeon 上跑短文本足够快（10~30ms/条），把 2 GB 显存也让给 LLM，少一个 GPU 争抢源。
- **按 key 记 Token 配额**：现在是 rpm + 在途数，还没有"每月多少 token"的硬额度。


---

## 6. "局域网数据库"怎么做才不乱

先把两件事分开：

### (a) 向量库（知识库检索）

**现在 MME 用的是 Chroma server 模式（`CHROMA_MODE=server`，进程在 `127.0.0.1:8001`）：**
写操作由这一个进程统一调度，MME 的多个 worker / 多个本机项目通过 HTTP 访问，不会再出现文件锁冲突。

但**别的机器**要用这个库，还有两道坎：Chroma 自身没有鉴权（谁连上谁能删库），而且直接把 8001 暴露到局域网等于把库交给全网。

| 方案 | 适用 | 做法 |
|---|---|---|
| **本机共享（已就绪）** | 同机多进程/多项目 | 直接连 `127.0.0.1:8001`，`CHROMA_MODE=server` |
| **局域网共享（推荐 pgvector）** | 多机器、要备份/权限/事务 | PostgreSQL + pgvector，**每个项目一个 schema 或 database**，向量列固定 `vector(1024)` |
| 局域网共享（轻量） | 临时看数、低频 | Chroma server 前面套一层带 basic-auth 的 nginx，别裸奔 |

不要做的事：
- ❌ 多个项目挂同一个 `chroma_db` 目录（embedded 模式必锁死）；
- ❌ 多个项目往同一个 collection 写（数据互相污染，且删不干净）；
- ❌ 不同 embedding 模型共用一个库（bge-m3 建的是 1024 维，换个 768 维的模型直接对不上，MME 会报"该知识库由 xxx 构建"）。

### (b) 关系型数据（业务表）

MME 自己用的是 SQLite（`auth.db`、`gateway.db`、`library/index.json`），WAL 模式，**单机多进程够用，但不适合多机共享**。

要"局域网数据库"，直接上 **PostgreSQL**：
- 一个实例，按项目分 database；
- 备份、账号权限、并发写都是它擅长的事；
- MME 这套继续用 SQLite 就行，两者不冲突（MME 是"知识库应用"，不是"共享数据库"）。

### 推荐的最终形态

```
┌─ 其他项目（各自独立）──┐        ┌──── 本机（172.16.1.210）────┐
│ 项目A → key A          │──────► │ 网关 8100（鉴权/限流/审计）  │
│ 项目B → key B          │        │   ├─ LLM 8010               │
│ 项目C → key C          │        │   ├─ Embedding 8002         │
└────────────────────────┘        │   └─ OCR 8101               │
                                  │                             │
   PostgreSQL 5432 ◄──────────────┤ 向量库：pgvector 或         │
   （共享业务库，按项目分库）      │ chroma server 8001          │
                                  └─────────────────────────────┘
```

- **模型**：只有一套，全部走网关 → 不会互相抢显存；
- **数据**：PG 分库 / 向量分 collection → 不会互相串数据；
- **配额**：一项目一 key → 出问题能定位、能限速、能单独吊销。

---

## 7. 端口台账（新项目照着避让）

| 端口 | 归属 | 绑定 | 能不能占 |
|---|---|---|---|
| 80 | reagent-nginx（Docker） | 0.0.0.0 | ❌ 已占 |
| 8000 | reagent-app（Docker） | 0.0.0.0 | ❌ 已占 |
| 8001 | MME Chroma server | 127.0.0.1 | ❌ 已占 |
| 8002 | MME Embedding | 127.0.0.1 | ❌ |
| 8010 | MME LLM | 127.0.0.1 | ❌ |
| 8100 | MME 网关 | 0.0.0.0 | ❌ |
| 8101 | MME OCR | 127.0.0.1 | ❌ |
| 8300 | MME Web | 0.0.0.0 | ❌ |
| 5432 | （预留）PostgreSQL | — | ⚠️ 规划给共享业务库 |
| 8300–8399 | 新项目 Web | — | ✅ 建议段 |
| 8400–8499 | 新项目 API | — | ✅ 建议段 |

新项目起服务前先 `netstat -ano | findstr ":8301 "` 确认没被占。

---

## 8. 已知限制

- **单路 72B ≈ 11–16 tok/s**，4 路并发时每路约 8–13 tok/s（实测 4 路短答 3.2 秒全部返回）。适合内网几个人用，不适合高并发产品。
- 单 Key 默认最多 2 个在途请求（`GATEWAY_KEY_CONCURRENCY`），需要更多就调大或按项目分 key。
- 32K 上下文（每槽），超长文档要切块，不要整篇塞。
- 网关做转发 + 限流 + 并发闸门，但**没有请求队列和优先级**；重活请错峰（比如批量入库放到晚上）。
- OCR 是 CPU 版，A4 扫描页约 2–4 秒，批量扫描件会明显吃 CPU。
