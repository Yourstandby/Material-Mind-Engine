# 贡献指南

## 新增一种材料

1. 建 profile：在 `profiles/` 下新建 `<name>.yaml`，包含：
   - `field_map`：字段映射（11 个字段，与现有 profile 保持一致）
   - `source_dir`：数据源根目录，默认 `.firecrawl/`
   - `keywords`：数据源相关性关键词（extract 会用它跳过无关文档）
   - `extraction_prompt`：领域抽取提示词，含 `{text}` 占位符
2. 抓数据：把公开技术页 / datasheet 抓成 markdown，按
   `.firecrawl/<hostname>/<path>/index.md` 存放；若按材料分目录，放到
   `.firecrawl/<profile 名>/` 下（extract 会优先读该目录）。
3. 跑 extract：`python extract.py --profile profiles/<name>.yaml`
   → 输出 `raw_json/<name>/merged.json`（单文件 JSON 与 merged 均由脚本生成）
4. 跑清洗 + 入库 + 问答：
   ```bash
   python data_cleaner.py raw_json/<name>/merged.json cleaned_<name>.json --profile profiles/<name>.yaml
   python vector_store.py cleaned_<name>.json --collection <name> --reset
   python query.py "..." --collection <name>
   ```

## 提交 PR

1. Fork 仓库并新建分支：`git checkout -b feat/<short-name>`
2. 保持改动聚焦：一次 PR 只做一件事，不要顺手重构无关代码。
3. 自测通过：
   ```bash
   python -m pytest tests/
   ```
4. 提交信息说明“为什么改”，而非“改了什么”。
5. 发起 PR，描述里写：动机、改动点、验证方式（命令 + 结果）。

## 报告 Bug

请在 issue 中提供：

- 复现步骤（精确到命令行与参数）
- 期望结果 vs 实际结果
- 报错原文（完整 traceback，不要截断）
- 环境信息：Python 版本、`pip list` 中相关包版本、操作系统
- 相关文件片段或配置（`.env` 请勿包含密钥）
