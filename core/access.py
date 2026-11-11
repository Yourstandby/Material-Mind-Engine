"""数据分级 + 游客可见范围（读权限分层）。

分级打在「文档」上，存 library/index.json 的 doc["tier"]：
    public        公开
    internal      内部（新入库默认）
    confidential  机密

游客能读哪几级由策略决定，存 library/access.json，管理员在 /admin 勾选；
部署级可用环境变量覆盖（优先于策略文件）：
    MME_GUEST_TIERS=public           游客可读的级别，逗号分隔；all/* 表示不限
    MME_DEFAULT_TIER=internal        新资料默认级别

检索侧不做数据搬运：向量库 where 里带 doc_id 白名单（Chroma 不支持按
元数据反查文档表），index.json 里查不到 doc_id 的旧数据对游客一律拒绝。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import env

TIER_PUBLIC = "public"
TIER_INTERNAL = "internal"
TIER_CONFIDENTIAL = "confidential"
ORDER = (TIER_PUBLIC, TIER_INTERNAL, TIER_CONFIDENTIAL)

TIERS = {
    TIER_PUBLIC: {"id": TIER_PUBLIC, "label": "公开", "level": 0, "hint": "游客与内部账号都可读"},
    TIER_INTERNAL: {"id": TIER_INTERNAL, "label": "内部", "level": 1, "hint": "仅登录账号可读"},
    TIER_CONFIDENTIAL: {"id": TIER_CONFIDENTIAL, "label": "机密", "level": 2, "hint": "仅内部授权，不给游客"},
}

GUEST_ROLE = "guest"
ALL_ROLES = ("guest", "member", "admin")

POLICY_FILE = Path("library/access.json")
# 用于「游客什么都读不到」时构造一个必定为空的 Chroma where
NONE_ID = "__none__"
_TRUTHY = ("1", "true", "yes", "on")


def normalize_tier(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in ORDER else default_tier()


def default_tier() -> str:
    text = str(env("MME_DEFAULT_TIER", TIER_INTERNAL) or "").strip().lower()
    return text if text in ORDER else TIER_INTERNAL


def _normalize_tiers(values) -> list[str]:
    if isinstance(values, str):
        values = values.replace(";", ",").split(",")
    out: list[str] = []
    for item in values or []:
        text = str(item or "").strip().lower()
        if text in ORDER and text not in out:
            out.append(text)
    return out


def _write_policy(data: dict) -> None:
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = POLICY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POLICY_FILE)


def policy() -> dict:
    """游客可读级别 + 新资料默认级别（环境变量 > 策略文件 > 内置默认）。"""
    data: dict = {}
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}

    guest = _normalize_tiers(data.get("guest_tiers"))
    raw_env = str(env("MME_GUEST_TIERS", "") or "").strip()
    if raw_env:
        open_all = raw_env.strip().lower() in ("all", "*", "any")
        guest = list(ORDER) if open_all else _normalize_tiers(raw_env)
    elif "guest_tiers" not in data:
        guest = [TIER_PUBLIC]

    return {
        "guest_tiers": guest,
        "default_tier": _normalize_tiers([data.get("default_tier")])[0]
        if _normalize_tiers([data.get("default_tier")])
        else default_tier(),
        "updated_at": data.get("updated_at") or "",
    }


def save_policy(guest_tiers=None, default_tier_value: str | None = None) -> dict:
    data = {"guest_tiers": policy()["guest_tiers"], "default_tier": policy()["default_tier"]}
    if guest_tiers is not None:
        data["guest_tiers"] = _normalize_tiers(guest_tiers)
    if default_tier_value:
        tier = str(default_tier_value).strip().lower()
        if tier not in ORDER:
            raise ValueError(f"未知分级：{default_tier_value}")
        data["default_tier"] = tier
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_policy(data)
    return policy()


def guest_tiers() -> list[str]:
    return list(policy()["guest_tiers"])


def visible_doc_ids(role: str = GUEST_ROLE):
    """该角色可读的 doc_id 集合；None 表示不限制（登录账号）。"""
    if str(role or "") != GUEST_ROLE:
        return None
    from ingest import repo

    tiers = set(guest_tiers())
    return {
        str(doc.get("doc_id"))
        for doc in repo.all_docs()
        if repo.tier_of(doc) in tiers
    }


def visible_folder_ids(role: str = GUEST_ROLE):
    """游客可见的文件夹（至少有一份可读资料）；None 表示不限制。"""
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
    """管理页用：分级定义 + 当前策略 + 每份资料的分级 + 文件夹。"""
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
                "tier": repo.tier_of(doc),
            }
        )
    current = policy()
    return {
        "tiers": [TIERS[tier] for tier in ORDER],
        "guest_tiers": current["guest_tiers"],
        "default_tier": current["default_tier"],
        "docs": docs,
        "folders": repo.folders(),
    }
