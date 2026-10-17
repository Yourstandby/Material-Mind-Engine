"""把 feedback.jsonl 中的人工纠错追加到 Chroma collection（upsert，不删旧数据）。

用法: python feedback.py [feedback.jsonl] [--persist ./chroma_db]
      python feedback.py --interactive
"""
import argparse
import hashlib
import json
from pathlib import Path

import chromadb


def load_records(path):
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def append_interactive(path):
    record = {
        "question": input("question: "),
        "correct_answer": input("correct_answer: "),
        "collection": input("collection [materials]: ") or "materials",
        "grade": input("grade: "),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"appended -> {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", default="feedback.jsonl")
    parser.add_argument("--persist", default="./chroma_db")
    parser.add_argument("--interactive", action="store_true", help="交互式录入一条纠错")
    args = parser.parse_args()

    if args.interactive:
        append_interactive(args.input)
        return

    grouped = {}
    for record in load_records(args.input):
        grouped.setdefault(record.get("collection", "materials"), []).append(record)

    client = chromadb.PersistentClient(path=args.persist)
    total = 0
    for name, items in grouped.items():
        collection = client.get_or_create_collection(name)
        documents = [
            f"question: {r['question']}\ncorrect_answer: {r['correct_answer']}" for r in items
        ]
        ids = [hashlib.md5(d.encode()).hexdigest() for d in documents]
        metadatas = [{"grade": r.get("grade", ""), "source": "feedback"} for r in items]
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        total += len(ids)
        print(f"{len(ids)} feedback -> {args.persist}/{name}")
    print(f"total {total}")


if __name__ == "__main__":
    main()
