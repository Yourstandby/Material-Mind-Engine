"""检索 + LLM 生成：各前端共用的核心查询引擎。

用法: from core.query_engine import query
      answer, sources = query("PEEK 的耐温范围", "peek")
"""
import os

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PERSIST = os.environ.get("CHROMA_PATH", "./chroma_db")
TEMP_KEYWORDS = ("耐温", "温度", "temperature")
TEMP_EXPANSION = "temperature range operating temperature -20 230 °C"

PROMPT = "你是一个材料领域专家。基于以下检索到的数据回答问题。\n规则：\n0. 如果任何条目含具体数值，必须直接以数值开头，禁止以“未能找到”“资料不足”“无法确认”开头\n1. 优先采信包含具体数值（温度、压力、百分比）的条目\n2. 如果多条数据冲突，先列出最可信的一条（confidence 最高或来源最权威），再注明“另有来源给出不同值”\n3. 牌号前缀 X 可忽略（X6055B 与 6055B 视为同一牌号）\n4. 回答末尾必须列出实际引用的来源，引用时使用条目编号如 [1][2]\n5. 只有在所有检索结果都没有相关数值时，才回答“资料不足”\n资料:\n{context}\n问题: {question}"


def expand_query(question):
    if any(keyword in question for keyword in TEMP_KEYWORDS):
        return f"{question} {TEMP_EXPANSION}"
    return question


def label_of(meta):
    return (meta or {}).get("grade") or (meta or {}).get("source")


def build_url(meta):
    value = str((meta or {}).get("source") or "")
    return value if value.startswith("http") else "—"


def format_context(docs, metas):
    return "\n\n".join(
        f"[{i}] {label_of(m) or 'unknown'} | {build_url(m)}\n{d}"
        for i, (d, m) in enumerate(zip(docs, metas), start=1)
    )


def source_lines(metas):
    return [
        f"[{i}] {label_of(m) or 'unknown'} | {build_url(m)}"
        for i, m in enumerate(metas, start=1)
    ]


def query(question: str, collection: str = "materials", top_k: int = 10) -> tuple:
    client = chromadb.PersistentClient(path=PERSIST)
    result = client.get_or_create_collection(collection).query(
        query_texts=[expand_query(question)], n_results=top_k
    )
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    if not docs:
        return "未检索到相关数据", []

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
                    context=format_context(docs, metas), question=question
                ),
            }
        ],
    )
    return response.choices[0].message.content, source_lines(metas)
