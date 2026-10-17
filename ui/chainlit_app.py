"""Chainlit 前端。

用法: chainlit run ui/chainlit_app.py -w
"""
import chainlit as cl

from core.query_engine import query

COLLECTIONS = ["materials", "fkm", "peek"]


@cl.on_chat_start
async def on_chat_start():
    await cl.Message(
        content="# Material-Mind-Engine\n材料数据问答 · 回答附编号来源，可追溯至原始文档。"
    ).send()
    actions = [cl.Action(name="pick", value=name, label=name) for name in COLLECTIONS]
    response = await cl.AskActionMessage(
        content="请选择 collection：", actions=actions, timeout=300
    ).send()
    collection = (response or {}).get("value") or "materials"
    if collection not in COLLECTIONS:
        collection = "materials"
    cl.user_session.set("collection", collection)
    await cl.Message(content=f"已选择 `{collection}`，直接提问即可。").send()


@cl.on_message
async def on_message(message: cl.Message):
    collection = cl.user_session.get("collection") or "materials"
    answering = cl.Message(content="查询中...")
    await answering.send()

    answer, sources = query(message.content, collection)
    answering.content = answer
    await answering.update()

    elements = []
    for index, line in enumerate(sources):
        url = line.split(" | ", 1)[-1]
        if url.startswith("http"):
            elements.append(cl.Text(name=f"[{index + 1}]", content=url, display="side"))
    if elements:
        await cl.Message(content="来源（点击展开）", elements=elements).send()

    await cl.Message(
        content="如需继续追问，或切换到其他 collection，请重新发消息。"
    ).send()
