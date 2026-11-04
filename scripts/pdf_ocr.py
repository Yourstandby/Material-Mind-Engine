"""离线 OCR 预处理：把扫描版 PDF 先啃成「文本层」，让投喂不再逐页等 OCR。

用法：
    python scripts/pdf_ocr.py inbox/_done/catalog/xxx.pdf
    python scripts/pdf_ocr.py book.crx --parallel 4          # 多进程并行（推荐）
    python scripts/pdf_ocr.py book.crx --dpi 300 --engine service
    python scripts/pdf_ocr.py book.pdf --pages 1-20 --force  # 抽检

做三件事：
  1. 有可用文本层的页直接沿用（判定与投喂时的 ingest.parse 完全一致），只 OCR 真正缺字的页；
  2. 每页结果立刻追加到 logs/pdf_ocr_cache/<sha1>.v1.jsonl —— 中断后重跑只补没做完的页；
  3. 跑完写 done 行，之后投喂原文件，parse() 直接取缓存，秒级完成解析。

并行说明：OCR 服务（8101）内部串行（一个进程一把锁），要压满多核只能
「多进程 + 进程内 rapidocr」，所以 --parallel>1 时自动转本地引擎。

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
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import ocr, ocrcache  # noqa: E402
from ingest.parse import _broken  # noqa: E402

PAGE_RETRY = 3
LOCK = threading.Lock()


def is_pdf(data: bytes) -> bool:
    return b"%PDF" in data[:1024]


def render(page, dpi: int) -> bytes:
    bitmap = page.render(scale=dpi / 72)
    buffer = io.BytesIO()
    bitmap.to_pil().save(buffer, format="PNG")
    return buffer.getvalue()


def _lines_local(png: bytes) -> list[dict]:
    """进程内 rapidocr；模型懒加载到模块级，一个进程只建一次。"""
    import numpy as np
    from PIL import Image

    from ocr_service.main import _open, engine

    image = _open(png).convert("RGB")
    with LOCK:
        result, _elapse = engine()(np.array(image))
    return [{"text": str(text), "score": float(score), "box": box} for box, text, score in (result or [])]


class ServiceEngine:
    """走 OCR_BASE_URL（8101），服务进程内串行，只适合单线程跑。"""

    name = "service"
    parallel = 1

    def text(self, page, dpi: int) -> str:
        return ocr.render_lines(ocr.image_to_lines(render(page, dpi), "page.png"))


class LocalEngine:
    name = "local"
    parallel = 1

    def text(self, page, dpi: int) -> str:
        return ocr.render_lines(_lines_local(render(page, dpi)))


def pick_engine(choice: str):
    if choice in ("service", "auto") and ocr.available():
        return ServiceEngine()
    if choice == "service":
        raise SystemExit("OCR_BASE_URL 未配置，无法走服务；改用 --engine local")
    return LocalEngine()


# ----------------------------- 子进程侧 -----------------------------
_DOCS: dict[str, object] = {}


def _doc(path: str):
    import pypdfium2 as pdfium

    doc = _DOCS.get(path)
    if doc is None:
        doc = pdfium.PdfDocument(path)
        _DOCS[path] = doc
    return doc


def _worker(job: tuple[str, int, int]) -> tuple[int, str]:
    path, page_no, dpi = job
    page = _doc(path)[page_no - 1]
    last: Exception | None = None
    for attempt in range(PAGE_RETRY):
        try:
            return page_no, ocr.render_lines(_lines_local(render(page, dpi)))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"第 {page_no} 页 OCR 失败: {last}")


# ----------------------------- 主流程 -----------------------------
def parse_pages(spec: str) -> set[int]:
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


def _progress(shown: int, total: int, ocr_pages: int, failed: int, started: float) -> None:
    elapsed = max(time.time() - started, 0.01)
    speed = shown / elapsed
    eta = (total - shown) / speed if speed else 0
    print(
        f"\r  已处理 {shown}/{total} 页 · OCR {ocr_pages} 页 · 失败 {failed} · "
        f"{speed:.2f} 页/s · 剩约 {eta / 60:.0f} 分",
        end="",
        flush=True,
    )


def process(path: Path, args, engine) -> None:
    data = path.read_bytes()
    if not is_pdf(data):
        raise SystemExit(f"{path.name}: 不是 PDF（文件头不是 %PDF）")

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    total = len(document)
    digest = ocrcache.key(data)
    wanted = parse_pages(args.pages) if args.pages else set(range(1, total + 1))
    wanted = sorted(p for p in wanted if 1 <= p <= total)

    done = {} if args.force else (ocrcache.stats(digest) if args.resume else {})
    if args.force:
        ocrcache.clear(digest)

    parallel = 1 if engine.name == "service" else max(1, min(args.parallel, 16))
    print(f"[{path.name}] {total} 页 · sha1 {digest[:12]} · {engine.name}×{parallel} · {args.dpi} DPI")
    print(f"  缓存 {ocrcache.path(digest)}（已完成 {len(done)} 页）")

    started = time.time()
    shown = ocr_pages = failed = 0
    pending: list[int] = []

    # 1) 文本层页 / 已缓存页：直接落盘，不占 OCR
    for index in wanted:
        if index in done:
            shown += 1
            continue
        try:
            text = document[index - 1].get_textpage().get_text_bounded() or ""
        except Exception:  # noqa: BLE001 - 单页取字失败当作无文本层
            text = ""
        if not args.force and text.strip() and not _broken(text):
            ocrcache.append(digest, index, text)
            shown += 1
            _progress(shown, len(wanted), ocr_pages, failed, started)
        else:
            pending.append(index)

    # 2) 需要 OCR 的页：并行提交，边完成边落盘
    if pending and parallel > 1:
        with ProcessPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(_worker, (str(path), page, args.dpi)): page for page in pending}
            for future in futures:
                page_no = futures[future]
                try:
                    page_no, text = future.result()
                except Exception as exc:  # noqa: BLE001 - 单页失败不影响整本
                    failed += 1
                    print(f"\n  第 {page_no} 页失败: {exc}")
                else:
                    ocrcache.append(digest, page_no, text)
                    ocr_pages += 1 if text.strip() else 0
                shown += 1
                _progress(shown, len(wanted), ocr_pages, failed, started)
    elif pending:
        for index in pending:
            page = document[index - 1]
            text = ""
            for attempt in range(PAGE_RETRY):
                try:
                    text = engine.text(page, args.dpi)
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == PAGE_RETRY - 1:
                        failed += 1
                        print(f"\n  第 {index} 页 OCR 失败: {type(exc).__name__}: {exc}")
                    else:
                        time.sleep(1.5 * (attempt + 1))
            ocrcache.append(digest, index, text)
            if text.strip():
                ocr_pages += 1
            shown += 1
            _progress(shown, len(wanted), ocr_pages, failed, started)

    print()
    if wanted == list(range(1, total + 1)):
        ocrcache.finish(digest, total, args.dpi)
        print(f"  完成：{total} 页已落盘（OCR {ocr_pages} 页，失败 {failed}），投喂原文件即可")
    else:
        print("  只跑了部分页（--pages），未写完成标记；缓存不会被 parse 采用")


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描版 PDF 的离线 OCR 预处理（断点续跑 / 多进程）")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--dpi", type=int, default=int(os.environ.get("OCR_DPI", "200")))
    parser.add_argument("--engine", choices=("auto", "service", "local"), default="auto")
    parser.add_argument("--parallel", type=int, default=2, help="OCR 进程数（>1 时自动用本地引擎）")
    parser.add_argument("--pages", default="", help="只跑指定页，如 1-20,35（不写完成标记）")
    parser.add_argument("--force", action="store_true", help="忽略已有文本层和缓存，全部重 OCR")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="不跳过已完成的页")
    args = parser.parse_args()

    # 多进程时给每个 ONNX 会话分一条窄线程带，否则 N 个进程各开满核互相抢，反而更慢
    if args.parallel > 1 and args.engine != "service":
        os.environ.setdefault("OMP_NUM_THREADS", str(max(2, (os.cpu_count() or 8) // args.parallel)))

    engine = pick_engine(args.engine)
    for raw in args.files:
        path = Path(raw)
        if not path.exists():
            print(f"跳过（不存在）: {raw}")
            continue
        process(path, args, engine)


if __name__ == "__main__":
    main()
