"""三阶段主流程：raw_json -> 清洗 -> 向量库 -> 问答。

用法: python main.py [--raw ./raw_json] [--db ./chroma_db]
"""
import argparse
import subprocess
import sys
from pathlib import Path


def run(script, *args):
    subprocess.run([sys.executable, script, *args], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="./raw_json")
    parser.add_argument("--db", default="./chroma_db")
    parser.add_argument("--collection", default="materials")
    parser.add_argument("--min-confidence", type=float, default=0.6)
    args = parser.parse_args()

    cleaned_dir = Path("./cleaned_json")
    cleaned_dir.mkdir(exist_ok=True)

    for src in sorted(Path(args.raw).glob("*.json")):
        cleaned = cleaned_dir / src.name
        run("data_cleaner.py", str(src), str(cleaned), "--min-confidence", str(args.min_confidence))
        run("vector_store.py", str(cleaned), "--persist", args.db, "--collection", args.collection)

    run("query.py")


if __name__ == "__main__":
    main()
