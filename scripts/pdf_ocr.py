"""离线 OCR 预处理：把扫描版 PDF 先啃成「文本层」，让投喂不再逐页等 OCR。

用法：
    python scripts/pdf_ocr.py inbox/_done/catalog/xxx.pdf
    python scripts/pdf_ocr.py book.crx --dpi 300 --engine local
    python scripts/pdf_ocr.py book.pdf --pages 1-20 --force   # 抽检

做三件事：
  1. 有可用文本层的页直接沿用（判定与投喂时的 ingest.parse 完全一致），只 OCR 真正缺字的页；
  2. 每页结果立刻追加到 logs/pdf_ocr_cache/<sha1>.v1.jsonl —— 中断后重跑只补没做完的页；
  3. 跑完写 done 行，之后投喂原文件，parse() 直接取缓存，秒级完成解析。

为什么不产出「带文字层的 PDF」：pdfium/pypdf 都画不了中文字形，要另外引入
PyMuPDF + CJK 字体，成本大于收益 —— 原 PDF 照样留在库里可预览。
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from ingest import ocr, ocrcache  # noqa: E402
from ingest.parse import _broken  # noqa: E402

LOCK = threading.Lock()
DPI = 200
PAGE_RETRY = 3


def is_pdf(data: bytes) -> bool:
    return b"%PDF" in data[:1024]


def render(page, dpi: int) -> bytes:
    from PIL import Image

    bitmap = page.render(scale=dpi / 72)
    layout = bitmap.to_pil()
    buffer = io.BytesIO()
    layout.save(buffer, format="PNG")
    return buffer.getvalue()


class ServiceEngine:
    """走 OCR_BASE_URL（8101）—— 大图在服务里跑，进程内存干净。"""

    name = "service"

    def lines(self, png: bytes, label: str) -> list[dict]:
        return ocr.image_to_lines(png, label)


class LocalEngine:
    """本机 rapidocr 进程内跑（服务没起时的兜底），模型与 ocr_service 一致。"""

    name = "local"

    def __init__(self) -> None:
        from ocr_service.main import engine  # 懒加载，导入不建模型

        self._engine = engine

    def lines(self, png: bytes, label: str) -> list[dict]:
        from PIL import Image

        from ocr_service.main import _open

        image = _open(png).convert("RGB")
        with LOCK:
            result, _elapse = self._engine()(np.array(image))
        return [
            {"text": str(text), "score": float(score), "box": box} for box, text, score in (result or [])
        ]


def pick_engine(choice: str):
    if choice in ("service", "auto") and ocr.available():
        return ServiceEngine()
    if choice == "service":
        raise SystemExit("OCR_BASE_URL 未配置，无法走服务；改用 --engine local")
    return LocalEngine()


def parse_pages(spec: str) -> set[int]:
    """'1-20,30' -> {1..20, 30}"""
    out: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            out.update(range(int(start), int(end) + 1))
        else:
            out.add(int(part))
    return out


def process(path: Path, args, engine) -> None:
    data = path.read_bytes()
    if not is_pdf(data):
        raise SystemExit(f"{path.name}: 不是 PDF（文件头不是 %PDF）")

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    total = len(document)
    digest = ocrcache.key(data)
    wanted = parse_pages(args.pages) if args.pages else set(range(1, total + 1))
    wanted = {p for p in wanted if 1 <= p <= total}

    done = ocrcache.stats(digest)
    if args.force:
        ocrcache.clear(digest)
        done = {}

    print(f"[{path.name}] {total} 页 · sha1 {digest[:12]} · 引擎 {engine.name} · {args.dpi} DPI")
    print(f"  缓存 {ocrcache.path(digest)}（已完成 {len(done)} 页）")

    started = time.time()
    ocr_pages = 0
    failures = 0
    for index in sorted(wanted):
        if index in done and args.resume:
            continue
        page = document[index - 1]
        try:
            text = page.get_textpage().get_text_bounded() or ""
        except Exception:  # noqa: BLE001 - 单页取字失败就当无文本层处理
            text = ""

        source = "text"
        if args.force or not text.strip() or _broken(text):
            if index in done and args.resume and done[index]:
                text, source = done[index], "cache"
            else:
                for attempt in range(PAGE_RETRY):
                    try:
                        text = ocr.render_lines(engine.lines(render(page, args.dpi), f"p{index}.png"))
                        source = "ocr"
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt == PAGE_RETRY - 1:
                            failures += 1
                            print(f"\n  第 {index} 页 OCR 失败: {type(exc).__name__}: {exc}")
                            text = text if text.strip() else ""
                        else:
                            time.sleep(1.5 * (attempt + 1))
                if source == "ocr":
                    ocr_pages += 1
        ocrcache.append(digest, index, text)

        elapsed = time.time() - started
        speed = (index - min(wanted) + 1) / max(elapsed, 0.01)
        eta = (len(wanted) - len([p for p in wanted if p <= index])) / max(speed, 0.01)
        print(
            f"\r  {index}/{total} 页 · OCR {ocr_pages} 页 · 失败 {failures} · "
            f"{speed:.2f} 页/s · 预计剩余 {eta / 60:.0f} 分",
            end="",
            flush=True,
        )

    print()
    if wanted == set(range(1, total + 1)):
        ocrcache.finish(digest, total, args.dpi)
        print(f"  完成：{total} 页已落盘，投喂原文件即可（parse 会直接用这份文本）")
    else:
        print("  只跑了部分页（--pages），未写完成标记；缓存不会被 parse 采用")


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描版 PDF 的离线 OCR 预处理（断点续跑）")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--dpi", type=int, default=int(os.environ.get("OCR_DPI", "200")))
    parser.add_argument("--engine", choices=("auto", "service", "local"), default="auto")
    parser.add_argument("--pages", default="", help="只跑指定页，如 1-20,35（不写完成标记）")
    parser.add_argument("--force", action="store_true", help="忽略已有文本层和缓存，全部重 OCR")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="不跳过已完成的页")
    args = parser.parse_args()

    engine = pick_engine(args.engine)
    for raw in args.files:
        path = Path(raw)
        if not path.exists():
            print(f"跳过（不存在）: {raw}")
            continue
        process(path, args, engine)


if __name__ == "__main__":
    main()
