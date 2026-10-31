"""分块 -> LLM 结构化抽取（JSON 数组）。"""

import json
import re

from core.llm import chat
from core.normalize import normalize_record

FIELDS = (
    "grade",
    "supplier",
    "composition",
    "material",
    "application",
    "industry",
    "part",
    "medium",
    "certifications",
    "temp_range",
    "cs",
    "chem_resistance",
    "test_medium",
    "test_temp",
    "test_duration",
    "source",
    "confidence",
)

PROMPT = """你是材料数据抽取器。从资料片段中抽取材料性能记录。

只输出 JSON 数组，不要输出解释、不要 Markdown 代码块。
每条记录字段：
grade 牌号 / supplier 供应商 / composition 组成 / material 材质 / temp_range 温度范围 /
cs 压缩永久变形 / chem_resistance 耐化学性 / test_medium 测试介质 /
test_temp 测试温度 / test_duration 测试时长 / source 来源标记 / confidence 0~1 /
application 应用场景 / industry 行业 / part 部件 / medium 接触介质 / certifications 认证

规则：
1. 只抽取片段中明确出现的数值，禁止推断、禁止补全
2. 缺失字段填 null
3. source 原样填入给定的来源标记
4. 片段中没有任何材料性能数据时输出 []
5. supplier：片段里出现的品牌或公司名（如 Tecnoflon、Solvay、索尔维、Daikin、Chemours），没有则 null
6. material：该表所属的材质类别，只能是 FKM / FFKM / PEEK / PTFE / VMQ / EPDM / NBR / PU 之一。
   依据片段标题或表头判断（出现 PFR、全氟橡胶、全氟醚、FFKM 记为 FFKM；只写氟橡胶记 FKM），判断不了填 null。
   material 和 supplier 属于分类信息，不算「推断数值」，允许根据片段中的文字线索填写。
7. 应用类字段同样是分类信息，可按片段中的设备、工艺、介质线索填写，但禁止编造客户名或具体工况数值：
   application 用简短中文描述用途（如「半导体刻蚀设备密封」）；
   industry 只能是 半导体 / 化工 / 汽车 / 食品饮料 / 医疗 / 能源电力 / 航空航天 / 通用工业 之一；
   part 只能是 O形圈 / 密封件 / 阀门 / 管/软管 / 隔膜 / 轴承/衬套 / 涂层/衬里 / 法兰 之一；
   medium 写接触介质（如 酸 / 碱 / 溶剂 / 油 / 燃料 / 蒸汽 / 水 / 气体 / 等离子 / 制冷剂）；
   certifications 写认证或合规（如 FDA / USP Class VI / ISO 10993 / REACH / RoHS / UL / NSF），多个用 / 分隔。
   判断不了填 null。
8. 片段经常是表格的一部分：如果「页面表头」里有牌号列表，而数据行里没有重复牌号，
   按列顺序把数值对应到牌号；对不上就填 null，不要猜。
9. 同一牌号在一个片段里有多个测试（如压缩形变、耐化学性/介质浸泡）时，拆成多条记录：
   一条记录只放一个测试数值和它自己的测试条件，不要把 A 测试的温度介质写到 B 测试上。

页面表头：{head}
来源标记：{source}
资料片段：
{text}"""


def _load_array(raw: str) -> list:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def extract(text: str, source: str, head: str = "") -> list[dict]:
    raw = chat(PROMPT.format(head=head or "—", source=source, text=text[:6000]))
    records = []
    for item in _load_array(raw):
        if not isinstance(item, dict):
            continue
        record = {key: item.get(key) for key in FIELDS}
        record["source"] = record.get("source") or source
        record["confidence"] = _confidence(record.get("confidence"))
        records.append(normalize_record(record))
    return records
