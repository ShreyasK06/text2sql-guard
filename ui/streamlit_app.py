"""Demo UI for text2sql-guard. Run with: streamlit run ui/streamlit_app.py

The pipeline runs in-process (no HTTP). Role and region are chosen in the sidebar;
in this demo they are caller-asserted, with no authentication.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.types import LAYERS, REGIONS, ROLES, GuardConfig  # noqa: E402

EXAMPLES: list[tuple[str, str, str | None]] = [
    # (button label, question, role to switch to or None to keep)
    ("Total revenue by region", "total revenue by region", None),
    ("Top 5 products by revenue", "top 5 products by revenue", None),
    ("Order count by status", "order count by status", None),
    ("Customer contact details (manager)", "list customers with contact details", "manager"),
]

DIRECT_SQL_HELP = (
    "Examples of safe reads:\n"
    "SELECT status, COUNT(*) AS n FROM orders GROUP BY status\n"
    "SELECT category, AVG(price) AS avg_price FROM products GROUP BY category"
)


@st.cache_resource(show_spinner="Starting pipeline")
def get_pipeline():
    from app.pipeline import build_pipeline

    return build_pipeline()


def init_state() -> None:
    st.session_state.setdefault("role", ROLES[0])
    st.session_state.setdefault("region", REGIONS[0])
    st.session_state.setdefault("question", "")
    st.session_state.setdefault("sql", "")
    st.session_state.setdefault("result", None)


def pick_example(question: str, role: str | None) -> None:
    st.session_state["question"] = question
    st.session_state["sql"] = ""
    if role:
        st.session_state["role"] = role


def sidebar() -> GuardConfig:
    st.sidebar.header("Caller")
    st.sidebar.selectbox("Role", ROLES, key="role")
    if st.session_state["role"] == "manager":
        st.sidebar.selectbox("Region", REGIONS, key="region")
    st.sidebar.caption("Role and region are caller-asserted in this demo (no authentication).")

    st.sidebar.header("Guard layers")
    enabled = [layer for layer in LAYERS if st.sidebar.checkbox(layer, value=True, key=f"layer_{layer}")]

    st.sidebar.header("Example prompts")
    for label, question, role in EXAMPLES:
        st.sidebar.button(label, key=f"ex_{label}", on_click=pick_example, args=(question, role))

    st.sidebar.header("Direct SQL")
    st.sidebar.caption("This box lets you paste any SQL to see how the guards respond.")
    st.sidebar.text_area("SQL", key="sql", help=DIRECT_SQL_HELP, height=120)
    return GuardConfig.only(*enabled)


def decision_badge(decision: str) -> None:
    if decision == "allow":
        st.success("ALLOWED")
    elif decision == "block":
        st.error("BLOCKED")
    else:
        st.warning("ERROR")


def show_result(res) -> None:
    decision_badge(res.decision)
    if res.decision != "allow":
        st.write(f"Layer: **{res.blocked_by}**  |  Rule: `{res.rule_id}`")
        st.write(f"Reason: {res.reason}")

    col1, col2 = st.columns(2)
    with col1:
        st.caption("Generated SQL")
        st.code(res.sql_generated or "(none)", language="sql")
    with col2:
        st.caption("Executed SQL")
        st.code(res.sql_executed or "(not executed)", language="sql")

    st.subheader("Guardrails fired")
    if res.guard_results:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "layer": g.layer,
                        "decision": g.decision.value,
                        "rule id": g.rule_id or "",
                        "reason": g.reason,
                        "latency ms": round(g.latency_ms, 2),
                    }
                    for g in res.guard_results
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.write("No guard ran.")

    st.write(
        f"Masked columns: {', '.join(res.masked_columns) or 'none'}  |  "
        f"Redactions: {res.redactions}  |  Retries: {res.retries}  |  "
        f"Latency: {res.latency_ms:.1f} ms  |  Config: {res.config}"
    )

    if res.decision == "allow":
        st.subheader("Result")
        st.dataframe(pd.DataFrame(res.rows, columns=res.columns), use_container_width=True)
        if res.self_check:
            st.write(f"Self-check verdict: **{res.self_check.get('verdict', 'n/a')}**")
            if res.self_check.get("explanation"):
                st.caption(str(res.self_check["explanation"]))


def audit_tail(pipeline) -> None:
    with st.expander("Audit log (last 20 records)"):
        log = getattr(pipeline, "audit", None)
        if log is None:
            st.write("Audit logging is not enabled.")
            return
        records = log.tail(20)
        if records:
            st.json(records)
        else:
            st.write("No records yet.")


def main() -> None:
    st.set_page_config(page_title="text2sql-guard", layout="wide")
    st.title("text2sql-guard")
    init_state()
    config = sidebar()
    pipeline = get_pipeline()

    st.text_input("Question", key="question", placeholder="e.g. total revenue by region")
    if st.button("Run", type="primary"):
        question = st.session_state["question"].strip() or None
        sql = st.session_state["sql"].strip() or None
        if not question and not sql:
            st.warning("Enter a question or some SQL.")
        else:
            role = st.session_state["role"]
            region = st.session_state["region"] if role == "manager" else None
            with st.spinner("Running"):
                st.session_state["result"] = pipeline.run(
                    question, role, region, sql_override=sql, config=config
                )

    if st.session_state["result"] is not None:
        show_result(st.session_state["result"])
    audit_tail(pipeline)


main()
