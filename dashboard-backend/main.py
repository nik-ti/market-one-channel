"""Market One Dashboard backend — FastAPI app.

Serves read-only data from data/news.db to the dashboard frontend under
/api/v1/*. Personal tool: no auth, CORS open to any origin, polling only
(no WebSocket). Run directly with `python3 main.py` or via run.sh.
"""

from __future__ import annotations

import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import graph, posts, prompts, stats, stories
from db_connector import DatabaseUnavailableError

app = FastAPI(title="Market One Dashboard API", version="1.0.0")

# Personal tool used from a phone/laptop/Vercel preview URLs — no reason to
# restrict origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(DatabaseUnavailableError)
async def db_unavailable_handler(request: Request, exc: DatabaseUnavailableError):
    print(f"[main] 503 database unavailable on {request.url.path}: {exc}")
    return JSONResponse(status_code=503, content={"error": f"database unavailable: {exc}"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Every unexpected error is logged with a full traceback to stdout, per
    # the spec's "no silent failures" requirement, then reported as a clean
    # 500 instead of leaking a stack trace to the client.
    print(f"[main] unhandled error on {request.url.path}:")
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"error": "internal server error"})


app.include_router(posts.router, prefix="/api/v1")
app.include_router(stories.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")
app.include_router(prompts.router, prefix="/api/v1")


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
