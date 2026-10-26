"""生成 MME 学术风结构图（SVG）。

用法: python scripts/make_arch_figure.py
输出: docs/figures/mme-architecture.svg
"""

from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1440, 1000
OUT = Path(__file__).resolve().parent.parent / "docs" / "figures" / "mme-architecture.svg"

INK = "#1F1D1A"
INK2 = "#45423C"
MUTED = "#6E6A62"
LINE = "#C8C3B8"
RULE = "#E2DED4"
FILL = "#FBFAF7"
FILL_SOFT = "#F4F1EA"
ACCENT = "#A63D1E"

SANS = "'Microsoft YaHei','PingFang SC','Source Han Sans SC','Noto Sans CJK SC',sans-serif"
MONO = "Consolas,'SF Mono','JetBrains Mono',monospace"

out: list[str] = []


def text(x, y, s, size=12, color=INK2, weight=400, family=SANS, anchor="start", spacing=None):
    attrs = [
        f'x="{x}"',
        f'y="{y}"',
        f'font-size="{size}"',
        f'fill="{color}"',
    ]
    if weight != 400:
        attrs.append(f'font-weight="{weight}"')
    if family != SANS:
        attrs.append(f'font-family="{family}"')
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if spacing:
        attrs.append(f'letter-spacing="{spacing}"')
    out.append(f'<text {" ".join(attrs)}>{escape(s)}</text>')


def rect(x, y, w, h, fill=FILL, stroke=LINE, rx=3, dash=None, sw=1.1):
    attrs = [
        f'x="{x}"', f'y="{y}"', f'width="{w}"', f'height="{h}"',
        f'fill="{fill}"', f'stroke="{stroke}"', f'stroke-width="{sw}"', f'rx="{rx}"',
    ]
    if dash:
        attrs.append(f'stroke-dasharray="{dash}"')
    out.append(f'<rect {" ".join(attrs)}/>')


def line(x1, y1, x2, y2, color=LINE, dash=None, sw=1.1):
    attrs = [f'x1="{x1}"', f'y1="{y1}"', f'x2="{x2}"', f'y2="{y2}"',
             f'stroke="{color}"', f'stroke-width="{sw}"']
    if dash:
        attrs.append(f'stroke-dasharray="{dash}"')
    out.append(f'<line {" ".join(attrs)}/>')


def arrow(x1, y1, x2, y2, color=INK2, dash=None):
    attrs = [f'x1="{x1}"', f'y1="{y1}"', f'x2="{x2}"', f'y2="{y2}"',
             f'stroke="{color}"', 'stroke-width="1.2"', 'marker-end="url(#head)"']
    if dash:
        attrs.append(f'stroke-dasharray="{dash}"')
    out.append(f'<line {" ".join(attrs)}/>')


def poly(points, color=INK2, dash=None):
    d = " ".join(f"{x},{y}" for x, y in points)
    attrs = [f'points="{d}"', 'fill="none"', f'stroke="{color}"', 'stroke-width="1.2"',
             'marker-end="url(#head)"']
    if dash:
        attrs.append(f'stroke-dasharray="{dash}"')
    out.append(f'<polyline {" ".join(attrs)}/>')


def step(x, y, w, h, num, title, lines):
    rect(x, y, w, h)
    text(x + w - 14, y + 21, num, 9.5, ACCENT, 400, MONO, "end", ".08em")
    text(x + 16, y + 29, title, 15, INK, 600)
    for i, item in enumerate(lines):
        text(x + 16, y + 52 + i * 17, item, 11.5, MUTED)


def chip(x, y, w, label):
    rect(x, y, w, 26, fill=FILL_SOFT, stroke="none", rx=4, sw=0)
    text(x + w / 2, y + 18, label, 10.5, INK2, 400, SANS, "middle")


# ---------------------------------------------------------------- 画布
out.append(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
    f'font-family="{SANS}">'
)
out.append("<defs>")
out.append(
    '<marker id="head" viewBox="0 0 7 7" refX="6.4" refY="3.5" markerWidth="7" markerHeight="7" '
    f'orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill="{INK2}"/></marker>'
)
out.append("</defs>")
out.append(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

# ---------------------------------------------------------------- 页眉
text(64, 60, "FIGURE 1 · SYSTEM ARCHITECTURE", 10, MUTED, 400, MONO, spacing=".16em")
text(64, 102, "Material Mind Engine：从资料到可溯源回答", 27, INK, 700)
text(64, 130, "双流水线：离线入库 × 在线检索生成 · 全本地部署（2×L20）· 中 / 英 / 日资料统一处理", 13, MUTED)

# 图例
lx = 1150
arrow(lx, 56, lx + 40, 56)
text(lx + 50, 60, "数据流", 11, MUTED)
rect(lx, 78, 24, 12, fill="none", stroke=LINE, rx=2, dash="4 3", sw=1)
text(lx + 50, 89, "本地部署边界", 11, MUTED)
text(lx + 2, 118, "01", 10, ACCENT, 400, MONO)
text(lx + 30, 118, "处理阶段", 11, MUTED)

# ---------------------------------------------------------------- 本地边界
rect(36, 152, 1368, 790, fill="none", stroke="#B9B4A8", rx=10, dash="6 5", sw=1.1)
text(58, 174, "LOCAL BOUNDARY · 127.0.0.1 · 单机 2×L20", 9.5, MUTED, 400, MONO, spacing=".12em")

# ---------------------------------------------------------------- ① 入库
AX, AW = 64, 352
text(AX, 200, "01 / INGESTION", 9.5, MUTED, 400, MONO, spacing=".14em")
text(AX, 220, "①", 16, ACCENT, 700)
text(AX + 24, 220, "资料入库", 15, INK, 600)

step(AX, 234, AW, 78, "01", "多格式资料", ["PDF / DOCX / XLSX / HTML / MD / CSV", "Web 上传、inbox 目录或 CLI 投放"])
step(AX, 325, AW, 78, "02", "解析与 OCR", ["文本层优先；扫描件与缺字页交给 OCR", "PP-OCRv5 · 中 / 英 / 日"])
step(AX, 416, AW, 78, "03", "语义分块", ["800 字滑窗，表头随块继承", "跨块表格仍能对齐列名"])
step(AX, 507, AW, 78, "04", "结构化抽取", ["LLM 抽取 11 个字段", "同表多次测试拆成多条记录"])
step(AX, 598, AW, 78, "05", "归一与去重", ["牌号 / 供应商 / 单位统一", "内容哈希去重，幂等写入"])
step(AX, 689, AW, 78, "06", "向量化入库", ["bge-m3 → 1024 维向量", "写入 Chroma 的 catalog 集合"])

for y in (312, 403, 494, 585, 676):
    arrow(240, y, 240, y + 11)

# 入库 → 知识库
poly([(416, 728), (436, 728), (436, 320), (452, 320)])

# ---------------------------------------------------------------- ② 知识核心
BX, BW = 452, 532
text(BX, 200, "02 / RETRIEVAL & CONTEXT", 9.5, MUTED, 400, MONO, spacing=".14em")
text(BX, 220, "②", 16, ACCENT, 700)
text(BX + 24, 220, "知识核心", 15, INK, 600)

# 向量库
rect(BX, 234, BW, 196)
text(BX + 16, 262, "Chroma 向量库 · catalog", 16, INK, 600)
chip(BX + 16, 272, 117, "251 条记录")
chip(BX + 143, 272, 117, "1024 维")
chip(BX + 270, 272, 117, "4 类材质")
chip(BX + 397, 272, 117, "2 家厂商")
text(BX + 16, 324, "元数据：材质 · 厂商 · 牌号 · 测试条件 · 来源页", 11.5, MUTED)

rect(BX + 16, 338, BW - 32, 74, fill=FILL_SOFT, stroke="none", rx=4, sw=0)
text(BX + 30, 356, "记录示例", 9, MUTED, 400, MONO, spacing=".1em")
text(BX + 30, 378, "N 535 NM · 氟橡胶 · Syensqo", 12.5, INK, 600)
text(BX + 30, 398, "压缩永久变形 14% · 200 °C · 70 h · 来源 p.5", 10.5, MUTED)

# 检索器
rect(BX, 450, BW, 112)
text(BX + 16, 478, "语义检索 Retrieve", 15, INK, 600)
text(BX + 16, 500, "· top-k：默认 5，清单类问题放到 12", 11.5, MUTED)
text(BX + 16, 523, "· 材质（FKM / FFKM / PEEK）与厂商双过滤", 11.5, MUTED)
text(BX + 16, 546, "· 温度类问题自动扩写检索词", 11.5, MUTED)

# 上下文组装
rect(BX, 582, BW, 112)
text(BX + 16, 610, "上下文组装 Format", 15, INK, 600)
text(BX + 16, 632, "· 中文字段标签；日语词转中文；温度补 °C", 11.5, MUTED)
text(BX + 16, 655, "· 每条证据独立成行，条件不跨记录拼接", 11.5, MUTED)
text(BX + 16, 678, "· 空字段省略，单条证据不套表格", 11.5, MUTED)

# ---------------------------------------------------------------- ③ 问答
CX, CW = 1020, 372
text(CX, 200, "03 / GENERATION", 9.5, MUTED, 400, MONO, spacing=".14em")
text(CX, 220, "③", 16, ACCENT, 700)
text(CX + 24, 220, "在线问答", 15, INK, 600)

rect(CX, 234, CW, 72)
text(CX + 16, 262, "用户提问", 15, INK, 600)
text(CX + 16, 284, "Web 对话或 API；中文 / 英文 / 日文", 11.5, MUTED)

rect(CX, 319, CW, 92)
text(CX + 16, 347, "问题解析", 15, INK, 600)
text(CX + 16, 369, "识别材质、厂商与牌号", 11.5, MUTED)
text(CX + 16, 389, "点名厂商时只在该厂商内检索", 11.5, MUTED)

rect(CX, 424, CW, 168)
text(CX + 16, 452, "检索证据（n 条记录）", 15, INK, 600)
rect(CX + 16, 464, CW - 32, 116, fill=FILL_SOFT, stroke="none", rx=4, sw=0)
text(CX + 30, 488, "N 535 NM · 氟橡胶 · Syensqo", 12.5, INK, 600)
text(CX + 30, 510, "压缩永久变形 14% · 200 °C · 70 h", 10.5, INK2)
text(CX + 30, 530, "来源：Tecnoflon 手册 p.5", 10, MUTED)
text(CX + 30, 556, "其余证据按同样结构排列", 10, MUTED)

rect(CX, 605, CW, 82)
text(CX + 16, 633, "答案生成", 15, INK, 600)
text(CX + 16, 655, "qwen2.5-72B，只依据检索证据", 11.5, MUTED)
text(CX + 16, 675, "冲突分别列出；缺数据直说，不猜", 11.5, MUTED)

rect(CX, 700, CW, 78)
text(CX + 16, 728, "回答与溯源", 15, INK, 600)
text(CX + 16, 750, "证据表格 + 引用 [n] + 来源卡片", 11.5, MUTED)
text(CX + 16, 770, "文档 / 页码可点开核对", 11.5, MUTED)

# 问答内部箭头
for y in (306, 411, 592, 687):
    arrow(1206, y, 1206, y + 11)

# 提问 → 检索 → 证据
poly([(CX, 374), (1002, 374), (1002, 472), (984, 472)])
arrow(984, 538, CX, 538)

# ---------------------------------------------------------------- 模型服务
line(64, 786, 1392, 786, color=RULE, sw=1)

rect(64, 794, 1328, 112, fill=FILL_SOFT, stroke="none", rx=6, sw=0)
text(80, 816, "模型服务 · MODEL SERVING", 10, MUTED, 400, MONO, spacing=".12em")
text(1376, 816, "统一经网关 8100 调用 · API Key · 每 Key 2 并发 · 超限 429", 10, MUTED, 400, SANS, "end")

models = [
    ("qwen2.5-72B Instruct（Q4）", "结构化抽取 · 答案生成", "llama.cpp · 4 并发 · 131k 上下文"),
    ("bge-m3", "记录与问题向量化", "1024 维 · CLS pooling"),
    ("PP-OCRv5", "扫描件 / 缺字页识别", "中 · 英 · 日 · CPU"),
]
mx = 80
for name, role, note in models:
    rect(mx, 826, 420, 68, fill="#FFFFFF", stroke=LINE, rx=4)
    text(mx + 16, 850, name, 13.5, INK, 600)
    text(mx + 16, 869, role, 11, INK2)
    text(mx + 16, 887, note, 10, MUTED)
    mx += 436

# ---------------------------------------------------------------- 页脚
text(64, 932, "当前规模：3 篇文档 · 251 条记录（Syensqo 124 / NOK 115 / 未标注 12）· 单页 OCR 5–12 s · 103 页抽取 ≈ 25 min", 10.5, MUTED)

out.append("</svg>")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(out), encoding="utf-8")
print(f"written {OUT} ({len(OUT.read_text(encoding='utf-8'))} chars)")
