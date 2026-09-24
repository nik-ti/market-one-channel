# Market One Dashboard — Live Monitoring for @market_one_news

## Overview

A web-based live dashboard that displays all posts/news flowing into the Market One Telegram channel (@market_one_news), along with stories, statistics, processing graphs, and model prompts. Replaces manual VPS file inspection with a modern, responsive UI accessible from any browser.

## GOAL

A fully functional, responsive dashboard deployed to Vercel that:
- Displays all items (news/posts) arriving at the channel in real time (polling every 5-10 seconds)
- Allows filtering by source (BullTheoryio, DeItaone, Barchart, etc.)
- Shows all active and completed stories with their item counts and current state
- Displays statistics: posts by source, gate/sorter outcomes (published/rejected/low_impact/duplicates), story metrics
- Renders the LangGraph as an interactive SVG showing all nodes and edges
- Shows the system prompt for each node (story_organizer, should_post, writer, editor, etc.)
- Works flawlessly on phone (480px width) and desktop (1920px+) with TailwindCSS responsive classes
- Has 0 external hardcoded IDs; all data flows from the live SQLite database via API
- Logs and handles network failures gracefully (shows "API offline" if VPS is unreachable)

**Success criteria:**
- User opens dashboard in browser, sees posts flowing in live within 5-10 seconds of arriving in the channel
- Can filter posts by source name, see story grouping, and understand why each post was placed/rejected
- Stats are accurate against the database (verified manually spot-check at least 3 times during QA)
- Dashboard loads in under 2 seconds on first visit; subsequent refreshes are instant (React query caching)
- No warnings/errors in browser console or server logs related to the dashboard code
- Works on iPhone Safari, Chrome desktop, Firefox — no layout shifts or broken buttons

---

## FORMAT

### Directory structure in `/home/nikita/systems/market-one-channel/`

```
market-one-channel/
├── dashboard/backend/          # Python FastAPI server (VPS, port 8000)
│   ├── main.py                 # FastAPI app entry point
│   ├── api/
│   │   ├── posts.py            # GET /api/v1/posts (with pagination, filters)
│   │   ├── stories.py          # GET /api/v1/stories
│   │   ├── stats.py            # GET /api/v1/stats
│   │   ├── graph.py            # GET /api/v1/graph (LangGraph schema)
│   │   └── prompts.py          # GET /api/v1/prompts (node prompts)
│   ├── models/
│   │   └── schemas.py          # Pydantic models for responses
│   ├── db_connector.py         # SQLite read-only connector with timeout handling
│   ├── requirements.txt
│   ├── run.sh                  # Start script (uvicorn)
│   └── README.md
│
├── dashboard/frontend/         # Next.js app (deploy to Vercel)
│   ├── app/
│   │   ├── page.tsx            # Main layout with tabs
│   │   ├── layout.tsx
│   │   └── globals.css         # TailwindCSS
│   ├── components/
│   │   ├── PostsFeed.tsx       # Tab: all posts with source filter
│   │   ├── StoriesView.tsx     # Tab: stories list and state
│   │   ├── StatsPanel.tsx      # Tab: statistics, charts
│   │   ├── GraphViewer.tsx     # Tab: LangGraph visualization
│   │   ├── PromptsViewer.tsx   # Tab: node prompts (if built)
│   │   ├── Header.tsx
│   │   ├── TabNav.tsx
│   │   └── ui/                 # Shadcn/ui components (Button, Card, Tabs, etc.)
│   ├── hooks/
│   │   └── useApi.ts           # TanStack Query setup for polling
│   ├── lib/
│   │   ├── api.ts              # Fetch functions
│   │   └── types.ts            # TypeScript types
│   ├── .env.example
│   ├── .env.local              # (gitignored) NEXT_PUBLIC_API_URL=https://your-vps/api/v1
│   ├── next.config.js
│   ├── package.json
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   └── README.md
│
└── (existing news-channel files remain unchanged)
```

### Deployment

**Backend:** Systemd service on VPS (add to `/etc/systemd/system/market-one-dashboard.service`)
```
[Service]
ExecStart=/usr/bin/python3 /home/nikita/systems/market-one-channel/dashboard/backend/main.py
Restart=always
WorkingDirectory=/home/nikita/systems/market-one-channel/dashboard/backend
```

Listens on `http://localhost:8000`. Nginx proxy at the VPS routes `/api/dashboard/v1/*` → `http://localhost:8000/api/v1/*`.

**Frontend:** Git repo pushed to Vercel; automatic deploy on `main` branch. Environment variable `NEXT_PUBLIC_API_URL` = `https://<your-vps-domain>/api/dashboard/v1`.

---

## CONSTRAINTS

- **Stack (non-negotiable):** FastAPI + Next.js (as specified by user)
- **Auth:** None for now (personal tool); if expanded, token-based (JWT in header)
- **Browser support:** Chrome, Firefox, Safari (iOS + macOS); no IE11
- **Minimal dependencies:** FastAPI, uvicorn, TanStack Query, Shadcn/ui, TailwindCSS — nothing exotic
- **Database:** Read-only access to existing `data/<channel>.db` (SQLite). No writes from dashboard.
- **Polling interval:** 5-10 seconds (user said "whichever is mechanically better" — 10s is cleaner, start there)
- **API versioning:** `/api/v1/` from day one
- **Hard deadline for core functionality:** Posts + Stories + Stats tabs working before moving to Graph/Prompts tabs

---

## FAILURE

Any of these = not done:

1. **Dashboard loads but shows empty/wrong data** — stats don't match manual DB queries, or posts are stale (> 15s old)
2. **Network failure (VPS offline)** — dashboard doesn't crash; shows clear "API offline" error with retry button
3. **Layout broken on phone** — buttons misaligned, text overlapping, scroll doesn't work, or any Tailwind responsive class missing
4. **No source filtering** — user can't filter posts by "BullTheoryio" or other source names; filtering UI must be visible and work
5. **Stories data missing or wrong** — story counts don't match items.story_id in DB, or story state is not displayed
6. **Graph never renders** — LangGraph tab loads but shows nothing or broken SVG
7. **Performance bad** — page takes > 3 seconds to load first time, or > 1 second on subsequent (React Query cache miss)
8. **API returns garbage** — malformed JSON, 500 errors, missing required fields, inconsistent types
9. **CORS breaks it** — frontend can't reach backend due to CORS misconfiguration (browser console shows blocked requests)
10. **Code has hardcoded test data or URLs** — any `http://localhost:8000` in frontend code, not in `.env`
11. **No error logging** — backend crashes silently, no stack trace in logs; errors must be logged with context
12. **SQLite timeout not handled** — if news-channel is writing and dashboard tries to read simultaneously, dashboard hangs instead of timing out and showing cached data
13. **Graph shows static SVG every refresh** — if LangGraph was updated (nodes added), dashboard should reflect it on next poll cycle
14. **Prompts are truncated or malformed** — multi-line prompts don't display properly; line breaks are lost
15. **Tab navigation broken** — clicking tabs doesn't switch views, or state is lost on tab switch

**UNVERIFIABLE in automated tests:**
- "Looks modern and minimal" — user will review UI and approve aesthetics manually
- "Color scheme works in light/dark mode" — manual browser test

---

## Built on top of

- **Frontend:** Next.js 14+, React 18+, TailwindCSS 3+, Shadcn/ui, TanStack Query v5
- **Backend:** FastAPI, Uvicorn, sqlite3 (stdlib)
- **Database:** The active channel's database under `data/` (no schema changes)
- **Deployment:** Vercel (frontend), systemd (backend on VPS)
- **Research:** 
  - SWR vs TanStack Query: chose TanStack for better error handling and offline detection
  - FastAPI chosen over Flask for async/await support and native Pydantic validation

---

## Status

**Phase 0 — Planning:** ✓ Complete
- User interview done
- Prior art researched
- Gotchas identified
- SPEC.md written

**Phase 1 — Backend API skeleton (IN PROGRESS)**
- FastAPI app with `/api/v1/posts`, `/api/v1/stories`, `/api/v1/stats` endpoints
- DB connector with timeout handling
- Systemd service setup

**Phase 2 — Frontend scaffolding**
- Next.js project with TailwindCSS + Shadcn/ui
- Tabs layout (Posts | Stories | Stats | Graph | Prompts)
- TanStack Query polling setup

**Phase 3 — Integration & Verification**
- Connect all tabs to live API
- Test on phone + desktop
- Deploy to Vercel
- Manual QA against FAILURE lines

---

## How to run and test

### Backend (local development)
```bash
cd dashboard/backend
pip install -r requirements.txt
python3 main.py  # Runs on http://localhost:8000
```

### Frontend (local development)
```bash
cd dashboard/frontend
npm install
npm run dev  # Runs on http://localhost:3000
# Set NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1 in .env.local
```

### Manual QA Checklist
- [ ] Open http://localhost:3000 in Chrome, then Safari on iPhone
- [ ] Posts tab: see posts flowing in, filter by source works
- [ ] Stories tab: count matches DB (SELECT COUNT(*) FROM stories)
- [ ] Stats tab: source counts match (SELECT source_name, COUNT(*) FROM items GROUP BY source_name)
- [ ] Graph tab: SVG renders without console errors
- [ ] Stop backend (`systemctl stop market-one-dashboard`), see "API offline" message
- [ ] Restart backend, data resumes within 10 seconds
- [ ] Responsive: all text readable at 480px width, no horizontal scroll

---

## Next session notes

- This is phase 0 (planning). Next agent(s) will build phases 1–3.
- SPEC.md is the contract. If anything contradicts SPEC.md, ask nikita before proceeding.
- User prefers clear, step-by-step plans over surprises.
- When deploying frontend to Vercel, remember to set `NEXT_PUBLIC_API_URL` environment variable.
