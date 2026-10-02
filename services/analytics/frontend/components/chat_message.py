from typing import Any

from frontend.components.streamlit_compat import st


def render_message(role: str, content: str) -> None:
    with st.chat_message(role):
        st.markdown(content)


def render(role: str = "assistant", content: str = "") -> None:
    render_message(role, content)


def render_citations(citations: list[dict[str, Any]]) -> None:
    if not citations:
        return
    with st.expander("引用来源", expanded=False):
        for citation in citations:
            st.markdown(
                "\n".join(
                    [
                        f"**{citation.get('document_name', '文档')}**",
                        f"片段：`{citation.get('chunk_reference', '-')}`",
                        f"相似度：`{citation.get('similarity_score', 0):.4f}`",
                    ]
                )
            )
