"""从 .firecrawl 下的 index.md 提取结构化材料数据（LangChain ChatOpenAI）。

用法: python extract.py [--profile profiles/ffkm.yaml]
"""
import argparse
import json
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


def load_profile(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_prompt(profile):
    prompt = profile.get("extraction_prompt", "")
    fields = f"字段：{', '.join(profile.get('field_map', {}).keys())}"
    lines = [fields if line.startswith("字段：") else line for line in prompt.splitlines()]
    return "\n".join(lines)


def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        pass
    records = []
    for chunk in re.findall(r"\[.*?\]", text, re.DOTALL):
        try:
            data = json.loads(chunk)
        except Exception:
            continue
        if isinstance(data, list):
            records.extend(data)
    if records:
        return records
    print(f"[解析失败] {text[:100]}")
    return []


def merge_records(out_dir):
    merged = []
    for path in sorted(out_dir.glob("*.json")):
        if path.name == "merged.json":
            continue
        merged.extend(json.loads(path.read_text(encoding="utf-8")))
    target = out_dir / "merged.json"
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[合并] {target} ({len(merged)}条)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="profiles/ffkm.yaml")
    args = parser.parse_args()

    load_dotenv()
    profile = load_profile(args.profile)
    prompt = build_prompt(profile)
    keywords = profile.get("keywords", [])
    llm = ChatOpenAI(
        model=os.environ.get("LLM_MODEL", "deepseek-chat"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        timeout=60,
        max_retries=1,
    )

    out_dir = Path("raw_json") / Path(args.profile).stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in out_dir.glob("*.json"):
        path.unlink()

    root = Path(profile.get("source_dir", ".firecrawl")) / Path(args.profile).stem
    if not root.is_dir():
        root = Path(profile.get("source_dir", ".firecrawl"))
    for md in sorted(root.rglob("index.md")):
        text = md.read_text(encoding="utf-8")
        if keywords and not any(k.lower() in text.lower() for k in keywords):
            print(f"[跳过-无关] {md}")
            continue
        print(f"[开始] {md}")
        try:
            response = llm.invoke(prompt.format(text=text))
            records = parse_json(response.content)
        except Exception as exc:
            print(f"[跳过] {md} 原因: {exc}")
            continue
        name = str(md.relative_to(root)).replace("\\", "_").replace("/", "_")
        target = out_dir / f"{name}.json"
        target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[完成] {md} ({len(records)}条)")

    merge_records(out_dir)


if __name__ == "__main__":
    main()
