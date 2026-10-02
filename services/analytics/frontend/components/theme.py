from __future__ import annotations

from html import escape
from typing import Mapping, Sequence

from frontend.components.streamlit_compat import st


Action = Mapping[str, str]


def apply_theme() -> None:
    st.html(
        """
        <style>
        :root {
          --dc-primary: #2563EB;
          --dc-primary-2: #0EA5E9;
          --dc-accent: #F97316;
          --dc-bg: #F8FAFC;
          --dc-panel: #FFFFFF;
          --dc-text: #1E293B;
          --dc-muted: #64748B;
          --dc-border: #E2E8F0;
          --dc-success: #16A34A;
          --dc-warning: #D97706;
          --dc-danger: #DC2626;
          --dc-shadow: 0 16px 40px rgba(15, 23, 42, 0.08);
        }

        .stApp {
          background:
            radial-gradient(circle at 12% 8%, rgba(37, 99, 235, 0.09), transparent 28rem),
            linear-gradient(180deg, #F8FAFC 0%, #EEF6FF 100%);
          color: var(--dc-text);
        }

        .block-container {
          max-width: 1280px;
          padding-top: 3.25rem;
          padding-bottom: 3rem;
          animation: fadeUp 360ms ease-out both;
        }

        section[data-testid="stSidebar"] {
          background: rgba(255, 255, 255, 0.92);
          border-right: 1px solid var(--dc-border);
          box-shadow: 10px 0 35px rgba(15, 23, 42, 0.05);
        }

        h1, h2, h3 {
          letter-spacing: 0;
          color: var(--dc-text);
          font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
        }

        div[data-testid="stMetric"],
        div[data-testid="stExpander"],
        div[data-testid="stAlert"],
        div[data-testid="stFileUploader"],
        div[data-testid="stDataFrame"] {
          border-radius: 8px;
        }

        div[data-testid="stMetric"] {
          background: rgba(255, 255, 255, 0.92);
          border: 1px solid var(--dc-border);
          box-shadow: var(--dc-shadow);
          padding: 0.85rem 1rem;
          transition: transform 180ms ease-out, box-shadow 180ms ease-out, border-color 180ms ease-out;
        }

        div[data-testid="stMetric"]:hover {
          transform: translateY(-2px);
          border-color: rgba(37, 99, 235, 0.32);
          box-shadow: 0 18px 45px rgba(37, 99, 235, 0.12);
        }

        .stButton > button,
        .stDownloadButton > button {
          border-radius: 8px;
          border: 1px solid rgba(37, 99, 235, 0.26);
          transition: transform 160ms ease-out, box-shadow 160ms ease-out, border-color 160ms ease-out;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover {
          transform: translateY(-1px);
          box-shadow: 0 10px 22px rgba(37, 99, 235, 0.16);
          border-color: rgba(37, 99, 235, 0.55);
        }

        .datacopilot-hero {
          position: relative;
          overflow: visible;
          box-sizing: border-box;
          min-height: 8.5rem;
          border: 1px solid rgba(226, 232, 240, 0.9);
          border-radius: 8px;
          background:
            linear-gradient(135deg, rgba(37, 99, 235, 0.95), rgba(14, 165, 233, 0.86)),
            linear-gradient(180deg, #FFFFFF, #EFF6FF);
          color: #FFFFFF;
          padding: 1.7rem 1.6rem;
          box-shadow: 0 20px 48px rgba(37, 99, 235, 0.22);
          animation: fadeUp 360ms ease-out both;
        }

        .datacopilot-hero::after {
          content: "";
          position: absolute;
          inset: auto -4rem -7rem auto;
          width: 18rem;
          height: 18rem;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.16);
          z-index: 0;
        }

        .datacopilot-hero h1 {
          position: relative;
          z-index: 1;
          color: #FFFFFF;
          font-size: 2.15rem;
          line-height: 1.28;
          margin: 0 0 0.45rem 0;
        }

        .datacopilot-hero p {
          position: relative;
          z-index: 1;
          color: rgba(255, 255, 255, 0.9);
          margin: 0;
          max-width: 64rem;
        }

        .dc-card-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
          gap: 0.85rem;
          margin: 1rem 0 0.35rem;
        }

        .dc-card {
          background: rgba(255, 255, 255, 0.92);
          border: 1px solid var(--dc-border);
          border-radius: 8px;
          padding: 1rem;
          box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
          transition: transform 180ms ease-out, box-shadow 180ms ease-out, border-color 180ms ease-out;
          animation: fadeUp 380ms ease-out both;
        }

        .dc-card:hover {
          transform: translateY(-2px);
          border-color: rgba(37, 99, 235, 0.3);
          box-shadow: 0 18px 42px rgba(15, 23, 42, 0.09);
        }

        .dc-card strong {
          display: block;
          color: var(--dc-text);
          margin-bottom: 0.35rem;
        }

        .dc-card span {
          color: var(--dc-muted);
          font-size: 0.92rem;
        }

        .dc-action-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 0.85rem;
          margin: 0.8rem 0 0.5rem;
        }

        .dc-action-card {
          min-height: 7.5rem;
          background:
            linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 250, 252, 0.96));
          border: 1px solid var(--dc-border);
          border-radius: 8px;
          padding: 0.95rem;
          box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06);
          transition: transform 180ms ease-out, box-shadow 180ms ease-out, border-color 180ms ease-out;
          animation: fadeUp 360ms ease-out both;
        }

        .dc-action-card:hover {
          transform: translateY(-2px);
          border-color: rgba(249, 115, 22, 0.38);
          box-shadow: 0 18px 40px rgba(15, 23, 42, 0.1);
        }

        .dc-action-card b {
          display: block;
          color: var(--dc-text);
          font-size: 0.98rem;
          margin-bottom: 0.42rem;
        }

        .dc-action-card p {
          color: var(--dc-muted);
          font-size: 0.9rem;
          line-height: 1.55;
          margin: 0;
        }

        .dc-action-card small {
          display: inline-flex;
          width: fit-content;
          margin-bottom: 0.55rem;
          padding: 0.18rem 0.52rem;
          border-radius: 999px;
          color: #9A3412;
          background: rgba(249, 115, 22, 0.11);
          border: 1px solid rgba(249, 115, 22, 0.16);
          font-weight: 700;
        }

        .dc-activity-panel {
          margin-top: 1rem;
          background: rgba(255, 255, 255, 0.92);
          border: 1px solid var(--dc-border);
          border-radius: 8px;
          box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
          overflow: hidden;
        }

        .dc-activity-row {
          display: grid;
          grid-template-columns: minmax(5.5rem, 9rem) 1fr;
          gap: 0.7rem;
          padding: 0.75rem 0.95rem;
          border-bottom: 1px solid rgba(226, 232, 240, 0.78);
          animation: fadeUp 300ms ease-out both;
        }

        .dc-activity-row:last-child {
          border-bottom: 0;
        }

        .dc-activity-module {
          color: var(--dc-primary);
          font-weight: 700;
          font-size: 0.88rem;
        }

        .dc-activity-desc {
          color: var(--dc-text);
          font-size: 0.9rem;
        }

        .dc-status {
          display: inline-flex;
          align-items: center;
          gap: 0.42rem;
          border-radius: 999px;
          padding: 0.28rem 0.68rem;
          background: rgba(22, 163, 74, 0.1);
          color: #166534;
          border: 1px solid rgba(22, 163, 74, 0.18);
          font-size: 0.86rem;
          font-weight: 600;
        }

        .dc-status::before {
          content: "";
          width: 0.48rem;
          height: 0.48rem;
          border-radius: 999px;
          background: var(--dc-success);
          animation: statusPulse 1.8s ease-in-out infinite;
        }

        .dc-muted {
          color: var(--dc-muted);
        }

        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }

        @keyframes statusPulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(22, 163, 74, 0.35); }
          50% { box-shadow: 0 0 0 6px rgba(22, 163, 74, 0); }
        }

        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
          }
        }
        </style>
        """,
    )


def hero(title: str, subtitle: str) -> None:
    st.html(
        f"""
        <section class="datacopilot-hero">
          <h1>{escape(title)}</h1>
          <p>{escape(subtitle)}</p>
        </section>
        """
    )


def card_grid(cards: list[tuple[str, str]]) -> None:
    items = "\n".join(
        f"<article class=\"dc-card\"><strong>{escape(title)}</strong><span>{escape(body)}</span></article>"
        for title, body in cards
    )
    st.html(f"<div class=\"dc-card-grid\">{items}</div>")


def status_badge(text: str) -> None:
    st.html(f"<span class=\"dc-status\">{escape(text)}</span>")


def quick_actions(title: str, actions: Sequence[Action], key_prefix: str) -> Action | None:
    st.subheader(title)
    if not actions:
        return None

    columns = st.columns(min(3, len(actions)))
    selected: Action | None = None
    for index, action in enumerate(actions):
        with columns[index % len(columns)]:
            st.html(_action_card_html(action))
            if st.button(
                action.get("button_label", "加载示例"),
                key=f"{key_prefix}-{index}",
                use_container_width=True,
            ) and selected is None:
                selected = action
    return selected


def record_activity(module: str, description: str) -> None:
    activity = {"module": module, "description": description}
    recent = list(st.session_state.get("recent_activity", []))
    if recent[:1] == [activity]:
        return
    st.session_state["recent_activity"] = [activity, *recent][:8]


def recent_activity(title: str = "最近操作", limit: int = 5) -> None:
    st.subheader(title)
    recent = list(st.session_state.get("recent_activity", []))[:limit]
    if not recent:
        st.caption("还没有操作记录，试试首页演示任务或各工具页的一键示例。")
        return

    rows = "\n".join(
        (
            '<div class="dc-activity-row">'
            f'<span class="dc-activity-module">{escape(item.get("module", "-"))}</span>'
            f'<span class="dc-activity-desc">{escape(item.get("description", "-"))}</span>'
            "</div>"
        )
        for item in recent
    )
    st.html(f'<section class="dc-activity-panel">{rows}</section>')


def _action_card_html(action: Action) -> str:
    tag = action.get("tag")
    tag_html = f"<small>{escape(tag)}</small>" if tag else ""
    return (
        '<article class="dc-action-card">'
        f"{tag_html}"
        f"<b>{escape(action.get('title', '示例'))}</b>"
        f"<p>{escape(action.get('description', ''))}</p>"
        "</article>"
    )
