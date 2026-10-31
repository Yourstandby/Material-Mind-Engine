"""投喂流水线：解析 → 分块 → 抽取 → 归一化 → 去重 → 向量化 → upsert。

用法（CLI）: python -m ingest.pipeline inbox/*.pdf --collection peek
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import chromadb

from core import catalog, embeddings
from core import applications
from core import vector_store as vs
from core.config import env
from core.query_engine import FIELDS as FIELD_LABELS
from ingest import metadata, repo
from ingest.chunk import split
from ingest.extract import extract_all
from ingest.parse import parse

MIN_CONFIDENCE = 0.6
QUARANTINE = Path("quarantine.jsonl")
LOG = Path("ingest.log")


def _log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


FIELD_LABELS = dict(FIELD_LABELS)


def _passage_collection(collection: str) -> str:
    """原文片段单独存，不能混进问答主集合，否则检索会把叙述和数值当成同一类证据。"""
    return f"{collection}__passages"


def _dedupe_passages(passages: list[dict]) -> list[dict]:
    seen, out = set(), []
    for item in passages:
        text = (item.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(item)
    return out


def _store_passages(collection: str, passages: list[dict], doc_id: str, material_hint: str = "") -> int:
    """把叙述性原文片段写进独立集合。失败不影响主流程（记录已经入库）。"""
    items = _dedupe_passages(passages)
    if not items:
        return 0
    try:
        client = _client()
        model = embeddings.config()["model"]
        store = client.get_or_create_collection(
            _passage_collection(collection), metadata={"embedding_model": model}
        )
        ids = [hashlib.md5(f"{doc_id}:passage:{i}".encode()).hexdigest() for i in range(len(items))]
        documents = [item["text"] for item in items]
        metadatas = [
            {
                "doc_type": "passage",
                "kind": item.get("kind") or "其他",
                "source": item.get("source") or "",
                "page": int(item.get("page") or 0),
                "doc_id": doc_id,
                "confidence": float(item.get("confidence") or 0.5),
                "material": material_hint or "",
            }
            for item in items
        ]
        store.upsert(
            ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings.embed(documents)
        )
        return len(items)
    except Exception as exc:  # noqa: BLE001 - 片段入库失败不应丢掉已抽到的记录
        _log(f"  原文片段入库失败: {type(exc).__name__}: {exc}")
        return 0


def serialize(record: dict) -> str:
    """向量化用的文本：字段名用中文，中文提问才能直接命中（cs → 压缩永久变形）。"""
    from core.normalize import localize

    lines = []
    for key, value in record.items():
        if value in (None, "", [], {}):
            continue
        # 行业/部件等原文已归一成中文标签，不重复进向量文本
        if key in ("industry", "part", "medium", "certifications", "industry_codes", "parts_codes"):
            continue
        if key == "material":
            value = catalog.material_label(str(value))
        elif isinstance(value, str):
            # 日语资料里的「72時間」「潤滑油」等统一成中文，检索和展示都不再出现假名
            value = localize(value)
        lines.append(f"{FIELD_LABELS.get(key, key)}: {value}")
    return "\n".join(lines)


def _meta(record: dict, doc_id: str = "", material_hint: str = "", doc_type: str = "") -> dict:
    meta = {k: v for k, v in record.items() if isinstance(v, (str, int, float, bool))}
    if doc_id:
        meta["doc_id"] = doc_id
    if doc_type:
        meta["doc_type"] = doc_type
    supplier = str(record.get("supplier") or "")
    grade = str(record.get("grade") or record.get("grade_raw") or "")
    # 抽取结果里没有材质线索时，用页面级判断 / 上传时选中的材质兜底（选「全部」则为空）
    fallback = str(record.get("material") or material_hint or "")
    material = catalog.material_of(str(record.get("composition") or ""), grade, fallback=fallback)
    if not grade and catalog.is_literature(doc_type):
        # 论文/专利没有牌号不是抽漏，别归到「未标注产品」里当垃圾
        meta["product_id"], meta["product_name"] = "literature", catalog.LITERATURE
    else:
        meta["product_id"] = catalog.product_key(supplier, grade)
        meta["product_name"] = catalog.product_name(supplier, grade)
    # 抽不到牌号的记录无法归属到具体产品，置信度不能还留着 LLM 自报的高分：
    # 只压不增，原始分留在 llm_confidence 里。
    identity = catalog.identity_confidence(supplier, grade, doc_type=doc_type)
    meta["identity_confidence"] = identity
    try:
        llm_conf = float(record.get("confidence") or 0.5)
    except (TypeError, ValueError):
        llm_conf = 0.5
    meta["llm_confidence"] = round(llm_conf, 3)
    meta["confidence"] = round(min(llm_conf, identity), 3)
    meta["material"] = material
    meta["material_label"] = catalog.material_label(material)
    # 应用是第一等维度：行业 / 部件 / 介质 / 认证（字符串，可直接入库）
    meta.update(applications.enrich(record))
    return meta


def _locate(collection: str, filename: str) -> str:
    path = Path("inbox") / "_done" / collection / Path(filename).name
    return str(path) if path.exists() else ""


# 页眉/页脚里的书名行会把每页都判成 FFKM（"FKM/FFKM 材料指南"），先剔掉
HEADER_NOISE = ("材料指南",)

# FFKM 判定在前：全氟体系优先
MATERIAL_SIGNALS = (
    ("FFKM", ("ffkm", "全氟醚", "全氟橡胶", "全氟弹性体", "perfluoro", "pfr")),
    ("FKM", ("fkm", "氟橡胶", "氟弹性体", "fluoroelastomer")),
)

SUPPLIER_SIGNALS = (
    ("Solvay", ("solvay", "索尔维", "苏威", "syensqo")),
    ("Daikin Industries", ("daikin", "大金")),
    ("Chemours", ("chemours", "科慕")),
)


def _body_text(text: str) -> str:
    keep = [line for line in (text or "").splitlines() if not any(noise in line for noise in HEADER_NOISE)]
    return "\n".join(keep).lower()


def detect_material(text: str) -> str:
    """按页面正文判断材质；判不出来返回空串（交给上传时选的材质兜底）。"""
    body = _body_text(text)
    for code, keys in MATERIAL_SIGNALS:
        if any(key in body for key in keys):
            return code
    return ""


def detect_supplier(text: str) -> str:
    body = _body_text(text)
    for name, keys in SUPPLIER_SIGNALS:
        if any(key in body for key in keys):
            return name
    return ""


def _page_head(text: str) -> str:
    """给抽取用的页面表头：优先取「特性 单位 …」那一行，跨块表格才能对上列名。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if "特性" in line or "单位" in line:
            return " ".join(lines[i : i + 2])[:220]
    return " ".join(lines[:2])[:220]


HEAD_HINTS = ("特性", "単位", "单位", "番号", "牌号", "型号", "該当", "グレード", "品名")


def _head_at(page_text: str, piece: str) -> str:
    """取片段之前最近的一个表头；一页有多张表时不会拿错列名。"""
    index = page_text.find(piece[:30]) if piece else -1
    prefix = page_text[:index] if index > 0 else ""
    lines = prefix.splitlines()
    hit = -1
    for i, line in enumerate(lines):
        if any(hint in line for hint in HEAD_HINTS):
            hit = i
    if hit >= 0:
        # 表头常跨两三行（材料种类 / JIS 记号 / NOK 該当番号），一起带上
        return " ".join(line.strip() for line in lines[max(hit - 2, 0) : hit + 1])[:320]
    return _page_head(page_text)


def default_material(pages_text: list[str]) -> str:
    """整篇文档的兜底材质。

    只在同一种材质出现时整体沿用；FKM/FFKM 混排的文档里，
    FFKM 页通常会明写 PFR/全氟，所以没标记的页按 FKM 处理。
    """
    known = {detect_material(text) for text in pages_text} - {""}
    if len(known) == 1:
        return known.pop()
    if {"FKM", "FFKM"} <= known:
        return "FKM"
    return ""


def _client():
    return vs.client()


def auto_folder(records: list[dict], material_hint: str = "") -> str:
    """按记录里的主材质归入文件夹；判断不出进未分类。"""
    counts: dict = {}
    for record in records:
        code = catalog.material_of(
            str(record.get("composition") or ""),
            str(record.get("grade") or record.get("grade_raw") or ""),
            fallback=str(record.get("material") or material_hint or ""),
        )
        counts[code] = counts.get(code, 0) + 1
    if not counts:
        return repo.UNFILED
    code = max(counts.items(), key=lambda item: item[1])[0]
    if code == "OTHER":
        return repo.UNFILED
    return repo.ensure_folder(catalog.material_label(code), code.lower())


def ingest(
    filename: str,
    data: bytes,
    collection: str = "catalog",
    material_hint: str = "",
    folder_id: str = "",
    should_cancel=None,
):
    """生成器：逐条抛出进度事件。

    should_cancel 可传入一个 () -> bool 的回调（Web 端用来支持「取消投喂」）：
    每处理一块前检查一次，需要停就不再继续跑 LLM；已经抽到的记录照常走完入库，
    所以取消不等于白做——重新投喂同一文件时，已入库的 ID 相同会被跳过，只补剩下的。
    """
    if not embeddings.ready():
        yield {"type": "error", "message": "未配置 EMBEDDING_BASE_URL / EMBEDDING_MODEL，无法入库"}
        return

    yield {"type": "stage", "stage": "parse", "file": filename}
    try:
        doc = parse(filename, data)
    except Exception as exc:
        _log(f"[{collection}] {filename} 解析失败: {exc}")
        yield {"type": "error", "message": f"解析失败: {exc}"}
        return

    chars = sum(len(page["text"]) for page in doc["pages"])
    _log(f"[{collection}] {filename} 解析 {len(doc['pages'])} 页 / {chars} 字 / 警告 {len(doc['warnings'])}")

    for warning in doc["warnings"]:
        yield {"type": "warn", "message": warning}
    if not doc["pages"]:
        yield {"type": "error", "message": "没有解析出任何文本（可能是扫描件，需要 OCR）"}
        return

    did = repo.doc_id(data)

    chunks = []
    for page in doc["pages"]:
        for piece in split(page["text"]):
            chunks.append({"page": page["page"], "text": piece, "head": _head_at(page["text"], piece)})

    # 页面级材质 / 文档级供应商：LLM 抽不到时用来补全，让「FFKM」「索尔维」这类问题能被检索到
    fallback_material = default_material([page["text"] for page in doc["pages"]])
    page_material = {
        page["page"]: detect_material(page["text"]) or fallback_material for page in doc["pages"]
    }
    doc_supplier = detect_supplier("\n".join(page["text"] for page in doc["pages"]))

    if not chunks:
        yield {"type": "error", "message": f"解析到 {chars} 字，但没有有效分块"}
        return

    _log(f"  切分 {len(chunks)} 块")
    yield {"type": "stage", "stage": "extract", "chunks": len(chunks)}

    records: list[dict] = []
    passages: list[dict] = []
    failed = 0
    last_error = ""
    cancelled = False
    for index, chunk in enumerate(chunks, start=1):
        if should_cancel is not None:
            try:
                stopped = bool(should_cancel())
            except Exception:  # noqa: BLE001 - 取消标记读不到就继续，别因此中断入库
                stopped = False
            if stopped:
                cancelled = True
                _log(f"  第 {index}/{len(chunks)} 块前收到取消，已抽 {len(records)} 条照常入库")
                yield {"type": "warn", "message": f"已取消：已完成 {index - 1}/{len(chunks)} 块"}
                break
        label = f"{filename} p.{chunk['page']}"
        try:
            found, found_passages = extract_all(chunk["text"], label)
        except Exception as exc:
            failed += 1
            last_error = f"{type(exc).__name__}: {exc}"
            _log(f"  第 {index}/{len(chunks)} 块抽取失败: {last_error}")
            yield {"type": "warn", "message": f"第 {index}/{len(chunks)} 块抽取失败: {exc}"}
            continue
        hint = page_material.get(chunk["page"], "")
        for record in found:
            if not record.get("material") and hint:
                record["material"] = hint
            if not record.get("supplier") and doc_supplier:
                record["supplier"] = doc_supplier
            record.update(applications.enrich(record))
        records.extend(found)
        for passage in found_passages:
            passage["page"] = chunk["page"]
        passages.extend(found_passages)
        yield {"type": "progress", "done": index, "total": len(chunks), "found": len(records)}

    _log(f"  抽取 {len(records)} 条记录 / {len(passages)} 条片段（失败块 {failed}/{len(chunks)}）")

    if cancelled and not records and not passages:
        yield {
            "type": "done",
            "added": 0,
            "skipped": 0,
            "quarantined": 0,
            "cancelled": True,
            "message": "已取消，尚未抽到可用数据；重新投喂会从整份文件重跑",
        }
        return

    if not records and not passages:
        if failed == len(chunks):
            message = f"全部 {len(chunks)} 块抽取失败：{last_error}"
        else:
            message = f"读到了 {chars} 字，但没有可结构化的性能数据，也没有可引用的叙述片段"
        yield {"type": "done", "added": 0, "skipped": 0, "quarantined": 0, "message": message}
        return

    keep, quarantined = [], 0
    for record in records:
        if record["confidence"] < MIN_CONFIDENCE:
            quarantined += 1
            with QUARANTINE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            keep.append(record)

    doc_meta = metadata.extract(
        filename, data, doc["pages"], suppliers=[r.get("supplier") for r in keep]
    )

    yield {"type": "stage", "stage": "embed", "records": len(keep)}

    # 同一份文件里经常出现完全相同的行：跨页重复的表格、重复表头、页脚。
    # 以前用「文本 md5」当 ID，这些重复项在同一批 upsert 里撞 ID，
    # Chroma 会整批拒绝（DuplicateIDError: Expected IDs to be unique）。
    # 现在：先按文本去重，再用「文档 ID + 序号」生成 ID
    #   - 批内一定唯一，不会再撞；
    #   - 同一份文件重复投喂时 ID 不变，仍然走「已存在 → 跳过」的幂等路径。
    documents: list[str] = []
    unique_records: list[dict] = []
    seen_text: set[str] = set()
    duplicates = 0
    for record in keep:
        text = serialize(record)
        if text in seen_text:
            duplicates += 1
            continue
        seen_text.add(text)
        unique_records.append(record)
        documents.append(text)

    keep = unique_records
    ids = [hashlib.md5(f"{did}:{index}".encode()).hexdigest() for index in range(len(documents))]
    if duplicates:
        _log(f"  批内去重 {duplicates} 条完全重复的记录")
        yield {
            "type": "warn",
            "message": f"同一份文件里有 {duplicates} 条完全重复的记录（多为跨页重复的表格行），已合并",
        }

    client = _client()
    model = embeddings.config()["model"]
    store = client.get_or_create_collection(collection, metadata={"embedding_model": model})
    known = (store.metadata or {}).get("embedding_model")
    if known and known != model:
        yield {
            "type": "error",
            "message": f"该知识库由 {known} 构建，当前 embedding 为 {model}，需重建 collection",
        }
        return

    try:
        found_ids = set(store.get(ids=ids).get("ids") or [])
    except Exception:
        found_ids = set()

    try:
        vectors = embeddings.embed(documents) if documents else []
    except Exception as exc:
        yield {"type": "error", "message": f"向量化失败: {exc}"}
        return

    if documents:
        store.upsert(
            ids=ids,
            documents=documents,
            metadatas=[
                _meta(r, did, material_hint, str(doc_meta.get("doc_type") or "")) for r in keep
            ],
            embeddings=vectors,
        )
    if not known:  # 旧库没有元数据时补写，防止查询回退到默认嵌入
        try:
            store.modify(metadata={"embedding_model": model})
        except Exception:
            pass

    saved_passages = _store_passages(collection, passages, did, material_hint)
    if saved_passages:
        _log(f"  原文片段入库 {saved_passages} 条 → {_passage_collection(collection)}")
    # 归属文件夹：投喂时指定优先，否则按资料的主材质自动归入
    target_folder = folder_id if folder_id in repo.folder_map() else ""
    if not target_folder:
        target_folder = auto_folder(keep, material_hint)

    try:
        entry = repo.register(
            data=data,
            filename=filename,
            collection=collection,
            pages=len(doc["pages"]),
            chars=chars,
            records=len(keep),
            meta=doc_meta,
            path=_locate(collection, filename),
            folder_id=target_folder,
        )
    except Exception as exc:  # 登记失败不应影响入库
        _log(f"  文献登记失败: {exc}")
        entry = None

    for record in keep:
        yield {"type": "record", "record": record}

    if entry:
        yield {"type": "doc", "doc": entry}

    yield {
        "type": "done",
        "added": len(keep) - len(found_ids),
        "skipped": len(found_ids),
        "quarantined": quarantined,
        "duplicates": duplicates,
        "total": len(keep),
        "passages": saved_passages,
        "cancelled": cancelled,
        "message": (
            f"已取消：已入库 {len(keep) - len(found_ids)} 条，重新投喂同一文件会跳过已入库的记录"
            if cancelled
            else ""
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--collection", default="catalog")
    args = parser.parse_args()

    for path in args.files:
        file_path = Path(path)
        print(f"[{file_path.name}]")
        for event in ingest(file_path.name, file_path.read_bytes(), args.collection):
            if event["type"] in ("done", "error"):
                print("  ", json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    main()
