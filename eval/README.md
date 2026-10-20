# 评测

没有评测集的调参就是猜。这里固定 20 道题，改分块、换 embedding、调提示词后跑一遍，就知道有没有退化。

## 跑法

```bash
python -m eval.run --dry-run          # 只测检索，不调 LLM
python -m eval.run                    # 完整评测（20 次 LLM 调用）
python -m eval.run --limit 5          # 只跑前 5 题
python -m eval.run --only 压缩         # 只跑含某关键词的题
python -m eval.run --top-k 8          # 换检索深度
```

报告写到 `eval/report-<时间>.md`（已 gitignore）。数值准确率低于 90% 时脚本返回非零退出码，可直接当 CI 门禁。

## 三个指标

| 指标 | 定义 | 合格线 |
| --- | --- | --- |
| 数值准确率 | 答案里出现期望数值 | ≥ 90% |
| 引用有效率 | 答案里的 `[n]` 都能对上真实来源 | ≥ 90% |
| 检索命中率 | 期望来源出现在 top-k | ≥ 80% |

数值准确率权重最高——用户要的是数字，不是流畅的废话。

## 加题

`questions.jsonl` 一行一题：

```json
{"q": "问题", "expect_source": "文件名片段", "expect_value": "315", "material": "FFKM", "note": "说明"}
```

- `expect_source` 写 `null`：只测数值，不断言来源
- `expect_value` 写 `null`：开放题，只测检索
- `material` 可选，用于验证材质过滤

约定：题目要能追溯到某份真实资料；答案不确定的题不要写死数值。
