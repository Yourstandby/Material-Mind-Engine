---
name: rag-eval
description: RAG 检索与回答质量评估：构建问题-来源评测集、计算命中率与引用准确率、设置回归门槛。在调整分块/embedding/提示词后需要验证效果没有变差、或用户问"准不准怎么证明"时使用。
---

# RAG 评测

没有评测集的调参 = 猜。20 条就能挡住大部分退化。

## 一、评测集（`eval/questions.jsonl`）

```json
{"q": "PEEK 的长期使用温度", "collection": "peek", "expect_grade": "450G", "expect_source": "victrex.pdf", "expect_value": "250"}
{"q": "FFKM 300℃ 老化 70h 压缩变形", "collection": "materials", "expect_value": "50%"}
```

字段只要三样：**问题、必须命中的来源、必须出现的数值**。
不确定标准答案的，写 `"expect_source": null`（只测检索不做断言）。

## 二、三个指标（按重要性排序）

| 指标 | 定义 | 合格线 |
| --- | --- | --- |
| 引用准确率 | 回答中 `[n]` 指向的来源，确实含有该数值 | ≥ 90% |
| 检索命中率 | 期望来源出现在 top-k 的比例 | ≥ 80% |
| 数值准确率 | 回答中的数值与来源一致 | ≥ 90% |

数值准确率权重最高——用户要的是数字，不是流畅的废话。

## 三、跑法

```python
# eval/run.py
for item in load("eval/questions.jsonl"):
    docs, metas = retrieve(item["q"], item["collection"])
    hit = any(item["expect_source"] in (m.get("source") or "") for m in metas) if item["expect_source"] else None
    answer, _ = query(item["q"], item["collection"])
    value_ok = item["expect_value"] and item["expect_value"] in answer
    rows.append({"q": item["q"], "hit": hit, "value_ok": value_ok})
print(summarize(rows))
```

输出一行汇总 + 失败清单（失败的题要能一眼看到差在哪）。

## 四、回归门槛

改分块、换 embedding、调提示词之前**先跑一次基线**，之后每次都对比：

- 任一指标下降 >5% → 不合并改动。
- 单题由对变错 → 打印 diff（问题、期望、实际检索到的来源）。

## 五、坑

| 坑 | 后果 |
| --- | --- |
| 用 LLM 给自己打分当唯一标准 | 分数虚高，掩盖退化 |
| 评测集只有简单问题 | 上线后不相关的问法全部崩 |
| 期望值写成"约 250" | 无法字符串匹配，等于没测 |
| 评测集和训练/调试用的是同一批数据 | 无法发现过拟合 |

## 六、自检

- [ ] 20 条以上，覆盖三种材料
- [ ] 每条都有明确期望数值或明确"不测数值"
- [ ] 基线结果已存档（含日期与配置）
- [ ] 失败清单能直接定位到"哪一层出问题"
- [ ] 每次改动前后各跑一次
