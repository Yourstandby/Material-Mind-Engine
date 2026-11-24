"""卡片视图配置：字段顺序 / 显隐 / 中文名、每产品记录数、卡片列数、元素开关。

改完存 ``data/card_view.json``，下一次请求就生效——不用改代码、不用重启、不用重建索引。
配置只有一份（全局）；以后要做成按用户/按角色，把跟路径那层换成 user_id 就行。
"""
from __future__ import annotations

import json
from pathlib import Path

from core.config import env

LIMITS = {"records_per_product": (1, 8), "columns": (1, 4)}
SHOW_KEYS = ("conclusion", "refs", "shared", "note", "missing", "footer")
DEFAULT_SHOW = {key: True for key in SHOW_KEYS}

# 文件很小，但每次问答都会读：用 mtime 做一级缓存，改配置立刻生效
_CACHE: tuple[float, dict] = (0.0, {})


def path() -> Path:
    return Path(env("CARD_VIEW_PATH", "./data/card_view.json"))


def field_catalog() -> list[dict]:
    from core.query_engine import FIELDS

    return [{"key": key, "label": label} for key, label in FIELDS]


def defaults() -> dict:
    return {
        "fields": [{**field, "visible": True} for field in field_catalog()],
        "records_per_product": 3,
        "columns": 3,
        "show": dict(DEFAULT_SHOW),
    }


def options() -> dict:
    """给前端面板用的边界信息，避免前后端各写一份 1~8 / 1~4。"""
    return {"limits": {key: list(value) for key, value in LIMITS.items()}, "show": list(SHOW_KEYS)}


def _merge(base: dict, raw) -> dict:
    if not isinstance(raw, dict):
        return base
    out = dict(base)

    # 字段表：按配置顺序排；不认识的 key 丢掉（防止历史配置引用已删除字段），
    # 配置里没有的新字段自动追加到末尾——新增字段永远能显示出来。
    known = {field["key"]: field for field in base["fields"]}
    ordered, seen = [], set()
    for item in raw.get("fields") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key not in known or key in seen:
            continue
        seen.add(key)
        label = str(item.get("label") or "").strip()[:24] or known[key]["label"]
        ordered.append({"key": key, "label": label, "visible": bool(item.get("visible", True))})
    for field in base["fields"]:
        if field["key"] not in seen:
            ordered.append({**field, "visible": True})
    out["fields"] = ordered

    for key, (low, high) in LIMITS.items():
        try:
            value = int(raw.get(key))
        except (TypeError, ValueError):
            value = base[key]
        out[key] = max(low, min(high, value))

    show = dict(base["show"])
    raw_show = raw.get("show")
    if isinstance(raw_show, dict):
        for key in SHOW_KEYS:
            if key in raw_show:
                show[key] = bool(raw_show[key])
    out["show"] = show
    return out


def load() -> dict:
    global _CACHE
    target = path()
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return defaults()
    if _CACHE[0] == mtime and _CACHE[1]:
        return _CACHE[1]
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults()
    view = _merge(defaults(), raw)
    _CACHE = (mtime, view)
    return view


def save(patch: dict) -> dict:
    global _CACHE
    view = _merge(load(), patch or {})
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE = (0.0, {})  # 让下次 load 重新读盘
    return view


def reset() -> dict:
    global _CACHE
    try:
        path().unlink()
    except OSError:
        pass
    _CACHE = (0.0, {})
    return load()


# ---- 查询层用的几个取值器 ----


def visible_keys(view: dict) -> list[str]:
    return [field["key"] for field in view.get("fields") or [] if field.get("visible")]


def labels(view: dict) -> dict:
    return {field["key"]: field["label"] for field in view.get("fields") or []}


def order(view: dict) -> dict:
    return {field["key"]: index for index, field in enumerate(view.get("fields") or [])}


def records_limit(view: dict) -> int:
    low, high = LIMITS["records_per_product"]
    try:
        return max(low, min(high, int(view.get("records_per_product"))))
    except (TypeError, ValueError):
        return high
