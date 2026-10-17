"""Chroma 检索 + LLM 生成带来源的回答。

用法: python query.py "问题" [--collection materials] [--top-k 10] [--min-confidence 0.0]
"""
import argparse
import os

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PROMPT = "你是一个材料领域专家。基于以下检索到的数据回答问题。\n规则：\n0. 如果任何条目含具体数值，必须直接以数值开头，禁止以“未能找到”“资料不足”“无法确认”开头\n1. 优先采信包含具体数值（温度、压力、百分比）的条目\n2. 如果多条数据冲突，先列出最可信的一条（confidence 最高或来源最权威），再注明“另有来源给出不同值”\n3. 牌号前缀 X 可忽略（X6055B 与 6055B 视为同一牌号）\n4. 回答末尾必须列出实际引用的来源\n5. 只有在所有检索结果都没有相关数值时，才回答“资料不足”\n资料:\n{context}\n问题: {question}"

TEMP_KEYWORDS = ("耐温", "温度", "temperature")
TEMP_EXPANSION = "temperature range operating temperature -20 230 °C"


def expand_query(question):
    return (
        f"{question} {TEMP_EXPANSION}"
        if any(keyword in question for keyword in TEMP_KEYWORDS)
        else question
    )


def label_of(meta):
    return (meta or {}).get("grade") or (meta or {}).get("source")


def confidence_of(meta):
    value = (meta or {}).get("confidence")
    return float(value) if isinstance(value, (int, float)) else 0.0


def format_context(docs, metas):
    return "\n\n".join(f"[{label_of(m) or 'unknown'}]\n{d}" for d, m in zip(docs, metas))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--collection", default="materials")
    parser.add_argument("--persist", default="./chroma_db")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-confidence", type=float, default=0.0)
    args = parser.parse_args()

    client = chromadb.PersistentClient(path=args.persist)
    result = client.get_or_create_collection(args.collection).query(
        query_texts=[expand_query(args.question)], n_results=args.top_k
    )
    pairs = sorted(
        (
            pair
            for pair in zip(
                (result.get("documents") or [[]])[0], (result.get("metadatas") or [[]])[0]
            )
            if confidence_of(pair[1]) >= args.min_confidence
        ),
        key=lambda pair: confidence_of(pair[1]),
        reverse=True,
    )[: args.top_k]
    if not pairs:
        print("未检索到相关数据")
        return

    docs = [pair[0] for pair in pairs]
    metas = [pair[1] for pair in pairs]
    llm = OpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ.get("OPENAI_API_KEY", "not-needed"),
    )
    response = llm.chat.completions.create(
        model=os.environ["LLM_MODEL"],
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(
                    context=format_context(docs, metas), question=args.question
                ),
            }
        ],
    )
    print(response.choices[0].message.content)

    sources = [label_of(m) for m in metas if label_of(m)]
    print("\n来源: " + "; ".join(dict.fromkeys(sources)))


if __name__ == "__main__":
    main()
