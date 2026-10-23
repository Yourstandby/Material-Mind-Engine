"""实体归一化：牌号、供应商、单位。入库前跑一次，查询时用同一套规则。"""

import re

GRADE_PREFIX = re.compile(r"^X(?=\d)")

SUPPLIER_ALIAS = {
    "daikin": "Daikin Industries",
    "大金": "Daikin Industries",
    "dyneon": "3M Dyneon",
    "solvay": "Syensqo",
    "syensqo": "Syensqo",
    "索尔维": "Syensqo",
    "苏威": "Syensqo",
    "tecnoflon": "Syensqo",
    "chemours": "Chemours",
    "科慕": "Chemours",
    "solvay speciality": "Syensqo",
    "greene tweed": "Greene Tweed",
    "parker": "Parker Hannifin",
    "victrex": "Victrex",
    "ensinger": "Ensinger",
    "mitsuichem": "Mitsui Chemicals",
    "三井": "Mitsui Chemicals",
}

UNIT_FIELDS = ("temp_range", "test_temp", "test_duration", "cs", "chem_resistance", "pressure")


def grade_key(value) -> str:
    """检索/去重用的牌号键：如 X6055B -> 6055B。"""
    if not value:
        return ""
    text = re.sub(r"\s+", "", str(value)).upper()
    return GRADE_PREFIX.sub("", text)


def supplier(value) -> str:
    if not value:
        return ""
    raw = str(value).strip()
    low = raw.lower()
    for alias, canonical in SUPPLIER_ALIAS.items():
        if alias in low:
            return canonical
    return raw


def _f_to_c(match: re.Match) -> str:
    return f"{round((float(match.group(1)) - 32) * 5 / 9, 1)}°C"


def units(text):
    """保守转换：只处理明确的数值 + 单位，其余原样保留。"""
    if not text:
        return text
    out = str(text)
    out = re.sub(r"(-?\d+(?:\.\d+)?)\s*°?\s*F\b", _f_to_c, out)
    out = re.sub(r"(-?\d+(?:\.\d+)?)\s*°?\s*C\b", r"\1°C", out, flags=re.I)
    out = re.sub(
        r"(\d+(?:\.\d+)?)\s*psi\b",
        lambda m: f"{round(float(m.group(1)) * 0.006895, 2)} MPa",
        out,
        flags=re.I,
    )
    out = re.sub(r"(\d+(?:\.\d+)?)\s*(?:hr|hrs|hours?)\b", r"\1 h", out, flags=re.I)
    return out


# 日语资料里的常见词，入库和展示时统一成中文（牌号、型号保持原文）
JA_TERMS = {
    "圧縮永久ひずみ": "压缩永久变形",
    "耐薬品性": "耐化学性",
    "引張強さ": "拉伸强度",
    "引張り強さ": "拉伸强度",
    "伸び": "伸长率",
    "硬さ": "硬度",
    "耐熱性": "耐热性",
    "比重": "比重",
    "ブレーキフルード": "制动液",
    "ブレーキ油": "制动液",
    "潤滑油": "润滑油",
    "鉱物油": "矿物油",
    "動植物油": "动植物油",
    "燃料油": "燃料油",
    "ガソリン": "汽油",
    "軽油": "柴油",
    "重油": "重油",
    "グリース": "润滑脂",
    "ジクロロメタン": "二氯甲烷",
    "メチルエチルケトン": "甲乙酮",
    "メチルtertブチルエーテル": "甲基叔丁基醚",
    "テトラヒドロフラン": "四氢呋喃",
    "ジブチルエーテル": "二丁醚",
    "醋酸ブチル": "乙酸丁酯",
    "酢酸": "乙酸",
    "アセトン": "丙酮",
    "キシレン": "二甲苯",
    "トルエン": "甲苯",
    "エタノール": "乙醇",
    "メタノール": "甲醇",
    "アンモニア": "氨",
    "水酸化ナトリウム": "氢氧化钠",
    "水酸化カリウム": "氢氧化钾",
    "ふっ酸": "氢氟酸",
    "フッ酸": "氢氟酸",
    "熱リン酸": "热磷酸",
    "硫酸": "硫酸",
    "硝酸": "硝酸",
    "塩酸": "盐酸",
    "蒸気": "蒸汽",
    "空気": "空气",
    "海水": "海水",
    "冷媒": "制冷剂",
    "不凍液": "防冻液",
    "酸素": "氧气",
    "窒素": "氮气",
    "水素": "氢气",
    "二酸化炭素": "二氧化碳",
    "メタン": "甲烷",
    "プロパン": "丙烷",
    "オゾン": "臭氧",
}

_KANA = re.compile(r"[\u3040-\u30ff]")


def japanese(text):
    """日语词/单位换成中文或通用符号。"""
    if not text:
        return text
    out = str(text)
    for word, zh in sorted(JA_TERMS.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(word, zh)
    out = out.replace("℃", "°C").replace("％", "%").replace("・", "、")
    out = re.sub(r"(\d+(?:\.\d+)?)\s*時間", r"\1 h", out)
    out = re.sub(r"(\d+(?:\.\d+)?)\s*分\b", r"\1 min", out)
    out = re.sub(r"(\d+(?:\.\d+)?)\s*秒", r"\1 s", out)
    return out


def localize(text):
    """展示用：先做单位换算，再把残留的日语词转成中文。"""
    return japanese(units(text))


def normalize_record(record: dict) -> dict:
    out = dict(record)
    out["grade_raw"] = str(out.get("grade") or "")
    out["grade_key"] = grade_key(out.get("grade"))
    if out.get("supplier"):
        out["supplier"] = supplier(out["supplier"])
    for field in UNIT_FIELDS:
        if out.get(field):
            out[field] = units(out[field])
    return out
