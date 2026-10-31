"""抽取基线：对资料库里的真实 PDF 量出「文本层 vs OCR + 版面还原」的差距。

用途：改抽取链路前先有数，改完再跑一次对比，避免「改了说不清好坏」。

跑法:
    python scripts/baseline_extract.py                 # 全量（慢，会跑 OCR）
    python scripts/baseline_extract.py --no-ocr        # 只统计文本层，不调 OCR
    python scripts/baseline_extract.py --pages 3       # 每份 PDF 最多跑前 3 页

输出:
    eval/baseline_report.json   机器可读，供回归对比
    eval/baseline_report.md     人读摘要

指标说明：
    text_layer_pages  有文本层的页数
    ocr_pages         走了 OCR 的页数
    table_pages       版面还原判定为表格的页数
    low_conf_lines    OCR 置信度 < 0.8 的行数（这些行不该直接抽数值）
    suspect_lines     含数值但缺单位的行数——错行的最常见前兆
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest import layout, ocr, parse  # noqa: E402

PDF_DIR = ROOT / "inbox" / "_done" / "catalog"
OUT_JSON = ROOT / "eval" / "baseline_report.json"
OUT_MD = ROOT / "eval" / "baseline_report.md"

# 有数值、但附近没有任何单位/条件词的文本 —— 抽成结构化记录时最容易错行
_UNIT = re.compile(r"(°C|℃|MPa|psi|kPa|%|h\b|hr|小时|分钟|min|ppm|mm|N/|硬度|Shore|IRHD)")
_NUMBER = re.compile(r"\d")


def _suspect(text: str) -> bool:
    for line in (text or "").splitlines():
        if _NUMBER.search(line) and not _UNIT.search(line):
            return True
    return False


def _raw_text_layer(pdf: Path) -> list[str]:
    """只用文本层拿文本，不走 OCR —— 基线需要知道「PDF 本身有多少字」。"""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    return pages


def _pages_of(pdf: Path) -> list[str]:
    """走完整解析链路（含 OCR 降级），用来看「最终拿到了多少字」。"""
    data = pdf.read_bytes()
    try:
        result = parse.parse(pdf.name, data)
    except Exception as exc:  # noqa: BLE001 - 单份失败不影响整体基线
        return [f"!! 解析失败 {type(exc).__name__}: {exc}"]
    pages = [p["text"] for p in result.get("pages") or []]
    return pages


def _ocr_pages(pdf: Path, limit: int) -> list[str]:
    data = pdf.read_bytes()
    out = []
    for index in range(1, limit + 1):
        try:
            image = parse._render_page(data, index)
            out.append(ocr.image_to_text(image, f"{pdf.name}-p{index}.png"))
        except Exception as exc:  # noqa: BLE001 - 页数越界或 OCR 挂了就地停下
            if index == 1:
                out.append(f"!! OCR 失败 {type(exc).__name__}: {exc}")
            break
    return out


def measure(pdf: Path, limit: int, use_ocr: bool) -> dict:
    raw = _raw_text_layer(pdf)
    pages = _pages_of(pdf)
    raw_empty = [i for i, t in enumerate(raw, 1) if len(t.strip()) < 20]
    final_empty = [i for i, t in enumerate(pages, 1) if len(t.strip()) < 20]
    record = {
        "file": pdf.name,
        "pages": len(raw),
        # 文本层本来就是空的页 = 扫描件，只能靠 OCR
        "scan_pages": raw_empty,
        "text_layer_pages": len(raw) - len(raw_empty),
        # 跑完解析（含 OCR 降级）仍然没字 = 真的抽不出来
        "final_empty_pages": final_empty,
        "chars": sum(len(t.strip()) for t in pages),
        "suspect_line_pages": sum(1 for t in pages if _suspect(t)),
        "ocr_pages": 0,
        "table_pages": 0,
        "low_conf_lines": 0,
        "notes": [],
    }
    if not use_ocr or not ocr.available():
        return record

    # 只对无文本层的前 N 页跑 OCR，避免 100 页的目录把基线跑成几分钟
    targets = [i for i in raw_empty][:limit]
    for index in targets:
        try:
            image = parse._render_page(pdf.read_bytes(), index)
            lines = ocr.image_to_lines(image, f"{pdf.name}-p{index}.png")
        except Exception as exc:  # noqa: BLE001
            record["notes"].append(f"第 {index} 页 OCR 失败: {type(exc).__name__}")
            continue
        text, is_table = layout.render(lines, ocr._page_width(lines))
        record["ocr_pages"] += 1
        record["table_pages"] += 1 if is_table else 0
        record["low_conf_lines"] += len(layout.low_confidence(lines))
        if not text.strip():
            record["notes"].append(f"第 {index} 页 OCR 无输出")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--dir", default=str(PDF_DIR))
    args = parser.parse_args()

    pdfs = sorted(Path(args.dir).glob("*.pdf"))
    if not pdfs:
        print(f"没找到 PDF: {args.dir}")
        return 1

    records = [measure(pdf, args.pages, not args.no_ocr) for pdf in pdfs]
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# 抽取基线", ""]
    lines.append("| 文件 | 页数 | 文本层页 | 扫描页 | 仍空白页 | 字符 | 含数值缺单位页 | OCR 页 | 表格页 | 低置信行 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in records:
        lines.append(
            f"| {r['file']} | {r['pages']} | {r['text_layer_pages']} | {len(r['scan_pages'])} | "
            f"{len(r['final_empty_pages'])} | {r['chars']} | {r['suspect_line_pages']} | "
            f"{r['ocr_pages']} | {r['table_pages']} | {r['low_conf_lines']} |"
        )
    totals = {
        "pages": sum(r["pages"] for r in records),
        "scan_pages": sum(len(r["scan_pages"]) for r in records),
        "still_empty": sum(len(r["final_empty_pages"]) for r in records),
        "text_layer_pages": sum(r["text_layer_pages"] for r in records),
        "ocr_pages": sum(r["ocr_pages"] for r in records),
        "table_pages": sum(r["table_pages"] for r in records),
        "low_conf_lines": sum(r["low_conf_lines"] for r in records),
    }
    lines += ["", f"合计: {totals}"]
    notes = [f"- {r['file']}: {n}" for r in records for n in r["notes"]]
    if notes:
        lines += ["", "## 备注"] + notes
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print(f"\n写入 {OUT_JSON.relative_to(ROOT)} / {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
