# Market One Dashboard — Frontend

Next.js app that shows what's flowing through the @market_one_news pipeline:
posts, stories, stats, the processing graph, and each node's model + system
prompt.

## Run locally

```bash
cd dashboard/frontend
npm install
cp .env.example .env.local   # points at http://localhost:8000/api/v1
npm run dev                  # http://localhost:3000
```

The backend (`dashboard/backend/`) must be running first — see its README.

## Tabs

- **Posts** — filterable, paginated feed (50/page), polls every 10s.
- **Stories** — live stories as cards, closed stories collapsed below.
- **Stats** — posts by source, gate outcomes, 24h trend (Recharts).
- **Graph** — the pipeline as an SVG; click a node for its last invocation
  time and error count.
- **Nodes** — every LLM node in the pipeline (dedup/judge, sorter, place
  story, story gate, writer, editor, embeddings): what it does, which model
  runs it (resolved the same way `config.py` does — `.env` override, else
  the default), and its full system prompt.

## Deploying to Vercel

Push this repo, import it in Vercel, and set the environment variable:

```
NEXT_PUBLIC_API_URL=https://<your-vps-domain>/api/dashboard/v1
```

(nginx on the VPS proxies `/api/dashboard/v1/*` to the backend's
`/api/v1/*` — see SPEC.md.)
