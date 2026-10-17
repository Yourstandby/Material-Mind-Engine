"""Gradio 前端（界面逻辑，回答与来源由 core.query_engine 提供）。

用法: python ui/gradio_app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gradio as gr

from core.query_engine import query

COLLECTIONS = ["materials", "fkm", "peek"]

CSS = """
.gradio-container { background: #f8fafc; }
.card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px;
        padding: 12px; margin-bottom: 24px; }
#head h1 { font-size: 28px; font-weight: 600; margin: 0 0 4px; }
#head p { font-size: 14px; color: #666; margin: 0; }
#answer textarea { font-size: 15px; line-height: 1.7; }
#submit-btn { background: #2563eb; color: #ffffff; border: none; border-radius: 8px; }
#submit-btn:hover { background: #1d4ed8; }
"""

LOADING_JS = (
    "() => {const b=document.querySelector('#submit-btn button');"
    "if (b) b.textContent='查询中...';}"
)
IDLE_JS = (
    "() => {const b=document.querySelector('#submit-btn button');"
    "if (b) b.textContent='查询';}"
)


def answer(question, collection):
    if not question.strip():
        return "请输入问题", ""
    text, sources = query(question, collection)
    return text, "\n".join(sources)


def main():
    with gr.Blocks(theme=gr.themes.Soft(), css=CSS, title="Material-Mind-Engine") as demo:
        with gr.Column(elem_id="head"):
            gr.Markdown("# Material-Mind-Engine")
            gr.Markdown("材料数据问答 · 回答附编号来源，可追溯至原始文档。")

        with gr.Column(elem_classes="card"):
            question = gr.Textbox(
                label="问题", lines=2, placeholder="例如：PEEK 的耐温范围是多少？"
            )
            collection = gr.Dropdown(COLLECTIONS, value="materials", label="collection")
            submit = gr.Button("查询", variant="primary", elem_id="submit-btn")

        with gr.Column(elem_classes="card"):
            answer_box = gr.Textbox(label="回答", lines=12, elem_id="answer")

        with gr.Accordion("查看来源", open=False):
            source_box = gr.Textbox(lines=8, show_label=False)

        submit.click(
            answer, [question, collection], [answer_box, source_box], js=LOADING_JS
        ).then(lambda: None, js=IDLE_JS)
        question.submit(
            answer, [question, collection], [answer_box, source_box], js=LOADING_JS
        ).then(lambda: None, js=IDLE_JS)

    demo.queue(False)
    demo.launch()


if __name__ == "__main__":
    main()
