# Sunairio NL2SQL client — SQL Runner

Standalone proxy backend + Streamlit SQL runner that talk to the deployed API at
`http://3.148.208.253:8000`. Nothing here imports the main app — it only calls the HTTP API.

```
Streamlit (8501)  ->  client backend (8601)  ->  http://3.148.208.253:8000
```

One page: paste SQL, hit **Run**, get rows. No chat, no LLM — the query is sent verbatim.

The old natural-language chat page is parked at `frontend/archive/chat_page.py.bak`.
It is not loaded; delete it or rename it back into place if you ever want it.

## ⚠️ Server redeploy required

The runner needs `POST /api/sql`, added to `sunairio-nl2sql/app/api/routes_query.py`, which is
**not yet live** on `3.148.208.253:8000` — that server still runs the older build and answers
`404 Not Found` for `/api/sql`. Redeploy the main app there or nothing will run.

## Install

```bash
pip install -r client/requirements.txt
```

## Run

Terminal 1 — proxy backend:

```bash
UPSTREAM_API_URL=http://3.148.208.253:8000 \
  python client/backend/main.py            # http://localhost:8601
```

Terminal 2 — Streamlit:

```bash
CLIENT_BACKEND_URL=http://localhost:8601 \
  streamlit run client/frontend/streamlit_app.py   # http://localhost:8501
```

Log in from the sidebar (upstream requires auth), then paste SQL and hit Run.

## Env vars

| Var | Default | Used by |
|---|---|---|
| `UPSTREAM_API_URL` | `http://3.148.208.253:8000` | backend |
| `UPSTREAM_TIMEOUT_SEC` | `180` | backend |
| `CLIENT_BACKEND_HOST` | `0.0.0.0` | backend |
| `CLIENT_BACKEND_PORT` | `8601` | backend |
| `CLIENT_BACKEND_URL` | `http://localhost:8601` | frontend |
| `CLIENT_TIMEOUT_SEC` | `180` | frontend |

## Backend endpoints

| Endpoint | Upstream |
|---|---|
| `GET /api/health` | `GET /api/health` (+ reachability flag) |
| `POST /api/login` | `POST /api/login` |
| `GET /api/me` | `GET /api/me` |
| `POST /api/run-sql` | `POST /api/sql` (direct SQL, no LLM) |
| `POST /api/ask` | `POST /api/query` (LLM path — kept, unused by the UI) |
| `POST /api/clear` | `POST /api/query/clear` |

The `Authorization: Bearer <token>` header is passed straight through.

## Safety

`/api/sql` reuses the exact same guards as the LLM path — `validate_sql` (SELECT/WITH only,
no forbidden keywords, single statement), `validate_sql_acl` (per-user project access),
read-only transactions, `statement_timeout`, and the `MAX_QUERY_ROWS` cap. A trailing `;` and
`/* … */` comments are fine. Guard rejections come back as `400` with the reason, shown inline.
