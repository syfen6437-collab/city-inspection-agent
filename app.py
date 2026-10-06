from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from city_agent.document_parser import parse_document_bytes
from city_agent.extractor import extract_report
from city_agent.index import NgramIndex
from city_agent.local_model import LocalModel, ModelConfig, ModelUnavailable
from city_agent.pipeline import prepare_corpus
from city_agent.qa import answer_question
from city_agent.render import render_prediction_docx


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
DEFAULT_SOURCE = Path(os.getenv("CITY_AGENT_DATASET", r"E:\6000RMB\赛题一城市基础设施定检报告问答分析.zip"))

st.set_page_config(page_title="城市定检报告智能体", page_icon="🏗️", layout="wide")
st.markdown("""
<style>
:root { --ink:#18231e; --green:#176b49; --line:#d8e2dc; --mint:#edf5f0; }
.stApp { background:#fbfcfb; color:var(--ink); }
[data-testid="stSidebar"] { background:#f0f5f2; border-right:1px solid var(--line); }
.block-container { max-width:1320px; padding-top:1.5rem; }
h1,h2,h3 { letter-spacing:0 !important; color:var(--ink); }
.status { border-left:3px solid var(--green); background:var(--mint); padding:.7rem 1rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def get_model() -> LocalModel:
    return LocalModel(ModelConfig.from_env()).load()


def model_or_error() -> tuple[LocalModel | None, str]:
    try:
        return get_model(), ""
    except Exception as exc:
        return None, str(exc)


with st.sidebar:
    st.markdown("## 城市定检报告智能体")
    page = st.radio("功能", ["数据源与索引", "单报告抽取", "跨报告问答", "结果与合规"], label_visibility="collapsed")
    st.divider()
    st.caption(f"数据集：{'已找到' if DEFAULT_SOURCE.exists() else '未找到'}")
    st.caption(f"模型目录：{os.getenv('CITY_AGENT_MODEL_PATH', 'models/Qwen3-8B')}")
    st.caption("预测只使用本地开源模型，不调用外部 API。")

if page == "数据源与索引":
    st.title("数据源与索引")
    st.markdown('<div class="status">原始 ZIP 只读使用，索引和解析缓存写入本地 cache，不进入提交包。</div>', unsafe_allow_html=True)
    source_text = st.text_input("训练/测试数据 ZIP 或目录", str(DEFAULT_SOURCE))
    split = st.selectbox("数据分区", ["test", "train", "labels"], format_func=lambda value: {"test": "初赛测试集", "train": "训练原文", "labels": "训练标签"}[value])
    limit = st.number_input("最多解析文件数（0 表示全部）", min_value=0, value=0, step=1)
    if st.button("准备解析与索引", type="primary"):
        with st.spinner("正在读取文档并建立中文检索索引"):
            reports, index = prepare_corpus(source_text, CACHE, split, int(limit) or None)
        st.success(f"已解析 {len(reports)} 份文档、{len(index.blocks)} 个证据片段")
    manifest = CACHE / f"reports_{split}.json"
    if manifest.exists():
        st.info(f"当前缓存：{manifest}")

elif page == "单报告抽取":
    st.title("单报告结构化抽取")
    upload = st.file_uploader("上传 .docx 或 .doc", type=["docx", "doc"])
    if upload is not None:
        report = parse_document_bytes(upload.getvalue(), upload.name)
        st.caption(f"报告 ID：{report.report_id}，片段数：{len(report.blocks)}")
        model, error = model_or_error()
        if error:
            st.warning(error)
        if st.button("本地模型抽取", type="primary", disabled=model is None):
            record = extract_report(report, model)
            st.session_state["last_record"] = record
            if record.prediction:
                st.json(record.prediction.to_dict())
            else:
                st.error(record.error)
        record = st.session_state.get("last_record")
        if record:
            output = ROOT / "result" / f"{Path(record.file_name).stem}_交互结果.docx"
            render_prediction_docx(record, output)
            st.download_button("下载结果 DOCX", output.read_bytes(), output.name)

elif page == "跨报告问答":
    st.title("跨报告问答")
    index_path = CACHE / "index_test.json"
    if not index_path.exists():
        st.info("请先在“数据源与索引”中准备测试集索引。")
    else:
        index = NgramIndex.load(index_path)
        question = st.text_area("问题", placeholder="例如：哪些桥梁近年的裂缝病害发展较快？")
        model, error = model_or_error()
        if error:
            st.warning(error)
        if st.button("检索并回答", type="primary", disabled=model is None or not question.strip()):
            result = answer_question(question, index, model)
            st.session_state["last_qa"] = result
        result = st.session_state.get("last_qa")
        if result:
            st.subheader("答案")
            st.write(result.get("answer"))
            st.caption(f"状态：{result.get('status')}，置信度：{result.get('confidence')}")
            st.subheader("证据链")
            st.dataframe(result.get("evidence", []), width="stretch", hide_index=True)

else:
    st.title("结果与合规")
    st.write("结果目录只允许保存模型生成结果、证据定位、状态和审计信息。")
    for name in ("predictions.v1.json", "manifest.json", "audit.jsonl"):
        path = ROOT / "result" / name
        if path.exists():
            st.download_button(f"下载 {name}", path.read_bytes(), name)
    st.code("python scripts/predict.py --source <dataset.zip> --split test", language="powershell")
    st.code("python scripts/package.py --output city_inspection_agent.tar.gz", language="powershell")

