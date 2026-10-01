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
from frontend.components.theme import apply_theme, card_grid, hero, quick_actions, record_activity


ENGINE_OPTIONS = {
    "Spark SQL": "spark",
    "Hive": "hive",
    "MySQL": "mysql",
    "ClickHouse": "clickhouse",
}


def render() -> None:
    apply_theme()
    hero("SQL 审查", "结合规则引擎和 LLM 解释，快速发现 SQL 风险、性能问题和优化空间。")
    card_grid(
        [
            ("风险等级", "将 SQL 质量转化为可比较的风险评分。"),
            ("规则检查", "识别全表扫描、分区缺失、SELECT * 等常见问题。"),
            ("优化建议", "给出可执行的改写和调优方向。"),
        ]
    )

    selected = quick_actions(
        "一键示例",
        [
            {
                "tag": "Spark 分区缺失",
                "title": "宽表全量扫描",
                "description": "包含 SELECT * 和时间字段过滤，适合演示分区裁剪建议。",
                "button_label": "填入示例",
                "sql": "SELECT * FROM dwd_order_detail WHERE create_time >= '2026-06-01'",
            },
            {
                "tag": "SELECT 星号风险",
                "title": "聚合前未裁剪字段",
                "description": "展示字段裁剪、分区过滤和聚合性能建议。",
                "button_label": "填入示例",
                "sql": (
                    "SELECT *\n"
                    "FROM dwd_user_behavior_detail\n"
                    "WHERE event_name IN ('view', 'pay')"
                ),
            },
        ],
        key_prefix="sql-review-demo",
    )
    if selected:
        st.session_state["sql_review_sql"] = selected.get("sql", "")
        record_activity("SQL 审查", f"加载{selected.get('tag', '示例')}示例")

    st.session_state.setdefault(
        "sql_review_sql",
        "SELECT * FROM dwd_order_detail WHERE create_time >= '2026-06-01'",
    )
    sql = st.text_area(
        "SQL",
        key="sql_review_sql",
        height=260,
    )
    engine_label = st.selectbox("SQL 引擎", list(ENGINE_OPTIONS.keys()))
    include_llm = st.checkbox("生成 LLM 解释", value=True)

    if not st.button("审查 SQL", type="primary"):
        return

    try:
        result = get_client().sql_review(
            sql,
            ENGINE_OPTIONS[engine_label],
            include_llm_explanation=include_llm,
        )
        col1, col2 = st.columns(2)
        col1.metric("风险等级", result.get("risk_level", "-"))
        col2.metric("质量分数", result.get("score", 0))

        st.subheader("问题列表")
        for issue in result.get("issues", []):
            with st.expander(f"{issue.get('severity')} - {issue.get('title')}"):
                st.write(issue.get("description", ""))
                st.info(issue.get("suggestion", ""))

        st.subheader("优化建议")
        for suggestion in result.get("optimization_suggestions", []):
            st.write(f"- {suggestion}")

        st.subheader("LLM 解释")
        st.write(result.get("llm_explanation", ""))
        record_activity("SQL 审查", f"完成 SQL 审查：{result.get('risk_level', '-')}")
    except Exception as exc:
        st.error(f"SQL 审查失败：{exc}")


if __name__ == "__main__":
    render()
