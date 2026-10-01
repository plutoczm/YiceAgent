from pathlib import Path
import sys
import uuid


def _bootstrap_project_root() -> None:
    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_bootstrap_project_root()
import frontend.bootstrap  # noqa: E402,F401

from frontend.components.api_client import get_client
from frontend.components.chat_message import render_citations, render_message
from frontend.components.streamlit_compat import st
from frontend.components.theme import apply_theme, card_grid, hero, quick_actions, record_activity


def render() -> None:
    apply_theme()
    hero("智能问答", "用自然语言触发 RAG、Text2SQL、SQL 审查和数仓设计，让数据工程任务从一个入口开始。")
    card_grid(
        [
            ("自动意图识别", "根据问题内容选择最合适的工具链。"),
            ("流式响应", "回答生成过程中实时展示意图和路由路径。"),
            ("结构化结果", "SQL、审查分数、引用来源和数仓层级可直接查看。"),
        ]
    )

    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("agent_session_id", str(uuid.uuid4()))
    selected = quick_actions(
        "演示问题",
        [
            {
                "tag": "知识问答",
                "title": "什么是 Spark AQE？",
                "description": "检索 Spark 优化知识并生成可引用解释。",
                "button_label": "使用该问题",
                "value": "什么是 Spark AQE？它能解决哪些查询性能问题？",
            },
            {
                "tag": "SQL 工作流",
                "title": "统计最近7天活跃用户并检查 SQL",
                "description": "让智能体先生成 SQL，再自动审查风险。",
                "button_label": "使用该问题",
                "value": "统计最近7天活跃用户并检查 SQL",
            },
            {
                "tag": "数仓建模",
                "title": "请帮我设计电商订单分析数仓",
                "description": "生成 ODS、DWD、DWS、ADS 分层方案。",
                "button_label": "使用该问题",
                "value": "请帮我设计电商订单分析数仓",
            },
        ],
        key_prefix="chat-demo",
    )
    if selected:
        st.session_state["chat_prompt_seed"] = selected.get("value", "")
        st.session_state["chat_prompt_pending"] = True
        record_activity("智能问答", f"加载演示问题：{selected.get('title', '-')}")

    with st.expander("上下文设置", expanded=False):
        user_id = st.text_input(
            "用户 ID",
            value="demo-user",
            help="用于跨会话长期记忆和规则记忆；生产环境应由认证系统提供。",
        )
        collection_name = st.text_input("知识库集合", value="knowledge_base")
        top_k = st.slider("Top K", min_value=1, max_value=20, value=5)
        engine = st.selectbox("SQL 引擎", ["hive", "spark_sql", "mysql", "clickhouse"])
        schema_context = st.text_area("表结构上下文", height=120)
        use_rag = st.checkbox("专业工具启用 RAG 上下文", value=False)
        retrieval_mode = st.segmented_control(
            "检索模式",
            options=["hybrid", "vector"],
            default="hybrid",
        )

    for item in st.session_state["chat_history"]:
        render_message(item["role"], item["content"])
        render_citations(item.get("citations", []))
        _render_agent_result(item.get("result"))

    prompt = st.chat_input("向 DataPilot-AI 提问，例如：统计最近7天活跃用户并检查SQL")
    if not prompt and st.session_state.pop("chat_prompt_pending", False):
        prompt = st.session_state.get("chat_prompt_seed", "")
    if not prompt:
        return

    st.session_state["chat_history"].append({"role": "user", "content": prompt})
    render_message("user", prompt)

    answer = ""
    citations = []
    result = None
    with st.chat_message("assistant"):
        placeholder = st.empty()
        try:
            for event in get_client().stream_agent_chat(
                prompt,
                engine=engine,
                schema_context=schema_context or None,
                collection_name=collection_name,
                top_k=top_k,
                use_rag=use_rag,
                session_id=st.session_state["agent_session_id"],
                user_id=user_id or None,
                retrieval_mode=retrieval_mode or "hybrid",
            ):
                if event["event"] == "metadata":
                    data = event["data"]
                    st.caption(
                        "意图："
                        f"{data.get('intent', '-')}"
                        " | 路由："
                        f"{' -> '.join(data.get('routing_path', []))}"
                    )
                if event["event"] == "token":
                    answer += event["data"].get("text", "")
                    placeholder.markdown(answer)
                elif event["event"] == "result":
                    result = event["data"].get("result")
                    citations = _extract_citations(result)
            render_citations(citations)
            _render_agent_result(result)
            st.session_state["chat_history"].append(
                {
                    "role": "assistant",
                    "content": answer,
                    "citations": citations,
                    "result": result,
                }
            )
            record_activity("智能问答", f"完成问答：{prompt[:28]}")
        except Exception as exc:
            st.error(f"流式问答失败：{exc}")


def _extract_provider(result) -> str | None:
    """从 agent result 中提取本次请求的服务 LLM（llm_provider / llm_model）。"""
    if not isinstance(result, dict):
        return None
    for candidate in (result, result.get("generated_sql"), result.get("review"), result.get("rag")):
        if not isinstance(candidate, dict):
            continue
        metadata = candidate.get("metadata")
        if isinstance(metadata, dict) and metadata.get("llm_provider"):
            provider = metadata["llm_provider"]
            model = metadata.get("llm_model", "")
            return f"{provider}{f'（{model}）' if model else ''}"
    return None


def _extract_citations(result):
    if not isinstance(result, dict):
        return []
    if isinstance(result.get("citations"), list):
        return result["citations"]
    if isinstance(result.get("rag"), dict):
        return result["rag"].get("citations", [])
    return []


def _render_agent_result(result) -> None:
    if not isinstance(result, dict):
        return
    provider = _extract_provider(result)
    if provider:
        st.caption(f"服务模型：{provider}")
    if "sql" in result:
        st.code(result.get("sql", ""), language="sql")
    generated_sql = result.get("generated_sql")
    if isinstance(generated_sql, dict):
        st.code(generated_sql.get("sql", ""), language="sql")
    review = result.get("review") if isinstance(result.get("review"), dict) else result
    if isinstance(review, dict) and "risk_level" in review:
        st.caption(
            f"风险：{review.get('risk_level', '-')} | 分数：{review.get('score', '-')}/100"
        )
    if "ods" in result and "dwd" in result:
        st.caption(
            "数仓层级："
            f"ODS {len(result.get('ods', []))}, "
            f"DWD {len(result.get('dwd', []))}, "
            f"DWS {len(result.get('dws', []))}, "
            f"ADS {len(result.get('ads', []))}"
        )


if __name__ == "__main__":
    render()
