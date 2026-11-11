# 资料可见级别（游客 / 登录用户 / 管理员）

> 一句话：每份资料一个「最低可见角色」，级别越低越开放；给游客看的资料天然也覆盖登录用户和管理员。
> 代码：`core/access.py`（级别与判定）、`ingest/repo.py`（落库）、`server/main.py`（按角色过滤）、`/admin`（管理）。

---

## 1. 级别

| 级别 | 标识 | 游客 | 登录用户 | 管理员 |
| --- | --- | --- | --- | --- |
| 游客可见 | `guest` | ✓ | ✓ | ✓ |
| 登录用户可见 | `member` | ✗ | ✓ | ✓ |
| 仅管理员 | `admin` | ✗ | ✗ | ✓ |

- 新投喂资料默认 `admin`（上传面板可改）。
- 历史资料没有 `tier` 字段 → 按 `guest`（对所有人放开）；重新投喂同一份文件不会把它收紧回 `admin`。
- 旧命名 `public` / `internal` / `confidential` 读入时自动映射为 `guest` / `member` / `admin`。

## 2. 投喂时选级别

上传面板「可见级别」下拉，默认「仅管理员可见」：
- `admin`（默认）：只有管理员能问到、能看到来源
- `member`：登录用户 + 管理员
- `guest`：所有人（对外演示、公开资料）

对应接口：`POST /api/ingest` 多一个表单字段 `level`，空值走默认级别。

## 3. 已入库资料改级别

管理页 `/admin` → 「资料可见级别」：
- 顶部「新投喂默认」切换默认级别（存在 `library/access.json`）
- 每行资料的下拉逐份改
- 「按文件夹批量」一次改整个文件夹

对应接口（均需管理员）：
```
GET   /api/access                     # 级别定义 + 默认级别 + 每份资料级别
PATCH /api/access/policy              # {"default_level": "member"}
PATCH /api/library/{doc_id}/level     # {"level": "guest"}
POST  /api/access/folder              # {"folder_id": "fkm", "level": "member"}
```

## 4. 部署级配置

```env
MME_DEFAULT_VISIBILITY=admin     # 新资料默认级别（覆盖策略文件）
MME_ALLOW_ANONYMOUS=1            # 允许游客匿名读只读接口
MME_ANONYMOUS_IPS=172.16.1.121   # 或只对指定来源放开
```

只读三件套才可能匿名：`/api/chat`、`/api/search`、`/api/bootstrap`；上传、删除、`/api/auth/*`、资料库写操作、`/api/access` 永远要登录（管理员）。

## 5. 过滤怎么生效

- 角色 → 可读 doc_id 集合：管理员 `None`（不过滤）、登录用户 = guest+member 文档、游客 = guest 文档
- 检索：Chroma `where` 带 `doc_id` 白名单，结果再按 `doc_id` 二次校验；空白名单用哨兵 `__none__`，不会退化成「不过滤」
- 产品清单、应用分布、文件夹、来源列表、产品详情都按同一白名单聚合
- `doc_id` 不在 `library/index.json` 里的历史孤儿 chunk，对非管理员一律拒绝（默认拒绝）
