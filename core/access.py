"""数据分级 + 分层读取（游客 / 登录用户 / 管理员）。

每份资料只记一个「可见级别」，含义是该资料**最低可见角色**：

    guest    游客可见：游客、登录用户、管理员都能读
    member   登录用户可见：登录用户与管理员可读，游客不可读
    admin    仅管理员（新投喂资料的默认值）

等级是单向包含的：给游客看的资料天然也覆盖登录用户与管理员，
所以不存在「同一份资料单独对游客开放、对登录用户关闭」这种组合。

分级打在「文档」上，存 library/index.json 的 doc["tier"]。
历史资料没有打标的按 guest（对所有人放开）；投喂时可指定级别，
入库后可在 /admin 逐份或按文件夹调整。

环境变量：
    MME_DEFAULT_VISIBILITY=admin     新投喂资料的默认级别
    MME_GUEST_TIERS / MME_ANONYMOUS_IPS 只决定「游客能否匿名读接口」，见 core/auth.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import env

LEVEL_GUEST = "guest"
LEVEL_MEMBER = "member"
LEVEL_ADMIN = "admin"
ORDER = (LEVEL_GUEST, LEVEL_MEMBER, LEVEL_ADMIN)

# 旧的三级命名（public / internal / confidential）映射到角色级别
LEGACY = {
    "public": LEVEL_GUEST,
    "internal": LEVEL_MEMBER,
    "confidential": LEVEL_ADMIN,
}
# 历史资料没有 tier 字段时按游客可见处理（对所有人放开）
LEGACY_LEVEL = LEVEL_GUEST

LEVELS = {
    LEVEL_GUEST: {
        "id": LEVEL_GUEST,
        "label": "游客可见",
        "rank": 0,
        "hint": "游客、登录用户、管理员都能读",
    },
    LEVEL_MEMBER: {
        "id": LEVEL_MEMBER,
        "label": "登录用户可见",
        "rank": 1,
        "hint": "登录用户与管理员可读，游客不可读",
    },
    LEVEL_ADMIN: {
        "id": LEVEL_ADMIN,
        "label": "仅管理员",
        "rank": 2,
        "hint": "新投喂资料的默认级别，仅管理员可读",
    },
}

ROLE_RANK = {LEVEL_GUEST: 0, LEVEL_MEMBER: 1, LEVEL_ADMIN: 2}
ROLES = (
    {"id": LEVEL_GUEST, "label": "游客"},
    {"id": LEVEL_MEMBER, "label": "登录用户"},
    {"id": LEVEL_ADMIN, "label": "管理员"},
)

POLICY_FILE = Path("library/access.json")
# 用于「一份都读不到」时构造一个必定为空的 Chroma where
NONE_ID = "__none__"


def default_level() -> str:
    text = str(env("MME_DEFAULT_VISIBILITY", LEVEL_ADMIN) or "").strip().lower()
    text = LEGACY.get(text, text)
    return text if text in ORDER else LEVEL_ADMIN


def normalize_level(value) -> str:
    text = str(value or "").strip().lower()
    text = LEGACY.get(text, text)
    return text if text in ORDER else default_level()


def level_of_doc(doc: dict) -> str:
    """资料实际生效的可见级别；缺字段的历史资料按「游客可见」。"""
    raw = (doc or {}).get("tier")
    if not raw:
        return LEGACY_LEVEL
    return normalize_level(raw)


def role_rank(role) -> int:
    return ROLE_RANK.get(str(role or "").strip().lower(), ROLE_RANK[LEVEL_GUEST])


def can_read(role: str, level: str) -> bool:
    """角色是否够格读该级别：guest0 / member1 / admin2，级别越低越开放。"""
    return role_rank(role) >= LEVELS[normalize_level(level)]["rank"]


def _write_policy(data: dict) -> None:
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = POLICY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POLICY_FILE)


def policy() -> dict:
    """新投喂资料的默认级别（环境变量 > 策略文件 > 内置 admin）。"""
    data: dict = {}
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    stored = normalize_level(data.get("default_level")) if data.get("default_level") else ""
    return {
        "default_level": default_level() if env("MME_DEFAULT_VISIBILITY", "") else (stored or default_level()),
        "updated_at": data.get("updated_at") or "",
    }


def save_policy(default_level_value: str | None = None) -> dict:
    data = {"default_level": policy()["default_level"]}
    if default_level_value:
        text = str(default_level_value).strip().lower()
        text = LEGACY.get(text, text)
        if text not in ORDER:
            raise ValueError(f"未知级别：{default_level_value}")
        data["default_level"] = text
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_policy(data)
    return policy()


def visible_doc_ids(role: str = LEVEL_GUEST):
    """该角色可读的 doc_id 集合；None 表示不限制（管理员）。"""
    role = str(role or "").strip().lower()
    if role == LEVEL_ADMIN:
        return None
    from ingest import repo

    return {
        str(doc.get("doc_id"))
        for doc in repo.all_docs()
        if can_read(role, level_of_doc(doc))
    }


def visible_folder_ids(role: str = LEVEL_GUEST):
    """该角色能看到的文件夹（至少有一份可读资料）；None 表示不限制。"""
    allowed = visible_doc_ids(role)
    if allowed is None:
        return None
    from ingest import repo

    return {
        str(doc.get("folder_id") or repo.UNFILED)
        for doc in repo.all_docs()
        if str(doc.get("doc_id")) in allowed
    }


def can_read_doc(role: str, doc: dict) -> bool:
    allowed = visible_doc_ids(role)
    if allowed is None:
        return True
    return str((doc or {}).get("doc_id") or "") in allowed


def where(allowed) -> dict | None:
    """doc 白名单 → Chroma where 片段；None 表示不加限制。"""
    if allowed is None:
        return None
    ids = sorted(str(x) for x in allowed)
    return {"doc_id": {"$in": ids or [NONE_ID]}}


def merge_where(base: dict | None, extra: dict | None) -> dict | None:
    if not base:
        return extra
    if not extra:
        return base
    return {"$and": [base, extra]}


def meta_allowed(meta: dict, allowed) -> bool:
    """chunk 元数据是否在允许范围（allowed=None 表示不限制）。"""
    return allowed is None or str((meta or {}).get("doc_id") or "") in allowed


def overview() -> dict:
    """管理页用：级别定义 + 默认级别 + 每份资料的级别 + 文件夹。"""
    from ingest import repo

    docs = []
    for doc in repo.all_docs():
        docs.append(
            {
                "doc_id": doc.get("doc_id"),
                "title": doc.get("title") or "",
                "name": doc.get("original_name") or doc.get("canonical_name") or "",
                "collection": doc.get("collection") or "",
                "folder_id": doc.get("folder_id") or repo.UNFILED,
                "records": doc.get("records") or 0,
                "level": level_of_doc(doc),
            }
        )
    current = policy()
    return {
        "levels": [LEVELS[key] for key in ORDER],
        "roles": list(ROLES),
        "default_level": current["default_level"],
        "docs": docs,
        "folders": repo.folders(),
    }
