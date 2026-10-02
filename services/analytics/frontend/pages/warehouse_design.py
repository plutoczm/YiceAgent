import json
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


def render() -> None:
    apply_theme()
    hero("数仓设计", "根据业务需求生成 ODS、DWD、DWS、ADS 分层方案、指标定义和 Hive DDL。")
    card_grid(
        [
            ("分层建模", "自动组织明细层、汇总层和应用层。"),
            ("指标口径", "沉淀指标定义、计算逻辑和业务含义。"),
            ("DDL 输出", "生成可复制的建表 SQL，便于继续落地。"),
        ]
    )

    selected = quick_actions(
        "一键示例",
        [
            {
                "tag": "电商订单主题",
                "title": "订单履约分析",
                "description": "覆盖下单、支付、退款、发货和签收链路。",
                "button_label": "填入示例",
                "requirement": "设计电商订单分析数仓，覆盖下单、支付、退款和履约分析",
            },
            {
                "tag": "用户行为主题",
                "title": "行为漏斗分析",
                "description": "覆盖浏览、加购、收藏、下单和支付转化。",
                "button_label": "填入示例",
                "requirement": "围绕用户行为日志设计实时与离线结合的分析数仓",
            },
        ],
        key_prefix="warehouse-design-demo",
    )
    if selected:
        st.session_state["warehouse_design_requirement"] = selected.get("requirement", "")
        record_activity("数仓设计", f"加载{selected.get('tag', '示例')}示例")

    st.session_state.setdefault("warehouse_design_requirement", "设计电商订单分析数仓")
    requirement = st.text_area("业务需求", key="warehouse_design_requirement", height=120)
    option_columns = st.columns(2)
    with option_columns[0]:
        use_rag = st.checkbox("使用知识库上下文", value=True)
    with option_columns[1]:
        recommendation_language = st.radio(
            "建议语言",
            options=["中文", "English"],
            horizontal=True,
            key="warehouse_recommendation_language",
        )

    if not st.button("生成数仓方案", type="primary"):
        return

    try:
        language_code = "zh-CN" if recommendation_language == "中文" else "en"
        result = get_client().warehouse_design(
            requirement,
            use_rag=use_rag,
            recommendation_language=language_code,
        )
        st.success("数仓方案已生成")
        st.download_button(
            "导出结果",
            data=json.dumps(result, ensure_ascii=False, indent=2),
            file_name="warehouse_design.json",
            mime="application/json",
        )

        layer_tabs = st.tabs(["ODS", "DWD", "DWS", "ADS", "指标", "DDL", "建议"])
        for tab, key in zip(layer_tabs[:4], ["ods", "dwd", "dws", "ads"], strict=True):
            with tab:
                _render_tables(result.get(key, []))

        with layer_tabs[4]:
            for metric in result.get("metrics", []):
                with st.expander(metric.get("name", "Metric")):
                    st.write(metric.get("definition", ""))
                    st.code(metric.get("calculation_logic", ""), language="sql")
                    st.caption(metric.get("business_meaning", ""))

        with layer_tabs[5]:
            for ddl in result.get("ddl", []):
                st.caption(ddl.get("table_name", "table"))
                st.code(ddl.get("sql", ""), language="sql")

        with layer_tabs[6]:
            for recommendation in result.get("recommendations", []):
                st.write(f"- {recommendation}")
        record_activity("数仓设计", f"生成方案：{requirement[:28]}")
    except Exception as exc:
        st.error(f"数仓设计失败：{exc}")


def _render_tables(tables: list[dict]) -> None:
    for table in tables:
        with st.expander(table.get("name", "表"), expanded=True):
            st.write(table.get("description", ""))
            st.json(table.get("columns", []))


if __name__ == "__main__":
    render()
