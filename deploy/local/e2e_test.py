"""端到端验收：注册/登录 → 投喂 DOCX → 抽取入库 → 问答带引用。

用法:
    .venv\\Scripts\\python deploy/local/e2e_test.py
    .venv\\Scripts\\python deploy/local/e2e_test.py --keep   # 不重复建号时用已有 admin

依赖：python-docx（requirements-local.txt 已包含）
"""

from __future__ import annotations

import argparse
import http.cookiejar
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from docx import Document
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

WEB = f"http://127.0.0.1:{os.environ.get('MME_PORT_WEB', '8300')}"
INVITE = os.environ.get("INVITE_CODE", "")
USERNAME = os.environ.get("E2E_USER", "admin")
PASSWORD = os.environ.get("E2E_PASSWORD", "Mme@2026")

JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


def api(path: str, payload: dict | None = None, method: str | None = None, raw: bytes | None = None, ctype: str = "application/json"):
    data = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    headers = {"Content-Type": ctype} if data else {}
    req = urllib.request.Request(WEB + path, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    return OPENER.open(req, timeout=600)


def sse(path: str, payload: dict | None = None, raw: bytes | None = None, ctype: str = "application/json"):
    response = api(path, payload, raw=raw, ctype=ctype)
    for line in response:
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("data:"):
            continue
        yield json.loads(text[5:].strip())


def login_or_register() -> None:
    me = json.loads(api("/api/auth/me").read().decode("utf-8"))
    if me.get("user"):
        print(f"已登录：{me['user']['username']}")
        return
    if me.get("has_users"):
        body = json.dumps({"username": USERNAME, "password": PASSWORD, "invite": ""}).encode()
        try:
            api("/api/auth/login", raw=body)
            print(f"登录成功：{USERNAME}")
            return
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"已有账号但登录失败（{exc.code}）：改 E2E_USER / E2E_PASSWORD 再试")
    body = json.dumps({"username": USERNAME, "password": PASSWORD, "invite": INVITE}).encode()
    try:
        api("/api/auth/register", raw=body)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"注册失败（{exc.code}）：{exc.read().decode('utf-8', 'replace')}")
    print(f"注册成功：{USERNAME}（管理员）")


def build_docx() -> bytes:
    document = Document()
    document.add_heading("FFKM 密封件材料数据表（测试样本）", level=1)
    document.add_paragraph("本文件由 MME 自检脚本生成，用于验证解析 → 抽取 → 入库 → 问答全链路。")

    rows = [
        ["牌号", "供应商", "组成", "温度范围", "压缩永久变形", "耐化学性", "测试介质", "测试温度", "测试时长"],
        ["FFKM-70A", "示例供应商甲", "全氟醚橡胶", "-20~230℃", "25%", "耐等离子体、耐酸碱", "空气", "200℃", "24h"],
        ["FFKM-80B", "示例供应商乙", "全氟醚橡胶（含填料）", "-15~220℃", "18%", "耐酮类、耐酯类", "IRM 903 油", "150℃", "70h"],
        ["PEEK-450G", "示例供应商丙", "聚醚醚酮", "-60~260℃", "不适用", "耐大多数有机溶剂", "不适用", "不适用", "不适用"],
    ]
    table = document.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r, c).text = value

    document.add_paragraph("备注：FFKM-70A 适用于半导体刻蚀腔体密封；PEEK-450G 用于高温结构件。")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_scanned_pdf() -> bytes:
    """生成一份「没有文本层」的扫描版 PDF（图片 PDF），用来验证 OCR 兜底。"""
    from PIL import Image, ImageDraw, ImageFont

    font_path = "C:/Windows/Fonts/msyh.ttc"
    font = ImageFont.truetype(font_path, 34) if os.path.exists(font_path) else ImageFont.load_default()
    image = Image.new("RGB", (1654, 1000), "white")
    draw = ImageDraw.Draw(image)
    lines = [
        "FFKM 材料数据表（扫描件测试）",
        "牌号：FFKM-SCAN-1    供应商：示例供应商丁",
        "组成：全氟醚橡胶    温度范围：-20~230 摄氏度",
        "压缩永久变形：22%    测试介质：空气",
        "测试温度：200 摄氏度    测试时长：24h",
        "耐化学性：耐等离子体、耐酸碱",
    ]
    for index, line in enumerate(lines):
        draw.text((90, 90 + index * 70), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=150)
    return buffer.getvalue()


def upload(data: bytes, filename: str) -> dict:
    boundary = "----mme" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"collection\"\r\n\r\ncatalog\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"material\"\r\n\r\n\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n"
        ).encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    summary = {}
    for event in sse("/api/ingest", raw=b"".join(parts), ctype=f"multipart/form-data; boundary={boundary}"):
        kind = event.get("type")
        if kind == "stage":
            print(f"  阶段：{event.get('stage')} {({k: v for k, v in event.items() if k not in ('type', 'stage')})}")
        elif kind == "progress":
            print(f"  抽取 {event.get('done')}/{event.get('total')}，已得 {event.get('found')} 条", end="\r")
        elif kind == "warn":
            print(f"  警告：{event.get('message')}")
        elif kind == "error":
            print(f"  错误：{event.get('message')}")
        elif kind == "done":
            summary = event
    print()
    return summary


def ask(question: str) -> str:
    answer = []
    sources = []
    for event in sse("/api/chat", {"question": question, "collection": "catalog", "top_k": 5}):
        if event.get("type") == "token":
            answer.append(event.get("text", ""))
        elif event.get("type") == "sources":
            sources = event.get("items", [])
        elif event.get("type") == "error":
            print("  问答错误：" + event.get("message", ""))
    joined = "".join(answer).strip()
    if sources:
        print(f"  引用 {len(sources)} 条：")
        for item in sources[:3]:
            print(f"    - {item.get('source')} p.{item.get('page')}")
    return joined


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", default="FFKM-70A 的压缩永久变形和测试条件是什么？")
    parser.add_argument("--scanned", action="store_true", help="额外验证扫描版 PDF 的 OCR 兜底")
    args = parser.parse_args()

    print(f"Web {WEB}")
    login_or_register()

    print("\n[1] 投喂测试 DOCX")
    summary = upload(build_docx(), f"mme-selftest-{uuid.uuid4().hex[:6]}.docx")
    print("  结果：" + json.dumps(summary, ensure_ascii=False))
    if summary.get("added", 0) == 0 and summary.get("skipped", 0) == 0:
        print("  没有新增记录，后续问答可能无内容")

    if args.scanned:
        print("\n[1b] 投喂扫描版 PDF（无文本层，走 OCR）")
        scanned = upload(build_scanned_pdf(), f"mme-scanned-{uuid.uuid4().hex[:6]}.pdf")
        print("  结果：" + json.dumps(scanned, ensure_ascii=False))
        if scanned.get("added", 0) == 0:
            print("  !! OCR 兜底可能没生效，检查 OCR_BASE_URL 与 OCR 服务日志")
            return 3

    print(f"\n[2] 问答：{args.question}")
    answer = ask(args.question)
    print("  回答：")
    for line in answer.splitlines():
        print("    " + line)

    ok = bool(answer) and "资料不足" not in answer
    print("\n结果：" + ("通过" if ok else "未通过（回答为空或资料不足）"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
