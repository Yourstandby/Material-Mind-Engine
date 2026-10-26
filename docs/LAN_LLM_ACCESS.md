# MME 模型网关 · 局域网接入说明

> 面向局域网内的其他项目。按本文可以完成接入，不需要了解 MME 内部实现。
> 更新：2026-10-26 · 维护：MME

## 0. 一句话

OpenAI 兼容接口，把下面三项填进任意 OpenAI SDK 即可：

```text
BASE_URL = http://172.16.1.210:8100/v1
MODEL    = qwen2.5-72b-instruct
API_KEY  = <单独发给你的 key，勿写入代码仓库>
```

## 1. 参数速查

| 项 | 值 |
|---|---|
| Base URL | `http://172.16.1.210:8100/v1` |
| 鉴权 | `Authorization: Bearer <API_KEY>`（`/v1/*` 与 `/ocr` 都校验） |
| 对话模型 | `qwen2.5-72b-instruct` |
| 向量模型 | `bge-m3`（1024 维） |
| 单请求上下文 | 32,768 tokens（服务端 4 个槽位，每槽 32k） |
| 并发 | 每个 Key 最多 2 个在途请求，超出立即 429，不排队 |
| 速率 | 每个 Key 120 次/分钟 |
| 传输 | 内网 HTTP；流式走 SSE（`text/event-stream`） |
| 健康检查 | `GET /health`，免鉴权 |

## 2. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/models` | 模型列表，返回 `qwen2.5-72b-instruct` |
| POST | `/v1/chat/completions` | 对话；`"stream": true` 时返回 SSE |
| POST | `/v1/embeddings` | 文本向量，`model: "bge-m3"`，1024 维，支持批量 |
| POST | `/ocr` | 图片 OCR，`multipart/form-data`，字段名 `file` |
| GET | `/health` | 存活检查，返回 `{"ok": true, ...}` |

不支持 OpenAI Responses API、图片输入、音频；`/v1/chat/completions` 以外的参数按 OpenAI 标准即可。

## 3. 最小可用代码

### 3.1 Python（openai SDK）

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://172.16.1.210:8100/v1",
    api_key=os.environ["MME_API_KEY"],
    timeout=120,      # 非流式；流式请逐块控制
    max_retries=0,    # 重试自己控制，见 §5
)

resp = client.chat.completions.create(
    model="qwen2.5-72b-instruct",
    messages=[
        {"role": "system", "content": "你是一个严谨的工程师。"},
        {"role": "user", "content": "用三句话说明氟橡胶的耐温范围。"},
    ],
    temperature=0.2,
    max_tokens=800,
)
print(resp.choices[0].message.content)
```

### 3.2 Python 流式

```python
stream = client.chat.completions.create(
    model="qwen2.5-72b-instruct",
    messages=[{"role": "user", "content": "写一段 100 字的项目简介"}],
    temperature=0.3,
    max_tokens=512,
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### 3.3 curl

```bash
curl -s http://172.16.1.210:8100/v1/chat/completions \
  -H "Authorization: Bearer $MME_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-72b-instruct",
    "messages": [{"role": "user", "content": "你好"}],
    "temperature": 0.2,
    "max_tokens": 256
  }'
```

### 3.4 PowerShell（注意中文要发 UTF-8 字节）

```powershell
$body = @{
  model    = "qwen2.5-72b-instruct"
  messages = @(@{ role = "user"; content = "你好" })
  max_tokens = 256
} | ConvertTo-Json -Depth 5

Invoke-RestMethod "http://172.16.1.210:8100/v1/chat/completions" `
  -Method Post `
  -Headers @{ Authorization = "Bearer $env:MME_API_KEY" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

不转 UTF-8 字节时，中文会被 PowerShell 按 GBK 发出去，模型收到乱码。

### 3.5 Node / TypeScript（原生 fetch，SSE 手工解析）

```ts
const BASE = "http://172.16.1.210:8100/v1";
const KEY = process.env.MME_API_KEY!;

const res = await fetch(`${BASE}/chat/completions`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${KEY}` },
  body: JSON.stringify({
    model: "qwen2.5-72b-instruct",
    messages: [{ role: "user", content: "你好" }],
    stream: true,
    max_tokens: 512,
  }),
});
if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);

const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buf = "";

for (;;) {
  const { done, value } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  const blocks = buf.split("\n\n");
  buf = blocks.pop() ?? "";
  for (const block of blocks) {
    for (const line of block.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6).trim();
      if (data === "[DONE]") return;
      const chunk = JSON.parse(data);
      const text = chunk.choices?.[0]?.delta?.content;
      if (text) process.stdout.write(text);
    }
  }
}
```

## 4. 流式响应格式

- `Content-Type: text/event-stream`
- 每个事件一行 `data: {...}`，空行分隔
- 增量在 `choices[0].delta.content`，首块可能是 `role`，`content` 为 `null`
- 正常结束：`data: [DONE]`
- 上游中途出错：可能收到 `data: {"error": "..."}` 后直接断开，**没有 `[DONE]`**

所以客户端要同时处理两种结束：读到 `[DONE]`，或连接关闭但没有 `[DONE]`（视为不完整，重试整次请求）。

## 5. 错误与重试

| 状态 | 含义 | 处理 |
|---|---|---|
| 401 | 缺少 / 无效 / 已停用 key | 检查 `Authorization`，不要重试 |
| 429 `并发超限：同一 API Key 最多 2 个在途请求，请重试` | 同一 key 第 3 个请求 | 按 `Retry-After` 等待，串行化，不要盲目加并发 |
| 429 `超过速率限制（120 次/分钟）` | 分钟级限速 | 指数退避 |
| 400 | 参数错误 / 超出上下文 | 修请求，不要重试 |
| 5xx | 上游模型不可用或超时 | 退避重试 + 告警 |

429 响应中，并发超限会带 `Retry-After: 2`（秒）；分钟级限速不带，按指数退避即可。

参考实现：

```python
import random
import time

import httpx

def post_with_retry(url: str, payload: dict, key: str, attempts: int = 3):
    for i in range(attempts):
        try:
            r = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
                timeout=httpx.Timeout(120, connect=5),
            )
        except httpx.TimeoutException:
            if i == attempts - 1:
                raise
            time.sleep(2 ** i)
            continue

        if r.status_code in (429, 500, 502, 503, 504):
            if i == attempts - 1:
                r.raise_for_status()
            wait = float(r.headers.get("Retry-After", 2 ** i))
            time.sleep(wait + random.random() * 0.5)
            continue

        r.raise_for_status()
        return r.json()
```

## 6. 客户端约定（重要）

1. **必须设置超时**。网关对上游是无限等待，上游卡住时请求会一直挂着。建议：连接 5 s，非流式总超时 120 s，流式按“无新数据 120 s”判死。
2. **必须显式设置 `max_tokens`**。不设时服务端会一直生成到 EOS，长回答会把槽位占满。
3. **上下文 32k tokens**（含输入 + 输出）。长文档要先切块，不要把整篇塞进去。
4. **并发上限 2**。批量任务用 2 个 worker 的队列；需要更高并发时申请独立 key，而不是多开线程。
5. **温度**：抽取 / 问答建议 `0.1–0.3`；服务端默认是 `0.8`，不传会偏发散。
6. **流式响应里不要依赖 `usage`**（默认不返回）；需要 token 数时用非流式或在客户端自行估算。
7. **不要自行拼接 Qwen 的 special token**；服务端会用模型自带的 chat template。
8. **model 字段必须写 `qwen2.5-72b-instruct`**，写错会 400。

## 7. 其他端点

### 向量

```python
resp = client.embeddings.create(
    model="bge-m3",
    input=["氟橡胶压缩永久变形", "FKM compression set"],
)
vec = resp.data[0].embedding   # 1024 个 float
```

建库和查询要用同一个模型；切换模型必须重建向量库。

### OCR

```bash
curl -s http://172.16.1.210:8100/ocr \
  -H "Authorization: Bearer $MME_API_KEY" \
  -F "file=@page.png"
```

返回：

```json
{"text": "整页文本", "lines": [{"text": "一行", "score": 0.98, "box": [[x1,y1], ...]}]}
```

限制：单张 ≤ 20 MB；PNG / JPG；CPU 识别，A4 页面约 5–12 s；同样受每 Key 2 并发限制。

## 8. 网络

- 网关监听 `0.0.0.0:8100`，只应在内网可达。
- 先在对方机器上验证：

  ```bash
  curl http://172.16.1.210:8100/health
  # {"ok":true,"upstream":"http://127.0.0.1:8010/v1","embed":"http://127.0.0.1:8002/v1"}
  ```

- 如果超时：确认在同一网段（172.16.0.0/23）、能 ping 通，然后在 MME 主机上用管理员 PowerShell 执行 `deploy/local/open-firewall.ps1`（只放行 8100 / 8300，限本网段）。Windows 网络配置文件需为「专用网络」，公用网络下规则可能不生效。
- 这个 IP 建议在路由器 / 交换机上做静态绑定；换了 IP，对方只需改 `base_url`。
- **不要做端口映射到公网**；链路是明文 HTTP，提示词里不要放密码、身份证号等敏感信息。

## 9. 验收清单

| # | 检查 | 期望 |
|---|---|---|
| 1 | `GET /health` | `{"ok": true}` |
| 2 | `GET /v1/models` 带 key | 列表含 `qwen2.5-72b-instruct` |
| 3 | 非流式 chat | 200，`choices[0].message.content` 非空 |
| 4 | 流式 chat | 连续 `data:` 帧，最后 `data: [DONE]` |
| 5 | 同时发 3 个请求 | 第 3 个返回 429，且带 `Retry-After` |

## 10. Key 管理（MME 侧）

```bash
# 新建（一个项目一把，别共用）
python -m scripts.make_key --name <项目名> --rpm 120

# 查看
python -m scripts.make_key --list

# 吊销
python -m scripts.make_key --revoke <id>
```

并发额度与速率都按 key 计算；共用一把 key 会让两个项目互相顶掉。

## 11. 给 AI 编程工具的说明（可直接粘贴）

```text
为一个局域网项目接入 OpenAI 兼容的 LLM 服务，按以下约束实现客户端：

- Base URL: http://172.16.1.210:8100/v1
- 模型: qwen2.5-72b-instruct（向量模型 bge-m3，1024 维）
- 鉴权: Authorization: Bearer <MME_API_KEY>，key 从环境变量读，禁止硬编码
- 每个 key 最多 2 个在途请求；第 3 个立即返回 429（带 Retry-After: 2），不排队
- 限速 120 次/分钟；单请求上下文 32,768 tokens
- 必须显式设置 max_tokens 与超时（连接 5s，非流式 120s）
- 429 与 5xx 做指数退避重试（最多 3 次，尊重 Retry-After）；400/401 不重试
- 流式用 SSE：逐行解析 "data: "，以 "data: [DONE]" 结束；连接关闭但无 [DONE] 视为失败并重试整次请求
- 传输是内网明文 HTTP，不要发送敏感信息

请交付：
1) 一个 client 封装：chat()、chat_stream()、embed()；内部用信号量把并发限制在 2
2) 统一错误类型（AuthError / RateLimited(retry_after) / UpstreamError）
3) 三个测试：正常对话、429 重试、流式拼接结果与 [DONE] 处理
```
