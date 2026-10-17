"""Streamlit 前端：材质信息从 profiles/*.yaml 自动扫描，问答由 core.query_engine 提供。

用法: streamlit run ui/streamlit_app.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
import yaml

from core.query_engine import query

PROFILES_DIR = ROOT / "profiles"


def load_profiles():
    items = []
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        stem = path.stem
        items.append(
            {
                "stem": stem,
                "collection": data.get("collection") or stem,
                "display_name": data.get("display_name") or stem,
                "description": data.get("description") or "",
            }
        )
    return items


def main():
    st.set_page_config(page_title="Material-Mind-Engine", layout="centered")
    st.title("Material-Mind-Engine")
    st.caption("材料数据问答 · 回答附编号来源，可追溯至原始文档。")

    profiles = load_profiles()
    if not profiles:
        st.error("未找到任何 profile：profiles/*.yaml")
        return

    labels = [f"{p['display_name']} ({p['collection']})" for p in profiles]
    index = st.selectbox("材质", range(len(profiles)), format_func=lambda i: labels[i])
    selected = profiles[index]
    if selected["description"]:
        st.caption(selected["description"])

    question = st.text_input("问题", placeholder="例如：PEEK 的耐温范围是多少？")
    if st.button("查询", type="primary"):
        if not question.strip():
            st.warning("请输入问题")
            return
        with st.spinner("查询中..."):
            answer, sources = query(question, selected["collection"])
        st.markdown(answer)
        with st.expander("查看来源", expanded=False):
            for line in sources or ["无来源"]:
                st.markdown(line)


if __name__ == "__main__":
    main()
