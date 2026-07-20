"""Shared helpers for the Streamlit pages — backend calls, auth state, result rendering."""

from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st

BACKEND = os.getenv("CLIENT_BACKEND_URL", "http://localhost:8601").rstrip("/")
TIMEOUT = float(os.getenv("CLIENT_TIMEOUT_SEC", "180"))


def call(method: str, path: str, json=None, token: str | None = None):
    """Call the client backend. Returns (payload, error_message)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.request(method, f"{BACKEND}{path}", json=json, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, f"Backend unreachable: {e}"
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = resp.text
        return None, f"{resp.status_code}: {detail}"
    return resp.json(), None


def init_state() -> None:
    st.session_state.setdefault("token", None)
    st.session_state.setdefault("user", None)
    st.session_state.setdefault("sql_draft", "")
    st.session_state.setdefault("sql_history", [])


def sidebar_auth() -> None:
    """Connection status + login/logout. Call once per page."""
    with st.sidebar:
        st.header("Connection")
        st.caption(f"Backend: `{BACKEND}`")
        health, err = call("GET", "/api/health")
        if err:
            st.error(err)
        else:
            st.caption(f"Upstream: `{health['upstream']}`")
            if health["upstream_ok"]:
                st.success("upstream reachable")
            else:
                st.error("upstream down")

        st.divider()
        if st.session_state.token:
            user = st.session_state.user or {}
            st.write(f"**{user.get('email', 'signed in')}** · `{user.get('role', '')}`")
            if st.button("Log out", use_container_width=True):
                st.session_state.token = None
                st.session_state.user = None
                st.session_state.sql_history = []
                st.rerun()
        else:
            st.subheader("Log in")
            with st.form("login"):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Log in", use_container_width=True):
                    data, err = call("POST", "/api/login", json={"email": email, "password": password})
                    if err:
                        st.error(err)
                    else:
                        st.session_state.token = data["access_token"]
                        st.session_state.user = data.get("user")
                        st.rerun()


def require_login() -> None:
    if not st.session_state.token:
        st.info("Log in from the sidebar to continue.")
        st.stop()


def result_frame(data: dict) -> pd.DataFrame:
    return pd.DataFrame(data.get("rows") or [], columns=data.get("columns") or [])


def render_data(data: dict, *, height: int = 420) -> pd.DataFrame | None:
    """Render a QueryData block (caption + table). Returns the DataFrame, if any rows."""
    if not data:
        return None
    meta = f"{data.get('row_count', 0)} rows · {data.get('backend', '?')} · {data.get('query_time_ms', 0)} ms"
    if data.get("truncated"):
        meta += " · truncated (row cap hit)"
    st.caption(meta)
    if not data.get("rows"):
        st.info("Query ran and returned no rows.")
        return None
    df = result_frame(data)
    st.dataframe(df, use_container_width=True, height=height)
    return df
