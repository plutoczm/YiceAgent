from frontend.components.api_client import get_client
from frontend.components.streamlit_compat import st


NAV_ITEMS = {
    "智能问答": "pages/chat.py",
    "知识库": "pages/knowledge_base.py",
    "Text2SQL": "pages/text2sql.py",
    "SQL 审查": "pages/sql_review.py",
    "数仓设计": "pages/warehouse_design.py",
}


def render() -> None:
    st.sidebar.title("DataPilot-AI")
    st.sidebar.caption("数据工程 AI 工作台")
    try:
        health = get_client().health()
        status = health.get("status", "unknown")
        st.sidebar.success(f"后端：{status}")
    except Exception as exc:
        st.sidebar.error(f"后端不可用：{exc}")
    st.sidebar.divider()
    st.sidebar.caption("工具")
    for label in NAV_ITEMS:
        st.sidebar.write(label)
