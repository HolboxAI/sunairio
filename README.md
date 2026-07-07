# Sunairio NL2SQL

Greenfield NL→SQL service aligned with `prompts/sunairio-sql-prompt.md`.

- Single LLM call per turn returning the 6-field JSON envelope
- Session context built from Metadata DB + app DB
- V1 API returns envelope only (no SQL execution)
- Before/after LLM audit logs in `logs/llm-audit/`

## Run

```bash
cd /home/ec2-user/sunairio-nl2sql
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DB credentials
uvicorn app.main:app --host 0.0.0.0 --port 8003
```

## API

- `POST /api/login` — authenticate
- `GET /api/me` — current user + allowed entities
- `POST /api/query` — NL question → LLM envelope + audit log path
- `POST /api/query/clear` — reset session
- `GET /api/health` / `GET /api/ready` — health checks
