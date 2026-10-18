---
name: embedding-provider
description: 统一 embedding 后端（远程 API 优先、本地模型可选），含批量、重试、维度校验与切换后的重建要求。在接入 embedding 接口、检索结果莫名变差、或切换模型/维度时使用。
---

# Embedding 后端

## 一、两个后端，一套接口

```python
def embed(texts: list[str]) -> list[list[float]]
def embed_one(text: str) -> list[float]
```

上层只认这两个函数，后端可替换：

| 后端 | 何时用 | 代价 |
| --- | --- | --- |
| 远程 API | 默认。中英文都稳，无需下载模型 | 按量计费、依赖网络 |
| 本地 `bge-small-zh` | 离线/内网、数据不出机器 | 首次下载约 100MB，CPU 稍慢 |

## 二、API 后端约定

走 OpenAI 兼容的 `/embeddings`：

```bash
EMBEDDING_BASE_URL=https://api.xxx.com/v1
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024        # 可选，用于校验
```

规则：
- **批量**：一次 32–64 条，太多会被服务端拒或超时。
- **重试**：最多 3 次，指数退避（1.5s → 3s）。
- **顺序**：返回结果必须与原列表一一对应，按 index 排序后再拼。
- **过短文本**：空白或少于 2 字符的片段直接跳过，不要发请求。

## 三、维度是最容易被忽略的坑

向量库按维度建索引。换 embedding 模型 = 维度变化 = **旧数据全部作废**。

- 入库前记录 `EMBEDDING_MODEL` 与维度到 collection 的 metadata。
- 查询时比对，不一致直接报错，不要静默返回垃圾结果。
- 切换模型后必须 `--reset` 重建整个 collection，没有捷径。

## 四、不要把 embedding 和 LLM 混用一套配置

两者可能是不同服务商、不同 key。分开：

| 用途 | 变量前缀 |
| --- | --- |
| 生成回答 | `OPENAI_*` / `LLM_MODEL` |
| 向量化 | `EMBEDDING_*` |

缺少 `EMBEDDING_*` 时，应给出明确提示，而不是偷偷回退到另一个模型——那会让检索质量无声劣化。

## 五、自检

- [ ] `embed(["测试"])` 返回一条向量，长度符合预期
- [ ] 同一文本两次调用结果一致（幂等）
- [ ] 批量 64 条不报错
- [ ] 断网时抛可读错误，不是卡死
- [ ] collection metadata 里存了模型名与维度
- [ ] 切换模型后有重建流程
