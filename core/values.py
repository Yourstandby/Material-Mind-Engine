"""数值字段结构化：库里的自由文本 → {value, min, max, unit, raw, text}。

卡片展示、极值比较、区间跨度都基于这里的结果。三条硬规则：

1. 单位统一成 ``°C`` / ``h`` / ``%`` / ``MPa``，展示一律写「数值 + 单位」；
2. 只允许温度、时长、百分比三类按**字段**推断单位（裸数字 ``232`` 在 ``test_temp``
   列就是 ``232 °C``），其余字段推断不出来就原文照旧 —— 猜错比不猜更糟；
3. 推断必须过范围校验（温度 -100~400，时长 0~10000），越界的当「单位未标注」。
"""
import re

# 数字：支持负号与小数，不匹配区间分隔符
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")

# 单位识别，按顺序取第一个命中的（°C 必须在 C 之前判，避免被 MPa 之类误吞）
UNIT_ALIAS = (
    (re.compile(r"°\s*[cC]|℃|摄氏度"), "°C"),
    (re.compile(r"°\s*[fF]|℉|华氏"), "°F"),
    (re.compile(r"\b(?:h|hr|hrs|hours?|小时)\b", re.I), "h"),
    (re.compile(r"\bmin(?:s|utes?)?\b", re.I), "min"),
    (re.compile(r"\bpsi\b", re.I), "psi"),
    (re.compile(r"\bmpa\b", re.I), "MPa"),
    (re.compile(r"%|％"), "%"),
)

# 字段 → 可安全推断的单位。密度/硬度/拉伸强度写法太杂，不进这张表。
FIELD_UNIT = {
    "temp_range": "°C",
    "test_temp": "°C",
    "test_duration": "h",
    "cs": "%",
    "chem_resistance": "%",
}

TEMPERATURE_FIELDS = ("temp_range", "test_temp")
RANGE_FIELDS = ("temp_range",)

# 推断安全区间：落不进去就不猜
PLAUSIBLE = {
    "temperature": (-100.0, 400.0),
    "duration": (0.0, 10000.0),
    "percent": (0.0, 400.0),
}


def _kind(field: str) -> str | None:
    if field in TEMPERATURE_FIELDS:
        return "temperature"
    if field == "test_duration":
        return "duration"
    if field in ("cs", "chem_resistance"):
        return "percent"
    return None


def _plausible(value: float, field: str) -> bool:
    kind = _kind(field)
    if not kind:
        return False
    low, high = PLAUSIBLE[kind]
    return low <= value <= high


def _f_to_c(value: float) -> float:
    return round((value - 32) * 5 / 9, 1)


def _psi_to_mpa(value: float) -> float:
    return round(value * 0.006895, 2)


def detect_unit(text: str) -> str | None:
    for pattern, unit in UNIT_ALIAS:
        if pattern.search(text or ""):
            return unit
    return None


def _num_text(value: float) -> str:
    return f"{value:g}"


def _format(low: float, high: float, unit: str | None) -> str:
    body = _num_text(low) if low == high else f"{_num_text(low)} ~ {_num_text(high)}"
    if not unit:
        return body
    # 百分号紧跟数字（18%），物理单位空一格（232 °C），跟排版习惯一致
    return f"{body}%" if unit == "%" else f"{body} {unit}"


def parse(value, field: str = "") -> dict | None:
    """解析一个字段值；空值返回 None。

    返回 ``numeric=False`` 表示没解析出数字（「优 / 良」「室温」这类），
    此时 ``text`` 是原文，调用方照原样展示。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    from core.normalize import localize

    text = localize(raw)
    numbers = [float(match.group(0)) for match in NUMBER.finditer(text)]
    unit = detect_unit(text)
    if unit == "°F":
        numbers = [_f_to_c(number) for number in numbers]
        unit = "°C"
    elif unit == "psi":
        numbers = [_psi_to_mpa(number) for number in numbers]
        unit = "MPa"

    if not numbers:
        return {
            "raw": raw,
            "text": text,
            "unit": None,
            "min": None,
            "max": None,
            "inferred": False,
            "numeric": False,
        }

    low, high = min(numbers), max(numbers)
    inferred = False
    if unit is None and field in FIELD_UNIT and all(_plausible(n, field) for n in numbers):
        unit, inferred = FIELD_UNIT[field], True

    # 该有单位却推不出来：保留原值并标注，卡片上不留裸数字（裸 1017 会被当成任意东西）
    body = _format(low, high, unit) if unit else text
    if unit is None and field in FIELD_UNIT:
        body = f"{body}（单位未标注）"

    return {
        "raw": raw,
        "text": body,
        "unit": unit,
        "min": low,
        "max": high,
        "inferred": inferred,
        "numeric": True,
    }


def display(value, field: str = "") -> str:
    """展示用文本：能结构化的补单位，不能的原样返回。"""
    parsed = parse(value, field)
    return parsed["text"] if parsed else ""


def single_value_range(text, field: str = "temp_range") -> bool:
    """是否是「本该是区间却只有一个数」的错列值。

    入库守卫（置空/降级）和展示层（删重复格）共用这一条判断，避免两处规则漂移。
    """
    parsed = parse(text, field)
    return bool(parsed and parsed.get("numeric") and parsed["min"] == parsed["max"])


def span(parsed: dict | None) -> float | None:
    """区间跨度（max - min），用于「差多少 / 跨度」这类问题。"""
    if not parsed or not parsed.get("numeric"):
        return None
    low, high = parsed.get("min"), parsed.get("max")
    if low is None or high is None or low == high:
        return None
    return round(high - low, 2)


def extreme(value, field: str = "", want_max: bool = True) -> float | None:
    """极值：区间取上界/下界，不再只抓第一个数字。

    原来 ``-20~230°C`` 会被读成 -20，问「最高耐温」直接答错。
    """
    parsed = parse(value, field)
    if not parsed or not parsed.get("numeric"):
        return None
    return parsed["max"] if want_max else parsed["min"]
