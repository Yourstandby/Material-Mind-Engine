# 部署到公司 Windows 服务器

面向：Windows Server 2019 / 2022，已经在一台机器上跑着本地 LLM（Ollama / vLLM / Xinference 等）。

---

## 0. 前置条件

| 项 | 要求 | 检查命令 |
|---|---|---|
| Python | 3.11+（推荐 3.12） | `python -V` |
| 磁盘 | ≥ 20 GB | — |
| 内存 | ≥ 8 GB（不含 LLM） | — |
| 端口 | 8000（Web）、8100（网关） | `netstat -ano \| findstr :8000` |
| NSSM（可选） | 装成服务时用 | `nssm version` |

> 如果服务器不能上外网，先在能上网的机器上 `pip download` 依赖，再拷贝 `wheels/` 目录离线安装。

---

## 1. 把代码放到服务器

```powershell
# 方式一：Git
git clone https://github.com/Yourstandby/Material-Mind-Engine.git D:\MME

# 方式二：压缩包
# 解压到 D:\MME，确认目录里有 server\ core\ ingest\ web\ deploy\
```

**不要把本地数据带过去**：`chroma_db\`、`inbox\`、`.env`、`auth.db`、`library\` 都不需要拷贝。

---

## 2. 一键安装

管理员 PowerShell：

```powershell
cd D:\MME
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\install.ps1
```

常用参数：

```powershell
# 指定上游 LLM（你已部署好的那个服务）
.\deploy\install.ps1 -Upstream "http://192.168.1.10:11434/v1"

# 装服务 + 开防火墙
.\deploy\install.ps1 -InstallServices -OpenFirewall -Upstream "http://192.168.1.10:11434/v1"
```

脚本会做五件事：建 `.venv`、装依赖、建数据目录、生成 `.env`、可选装服务。

---

## 3. 配置模型地址

编辑 `D:\MME\.env`：

```ini
# 全部走内网网关
OPENAI_BASE_URL=http://127.0.0.1:8100/v1
EMBEDDING_BASE_URL=http://127.0.0.1:8100/v1
LLM_MODEL=qwen2.5:7b
EMBEDDING_MODEL=bge-m3

# 网关上游（你的 Windows 服务）
UPSTREAM_LLM=http://192.168.1.10:11434/v1
GATEWAY_ADMIN_TOKEN=换成一串随机字符
```

> `LLM_MODEL` 必须填上游实际存在的模型名。Ollama 用 `ollama list` 查看。

---

## 4. 首次初始化（顺序很重要）

```powershell
cd D:\MME

# ① 起网关
.\.venv\Scripts\python -m uvicorn gateway.main:app --host 0.0.0.0 --port 8100

# ② 另开一个窗口，发 API Key
.\.venv\Scripts\python -m scripts.make_key --name mme --rpm 600

# ③ 把打印出来的 mme-xxx 写进 .env
#    OPENAI_API_KEY=mme-xxx
#    EMBEDDING_API_KEY=mme-xxx

# ④ 起 Web
.\.venv\Scripts\python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 3
```

浏览器打开 `http://<服务器IP>:8000` → 注册第一个账号（自动成为管理员）。

---

## 5. 装成 Windows 服务

先装 [NSSM](https://nssm.cc/download)，把 `win64\nssm.exe` 放进 `C:\Windows\System32` 或加入 PATH。

```powershell
$py = "D:\MME\.venv\Scripts\python.exe"

# Web
nssm install MME-Web $py -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 3
nssm set MME-Web AppDirectory D:\MME
nssm set MME-Web Start SERVICE_AUTO_START
nssm set MME-Web AppStdout D:\MME\logs\web.log
nssm set MME-Web AppStderr D:\MME\logs\web.err.log
nssm set MME-Web AppEnvironmentExtra WATCH_INBOX=1
nssm start MME-Web

# 网关
nssm install MME-Gateway $py -m uvicorn gateway.main:app --host 0.0.0.0 --port 8100
nssm set MME-Gateway AppDirectory D:\MME
nssm set MME-Gateway Start SERVICE_AUTO_START
nssm set MME-Gateway AppEnvironmentExtra UPSTREAM_LLM=http://192.168.1.10:11434/v1 GATEWAY_ADMIN_TOKEN=你的令牌
nssm start MME-Gateway
```

停止 / 卸载：

```powershell
nssm stop MME-Web ; nssm remove MME-Web confirm
```

---

## 6. 防火墙

```powershell
New-NetFirewallRule -DisplayName "MME-Web" -Direction Inbound -Protocol TCP `
  -LocalPort 8000 -Action Allow -Profile Domain,Private
New-NetFirewallRule -DisplayName "MME-Gateway" -Direction Inbound -Protocol TCP `
  -LocalPort 8100 -Action Allow -Profile Domain,Private
```

**只放行 Domain / Private**，不要用 Public。网关 8100 是否对内网开放，取决于是否让别的系统直接调模型。

---

## 7. 验证清单

| # | 检查 | 命令 / 方式 | 期望 |
|---|---|---|---|
| 1 | 网关健康 | `curl http://127.0.0.1:8100/health` | `{"ok":true,...}` |
| 2 | 网关鉴权 | 不带 key 调 `/v1/models` | 401 |
| 3 | Web 健康 | `curl http://127.0.0.1:8000/api/health` | `chroma.ok = true` |
| 4 | 登录页 | 浏览器访问 `/` | 跳转到 `/login` |
| 5 | 注册 | 创建管理员 | 进入主界面 |
| 6 | 投喂 | 拖入一份 PDF | 进度跑完，出现记录 |
| 7 | 问答 | 问一个牌号 | 返回带 `[n]` 引用 |
| 8 | 原文 | 点引用里的「原文」 | 打开 PDF 并跳到对应页 |
| 9 | 权限 | 退出后直接访问 `/api/library` | 401 |

---

## 8. 更新版本

```powershell
# 停服务
nssm stop MME-Web
nssm stop MME-Gateway

# 拉代码（数据目录不受影响）
cd D:\MME
git pull

# 补依赖（requirements 有变化时）
.\.venv\Scripts\python -m pip install -q -r requirements.txt -r requirements-web.txt

# 起服务
nssm start MME-Gateway
nssm start MME-Web
```

---

## 9. 备份

**必须备份的**：

```
D:\MME\.env              密钥与模型配置
D:\MME\auth.db           账号
D:\MME\gateway.db        API Key 与用量
D:\MME\chroma_db\        向量库
D:\MME\library\          文献索引
D:\MME\inbox\_done\      原始文件（「打开原文」依赖它）
```

一条命令：

```powershell
Compress-Archive -Path D:\MME\.env,D:\MME\auth.db,D:\MME\gateway.db,D:\MME\chroma_db,D:\MME\library,D:\MME\inbox -DestinationPath "D:\backup\mme-$(Get-Date -f yyyyMMdd).zip"
```

建议每天定时任务跑一次，保留最近 7 份。

---

## 10. 常见问题

**Q：`python` 不是内部命令**
安装 Python 时没勾 Add to PATH。用完整路径 `C:\Python312\python.exe` 重新执行脚本。

**Q：端口被占用**
`netstat -ano | findstr :8000` 找到 PID，`taskkill /F /PID <PID>`。或换端口：`-Port 8001`。

**Q：投喂失败，提示 embedding 错误**
检查 `.env` 的 `EMBEDDING_BASE_URL` 是否指向 8100，以及 key 是否已发。先验证网关：`curl http://127.0.0.1:8100/v1/embeddings -H "Authorization: Bearer <key>"`。

**Q：中文乱码**
PowerShell 执行脚本前 `chcp 65001`；日志文件用 UTF-8 打开。

**Q：多进程下重复投喂**
不会。`watcher.lock` 保证只有一个 worker 守护 inbox。

**Q：忘记管理员密码**
`.\.venv\Scripts\python -m scripts.reset_password -u admin`

**Q：想换模型**
只改 `.env` 里的 `LLM_MODEL` / `EMBEDDING_MODEL`，重启服务。**换 embedding 模型必须重建向量库**（不同模型向量不兼容），否则入库会报「该知识库由 xxx 构建」。
