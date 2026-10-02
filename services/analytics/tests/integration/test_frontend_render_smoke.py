from types import SimpleNamespace

from frontend.components import chat_message, sidebar, theme
from frontend.pages import chat, knowledge_base, sql_review, text2sql, warehouse_design


class RenderStreamlit:
    def __init__(self) -> None:
        self.session_state = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        if name == "sidebar":
            return self

        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in {"button", "checkbox"}:
                return True
            if name == "chat_input":
                return "统计最近7天活跃用户并检查SQL"
            if name == "selectbox":
                options = args[1] if len(args) > 1 else []
                return options[0] if options else ""
            if name in {"text_area", "text_input"}:
                key = kwargs.get("key")
                if key:
                    if key not in self.session_state:
                        self.session_state[key] = kwargs.get("value") or (
                            args[1] if len(args) > 1 else args[0] if args else ""
                        )
                    return self.session_state[key]
                return kwargs.get("value") or (args[1] if len(args) > 1 else args[0] if args else "")
            if name == "slider":
                return kwargs.get("value", 5)
            if name == "file_uploader":
                return SimpleNamespace(name="spark.txt", getvalue=lambda: b"Spark AQE")
            if name == "columns":
                count = args[0] if args else 1
                if isinstance(count, int):
                    return [self for _ in range(count)]
                return [self for _ in count]
            if name == "tabs":
                return [self for _ in (args[0] if args else [])]
            if name in {"container", "form", "expander", "spinner", "chat_message", "empty"}:
                return self
            return None

        return _call

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class RenderClient:
    def health(self):
        return {"status": "ok"}

    def list_documents(self):
        return {
            "documents": [
                {
                    "document_id": "doc-1",
                    "filename": "spark.txt",
                    "domain": "spark",
                    "collection_name": "knowledge_base",
                    "chunk_count": 1,
                }
            ]
        }

    def upload_document(self, **kwargs):
        return {"document_id": "doc-1", "filename": kwargs["file_name"], "chunk_count": 1}

    def delete_document(self, document_id):
        return {"deleted": True}

    def query_knowledge(self, *args, **kwargs):
        return {"answer": "Spark AQE answer", "citations": [{"document_name": "spark.txt"}]}

    def text2sql(self, *args, **kwargs):
        return {
            "sql": "SELECT COUNT(*) FROM dwd_user_behavior_detail",
            "explanation": "Count rows.",
            "validation": {"is_valid": True},
            "optimization_suggestions": ["Use partitions."],
        }

    def sql_review(self, *args, **kwargs):
        return {
            "risk_level": "LOW",
            "score": 94,
            "issues": [],
            "optimization_suggestions": ["Use partitions."],
            "llm_explanation": "Looks fine.",
        }

    def warehouse_design(self, *args, **kwargs):
        return {
            "ods": [{"name": "ods_order_raw", "columns": []}],
            "dwd": [{"name": "dwd_order_detail", "columns": []}],
            "dws": [{"name": "dws_order_day", "columns": []}],
            "ads": [{"name": "ads_order_dashboard", "columns": []}],
            "metrics": [
                {
                    "name": "GMV",
                    "definition": "Gross merchandise volume",
                    "calculation_logic": "SUM(pay_amount)",
                    "business_meaning": "Sales scale",
                }
            ],
            "ddl": [{"table_name": "dwd_order_detail", "sql": "CREATE TABLE dwd_order_detail(id bigint)"}],
            "recommendations": ["Partition by dt."],
        }

    def stream_agent_chat(self, *args, **kwargs):
        yield {
            "event": "metadata",
            "data": {
                "intent": "TEXT2SQL_SQL_REVIEW",
                "routing_path": ["classify_intent", "planner", "text2sql", "sql_review", "format_response"],
            },
        }
        yield {"event": "token", "data": {"text": "Generated SQL"}}
        yield {
            "event": "result",
            "data": {
                "result": {
                    "generated_sql": {"sql": "SELECT 1"},
                    "review": {"risk_level": "LOW", "score": 94},
                }
            },
        }
        yield {"event": "done", "data": {"status": "complete"}}


def test_streamlit_pages_render_with_backend_payloads(monkeypatch) -> None:
    fake_st = RenderStreamlit()
    fake_client = RenderClient()
    modules = [chat, knowledge_base, text2sql, sql_review, warehouse_design]
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "get_client", lambda: fake_client)
    monkeypatch.setattr(chat_message, "st", fake_st)
    monkeypatch.setattr(theme, "st", fake_st)

    for module in modules:
        monkeypatch.setattr(module, "st", fake_st)
        monkeypatch.setattr(module, "get_client", lambda: fake_client)
        module.render()

    call_names = [name for name, _, _ in fake_st.calls]
    assert "code" in call_names
    assert "json" in call_names
    assert "download_button" in call_names
    assert "metric" in call_names


def test_guided_demo_actions_seed_page_inputs(monkeypatch) -> None:
    fake_st = RenderStreamlit()
    fake_client = RenderClient()
    modules = [chat, text2sql, sql_review, warehouse_design]
    monkeypatch.setattr(chat_message, "st", fake_st)
    monkeypatch.setattr(theme, "st", fake_st)

    for module in modules:
        monkeypatch.setattr(module, "st", fake_st)
        monkeypatch.setattr(module, "get_client", lambda: fake_client)
        module.render()

    assert fake_st.session_state["chat_prompt_seed"] == "什么是 Spark AQE？它能解决哪些查询性能问题？"
    assert fake_st.session_state["text2sql_question"] == "按天统计最近7天活跃用户数，并按省份输出 Top 10"
    assert "dwd_user_behavior_detail" in fake_st.session_state["text2sql_schema_context"]
    assert fake_st.session_state["sql_review_sql"].startswith("SELECT *")
    assert fake_st.session_state["warehouse_design_requirement"] == "设计电商订单分析数仓，覆盖下单、支付、退款和履约分析"
    assert {
        "module": "数仓设计",
        "description": "加载电商订单主题示例",
    } in fake_st.session_state["recent_activity"]
