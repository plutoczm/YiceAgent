from pathlib import Path
import sys


def _bootstrap_project_root() -> None:
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_bootstrap_project_root()
import frontend.bootstrap  # noqa: E402,F401

from frontend.components.api_client import get_client
from frontend.components.streamlit_compat import st
from frontend.components.theme import (
    apply_theme,
    card_grid,
    hero,
    quick_actions,
    recent_activity,
    record_activity,
    status_badge,
)


def render() -> None:
    st.set_page_config(
        page_title="DataPilot-AI 数据工程工作台",
        page_icon="DC",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme()

    hero(
        "数据工程 AI 工作台",
        "把知识库问答、Text2SQL、SQL 审查和数仓设计放在一个可演示、可扩展的智能平台里。",
    )

    try:
        health = get_client().health()
        status = health.get("status", "unknown")
        environment = health.get("environment", "-")
        vector_store = health.get("vector_store", {})
        llm_provider = health.get("llm_provider", {})
        st.subheader("系统状态")
        status_badge("后端服务正常" if status == "ok" else f"后端状态：{status}")
        col1, col2, col3 = st.columns(3)
        col1.metric("服务", health.get("service", "DataPilot-AI"))
        col2.metric("运行环境", environment)
        col3.metric("知识块", vector_store.get("document_count", 0))
        st.caption(
            "LLM: "
            f"{llm_provider.get('provider', '-')}"
            " | 模型: "
            f"{llm_provider.get('model', '-')}"
        )
    except Exception as exc:
        st.error(f"后端健康检查失败：{exc}")

    st.subheader("工作区")
    card_grid(
        [
            ("智能问答", "自动识别意图，路由到知识库、SQL 生成、SQL 审查或数仓设计。"),
            ("知识库", "上传技术文档，构建可引用的 RAG 问答能力。"),
            ("Text2SQL", "把中文业务口径转成 Hive、Spark SQL、MySQL 或 ClickHouse 查询。"),
            ("SQL 审查", "识别性能风险、规范问题和优化建议。"),
            ("数仓设计", "生成 ODS、DWD、DWS、ADS 分层方案、指标和 DDL。"),
        ]
    )

    selected = quick_actions(
        "演示任务",
        [
            {
                "tag": "知识问答",
                "title": "什么是 Spark AQE？",
                "description": "加载一个适合知识库问答的 Spark 优化问题。",
                "button_label": "发送到智能问答",
                "target": "chat",
                "value": "什么是 Spark AQE？它能解决哪些查询性能问题？",
            },
            {
                "tag": "SQL 工作流",
                "title": "统计最近7天活跃用户并检查 SQL",
                "description": "准备一个 Text2SQL 到 SQL 审查的组合任务。",
                "button_label": "填入 Text2SQL",
                "target": "text2sql",
                "value": "按天统计最近7天活跃用户数，并按省份输出 Top 10",
            },
            {
                "tag": "数仓建模",
                "title": "设计电商订单分析数仓",
                "description": "准备一个 ODS/DWD/DWS/ADS 分层建模任务。",
                "button_label": "填入数仓设计",
                "target": "warehouse",
                "value": "设计电商订单分析数仓，覆盖下单、支付、退款和履约分析",
            },
        ],
        key_prefix="home-demo",
    )
    if selected:
        _apply_home_demo(selected)

    recent_activity("最近操作")


def main() -> None:
    pages = [
        st.Page(render, title="首页", icon=":material/dashboard:", default=True),
        st.Page("pages/chat.py", title="智能问答", icon=":material/forum:", url_path="chat"),
        st.Page(
            "pages/knowledge_base.py",
            title="知识库",
            icon=":material/library_books:",
            url_path="knowledge",
        ),
        st.Page("pages/text2sql.py", title="Text2SQL", icon=":material/code:", url_path="text2sql"),
        st.Page("pages/sql_review.py", title="SQL 审查", icon=":material/rule:", url_path="sql-review"),
        st.Page(
            "pages/warehouse_design.py",
            title="数仓设计",
            icon=":material/schema:",
            url_path="warehouse-design",
        ),
    ]
    navigation = st.navigation(pages)
    navigation.run()


def _apply_home_demo(action: dict[str, str]) -> None:
    target = action.get("target")
    value = action.get("value", "")
    if target == "chat":
        st.session_state["chat_prompt_seed"] = value
        st.session_state["chat_prompt_pending"] = True
        record_activity("智能问答", "加载 Spark AQE 演示问题")
    elif target == "text2sql":
        st.session_state["text2sql_question"] = value
        st.session_state["text2sql_schema_context"] = (
            "dwd_user_behavior_detail(user_id bigint, event_time timestamp, province string, "
            "event_name string, dt string)\n"
            "dim_user(user_id bigint, user_level string, province string)"
        )
        record_activity("Text2SQL", "加载活跃用户分析演示")
    elif target == "warehouse":
        st.session_state["warehouse_design_requirement"] = value
        record_activity("数仓设计", "加载电商订单主题演示")


if __name__ == "__main__":
    main()
