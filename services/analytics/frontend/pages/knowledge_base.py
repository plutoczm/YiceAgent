from pathlib import Path
import sys


def _bootstrap_project_root() -> None:
    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_bootstrap_project_root()
import frontend.bootstrap  # noqa: E402,F401

from frontend.components.api_client import get_client
from frontend.components.streamlit_compat import st
from frontend.components.theme import apply_theme, card_grid, hero


def render() -> None:
    apply_theme()
    hero("知识库", "上传数据工程文档，构建可追溯引用的 RAG 知识问答能力。")
    card_grid(
        [
            ("上传文档", "支持 PDF、DOCX、TXT 和 Markdown。"),
            ("文档列表", "查看已索引文档、领域标签和删除入口。"),
            ("知识问答", "基于向量检索和 LLM 生成带引用的答案。"),
        ]
    )

    upload_tab, documents_tab, query_tab = st.tabs(["上传文档", "文档列表", "知识问答"])

    with upload_tab:
        collection_name = st.text_input("知识库集合", value="knowledge_base", key="upload_collection")
        domain = st.text_input("业务领域", value="general")
        tags = st.text_input("标签", value="")
        uploaded_file = st.file_uploader(
            "上传 PDF、DOCX、TXT 或 Markdown",
            type=["pdf", "docx", "txt", "md", "markdown"],
        )
        if st.button("索引文档", type="primary") and uploaded_file is not None:
            try:
                result = get_client().upload_document(
                    file_name=uploaded_file.name,
                    file_bytes=uploaded_file.getvalue(),
                    collection_name=collection_name,
                    domain=domain,
                    tags=tags,
                )
                st.success(f"已索引：{result.get('filename', uploaded_file.name)}")
                st.json(result)
            except Exception as exc:
                st.error(f"上传失败：{exc}")

    with documents_tab:
        if st.button("刷新文档列表"):
            st.session_state["documents_cache"] = get_client().list_documents()
        documents = st.session_state.get("documents_cache")
        if documents is None:
            try:
                documents = get_client().list_documents()
            except Exception as exc:
                st.error(f"加载文档失败：{exc}")
                documents = {"documents": []}
        for document in documents.get("documents", []):
            col1, col2, col3 = st.columns([4, 2, 1])
            col1.write(f"**{document.get('filename')}**")
            col2.write(document.get("domain", "general"))
            if col3.button("删除", key=f"delete-{document.get('document_id')}"):
                try:
                    get_client().delete_document(document["document_id"])
                    st.success("已删除")
                except Exception as exc:
                    st.error(f"删除失败：{exc}")

    with query_tab:
        question = st.text_area("问题", value="Spark AQE 如何帮助优化查询？")
        collection = st.text_input("知识库集合", value="knowledge_base", key="query_collection")
        top_k = st.slider("Top K", min_value=1, max_value=20, value=5, key="query_top_k")
        if st.button("运行知识问答", type="primary"):
            try:
                result = get_client().query_knowledge(question, collection_name=collection, top_k=top_k)
                st.subheader("回答")
                st.write(result.get("answer", ""))
                st.subheader("引用来源")
                st.json(result.get("citations", []))
            except Exception as exc:
                st.error(f"查询失败：{exc}")


if __name__ == "__main__":
    render()
