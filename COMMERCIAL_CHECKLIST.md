# 商业化前检查清单（COMMERCIAL_CHECKLIST）

生成日期：2026-10-16

## 1. 项目许可证
- LICENSE 类型：MIT License（Copyright (c) 2026 Material-Mind-Engine Contributors）
- 结论：可商用、可闭源分发、可修改再发布（需保留版权与许可声明）

## 2. 依赖许可证（requirements.txt 共 12 项）
| 包 | 版本 | 许可证 |
| --- | --- | --- |
| langchain | 0.1.16 | MIT |
| langchain-openai | 0.1.4 | MIT |
| chromadb | 1.5.9 | Apache-2.0 |
| pydantic | 2.11.10 | MIT |
| pandas | 2.3.3 | BSD |
| pyyaml | 6.0.3 | MIT |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| openai | 3.31.0 | Apache-2.0 |
| gradio | <6.0 | Apache-2.0 |
| huggingface-hub | <1.0 | Apache-2.0 |
| transformers | 4.46.3 | Apache-2.0 |
| tokenizers | 0.20.3 | Apache-2.0 |

- copyleft（GPL/AGPL/LGPL）数量：0
- 结论：全部宽松许可证，无传染性条款

## 3. 核心资产隔离
.gitignore 已配置忽略：
- profiles/
- chroma_db/
- raw_json/
- .firecrawl/
- .env
- 另含：__pycache__/ .idea/ venv/

注：当前目录非 git 仓库，`git check-ignore -v` 返回
`fatal: not a git repository (or any of the parent directories): .git`，
忽略规则在 `git init` 后生效。

## 4. GPL 包来源
- 环境内 copyleft 包：fuzzywuzzy / python-Levenshtein / Levenshtein / PyQt6 / PyQt6-Qt6
- Required-by：materials-eunomia（PyQt6 无被依赖方）
- 项目 .py 未 import 上述包；requirements.txt 未列出
- 结论：来自环境邻居，非项目依赖，不进入商业交付物

## 5. Git 状态
- NO_GIT（目录尚未初始化仓库）
- 建议：`git init` 后确认 .gitignore 生效，再提交

## 6. 商业化待办
- [ ] 软件著作权登记（需源码 + 说明书 + 权属声明）
- [ ] 建立私有仓库（GitHub/GitLab private 或自建）
- [ ] 与数据提供方、合作方签署 NDA
- [ ] 第三方数据源（.firecrawl 抓取内容）授权复核
- [ ] 商用前移除/替换含 GPL 的环境包，交付环境重装
