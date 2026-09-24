# Market One Dashboard — Backend

Read-only FastAPI service that exposes `data/news.db` to the dashboard
frontend. Never writes to the database.

## Run locally

```bash
cd dashboard-backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
python3 main.py          # http://localhost:8000
# or: ./run.sh
```

Test it:

```bash
curl http://localhost:8000/api/v1/posts
curl http://localhost:8000/api/v1/stories
curl http://localhost:8000/api/v1/stats
curl http://localhost:8000/api/v1/graph
curl http://localhost:8000/api/v1/nodes
```

## Endpoints

All under `/api/v1`, GET only:

| Path | Query params | Returns |
|---|---|---|
| `/posts` | `source`, `limit` (default 50), `offset` (default 0) | paginated items |
| `/stories` | — | all stories with computed item/post counts |
| `/stats` | — | posts-by-source, published/rejected/held/expired split, 24h trend |
| `/graph` | — | pipeline nodes/edges + per-node health |
| `/nodes` | — | each LLM node's model (+ fallback) and full system prompt |

## Errors

- DB file missing, or a query times out (5s) / hits a lock → `503` with an
  `{"error": "..."}` body. The dashboard never hangs waiting on a write in
  progress on the same file.
- Anything else unexpected → `500`, full traceback printed to stdout.

## Deploying (VPS, systemd)

See `/home/nikita/systems/market-one-channel/SPEC.md` for the
`market-one-dashboard.service` unit. It runs `run.sh` from this directory.
