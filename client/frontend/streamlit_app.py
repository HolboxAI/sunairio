"""SQL Runner — type SQL, run it, see the rows. No LLM, no chat."""

from __future__ import annotations

import streamlit as st

from common import call, init_state, render_data, require_login, sidebar_auth

st.set_page_config(page_title="SQL Runner", page_icon="🗄️", layout="wide")

init_state()
sidebar_auth()

st.title("🗄️ SQL Runner")
st.caption("Executes SQL directly via `POST /api/sql`. Read-only: SELECT / WITH only.")

require_login()

# key= (not value=) so the Clear / Recent-SQL buttons can rewrite the box across a rerun.
sql = st.text_area(
    "SQL",
    height=240,
    placeholder="SELECT * FROM ... LIMIT 100",
    label_visibility="collapsed",
    key="sql_draft",
)

run_col, clear_col, _ = st.columns([1, 1, 6])
run = run_col.button("▶ Run", type="primary", use_container_width=True)
if clear_col.button("Clear", use_container_width=True):
    st.session_state.sql_draft = ""
    st.rerun()

if run:
    if not sql.strip():
        st.warning("Enter a SQL statement first.")
    else:
        with st.spinner("Running…"):
            res, err = call("POST", "/api/run-sql", json={"sql": sql}, token=st.session_state.token)
        if err:
            st.error(err)
        else:
            df = render_data(res.get("data") or {})
            st.caption(
                f"plan `{res.get('plan') or 'standard'}` · {res.get('latency_ms')} ms total · "
                f"request `{res.get('request_id', '')[:8]}`"
            )
            detail = res.get("execution_detail") or {}
            if detail.get("steps"):
                with st.expander("Execution steps"):
                    st.json(detail)
            if df is not None:
                st.download_button(
                    "Download CSV",
                    df.to_csv(index=False).encode(),
                    file_name=f"query_{res.get('request_id', 'result')[:8]}.csv",
                    mime="text/csv",
                )
            history = st.session_state.sql_history
            stripped = sql.strip()
            if stripped in history:
                history.remove(stripped)
            history.insert(0, stripped)
            st.session_state.sql_history = history[:20]

if st.session_state.sql_history:
    with st.sidebar:
        st.divider()
        st.subheader("Recent SQL")
        for i, past in enumerate(st.session_state.sql_history):
            label = past.replace("\n", " ")[:45] + ("…" if len(past) > 45 else "")
            if st.button(label, key=f"hist_{i}", use_container_width=True):
                st.session_state.sql_draft = past
                st.rerun()
