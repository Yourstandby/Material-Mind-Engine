"""分块 -> LLM 抽取。

抽两类东西：
    records   长表记录（字段 | 数值 | 条件），可筛选、可对比、可出报表；
    passages  原文片段，保存叙述性内容（应用说明、选型建议、工艺、失效机理），
              整段保留原话，不做字段化，交给向量检索回答「为什么 / 用在哪」这类问题。

为什么不一概结构化：叙述性内容硬塞进固定字段会失真（一整段选型建议压成 application 一个短句），
且库里没有地方存「原文到底怎么说的」。判断标准是「能不能定位到具体数值 + 字段名」，
定位不到就进 passages，宁可不成表也不要错表。
"""

import json
import re

from core.llm import chat
from core.normalize import normalize_record

from ingest import guard

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

PASSAGE_FIELDS = ("text", "kind", "confidence")
PASSAGE_KINDS = ("说明", "应用", "工艺", "规格", "其他")

PROMPT = """你是材料数据抽取器。输入资料片段可能是普通段落，也可能是 Markdown 表格。

只输出一个 JSON 对象，不要解释、不要 Markdown 代码块：
{{"records": [...], "passages": [...]}}

records 每条是一行结构化数据，字段：
grade 牌号 / supplier 供应商 / composition 组成 / material 材质 / temp_range 温度范围 /
cs 压缩永久变形 / chem_resistance 耐化学性 / test_medium 测试介质 /
test_temp 测试温度 / test_duration 测试时长 / source 来源标记 / confidence 0~1 /
application 应用场景 / industry 行业 / part 部件 / medium 接触介质 / certifications 认证

passages 每条是原文片段：{{"text": "原话", "kind": "说明|应用|工艺|规格|其他", "confidence": 0~1}}

【记录还是片段】
A. 进 records：能从片段里定位到具体数值，或明确的分类值（牌号/材质/供应商/认证），
   并且写得出它的字段名。一条记录只放一个测试值和它自己的测试条件。
B. 进 passages：叙述性内容——应用说明、选型建议、失效机理、加工工艺、认证背景、规格表注释。
   整段保留原话（可去掉页眉页脚、页码），不要改写成字段、不要与别的段落合并、不要总结。
C. 判断不了就进 passages。宁可不成表，也不要硬塞进 records。

【表格片段】
输入若是 Markdown 表格（| 列 | 列 |），表头是列名，分隔行 | --- | 忽略。
同一行的数值必须来自同一行，跨行拼接即视为错误。列对不上就丢弃该条。

【规则】
1. 只抽取片段中明确出现的数值，禁止推断、禁止换算、禁止补全
2. 缺失字段填 null
3. source 原样填入给定的来源标记
4. 片段里既没有性能数值也没有叙述性内容时输出 {{"records": [], "passages": []}}
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
11. temp_range 必须是**上下界两个数**（如 "-20~230°C"）。片段里只有一个温度时，
   写进 test_temp，temp_range 填 null；把单值填进 temp_range 会被判为错列。
12. test_duration 统一写小时（如 "70 h"）；原文是分钟就保留 "70 min"，不要自己换算。
10. 片段是日语或英语时，records 的字段值用中文（牌号、化学式保留原文）；passages.text 保留原话。

页面表头：{head}
来源标记：{source}
资料片段：
{text}"""


def _strip_fence(raw: str) -> str:
    text = (raw or "").strip()
    return re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()


def _load_object(raw: str) -> dict:
    """解析模型输出；兼容「只返回数组」的旧行为（当成 records）。"""
    text = _strip_fence(raw)

    for candidate in (text, _first_json(text)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"records": data, "passages": []}
    return {}


def _first_json(text: str) -> str:
    """从夹杂说明文字的输出里截出第一个 JSON 对象或数组。"""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return text[start : end + 1]
    return ""


def _confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def _records(items) -> list[dict]:
    records = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        record = {key: item.get(key) for key in FIELDS}
        record["confidence"] = _confidence(record.get("confidence"))
        record = normalize_record(record)
        # 字段级守卫：越界置空、单值范围降级、时长单位换算，问题随记录带到入库层
        record, issues = guard.audit(record)
        if issues:
            record["_issues"] = issues
        records.append(record)
    return records


def _passages(items, source: str) -> list[dict]:
    out = []
    for item in items or []:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if len(text) < 8:  # 太短的多是页眉页脚或残句
            continue
        kind = str(item.get("kind") or "").strip()
        out.append(
            {
                "text": text,
                "kind": kind if kind in PASSAGE_KINDS else "其他",
                "source": str(item.get("source") or source),
                "confidence": _confidence(item.get("confidence")),
            }
        )
    return out


def extract_all(text: str, source: str, head: str = "") -> tuple[list[dict], list[dict]]:
    """返回（结构化记录，原文片段）。"""
    raw = chat(PROMPT.format(head=head or "—", source=source, text=text[:6000]))
    payload = _load_object(raw)
    records = _records(payload.get("records"))
    for record in records:
        record["source"] = record.get("source") or source
    return records, _passages(payload.get("passages"), source)


def extract(text: str, source: str, head: str = "") -> list[dict]:
    """向后兼容：只要记录。"""
    records, _passages_out = extract_all(text, source, head)
    return records
