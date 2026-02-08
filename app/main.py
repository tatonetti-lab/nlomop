import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import time

from pydantic import BaseModel

from app import agent, db, llm
from app.concept_cache import build_catalog_text, set_catalog
from app.models import QueryRequest, QueryResponse, SqlRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("Opening database pool...")
    await db.open_pool()

    log.info("Loading concept catalog...")
    concepts = await db.fetch_concept_catalog()
    catalog_text = build_catalog_text(concepts)
    set_catalog(catalog_text)
    log.info("Concept catalog loaded")

    yield

    # Shutdown
    await db.close_pool()


app = FastAPI(title="nlomop", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    return await agent.answer(req.question)


@app.post("/api/sql")
async def run_sql(req: SqlRequest):
    """Execute raw SQL for the SQL IDE panel (read-only, same safety as /api/query)."""
    from app.agent import _validate_sql
    err = _validate_sql(req.sql)
    if err:
        return {"columns": [], "rows": [], "error": f"Blocked: {err}", "elapsed_s": 0}
    t0 = time.monotonic()
    try:
        rows = await db.execute_query(req.sql)
    except Exception as e:
        return {"columns": [], "rows": [], "error": str(e), "elapsed_s": round(time.monotonic() - t0, 2)}
    columns = list(rows[0].keys()) if rows else []
    from app.agent import _serialize_row
    row_values = [_serialize_row(r, columns) for r in rows]
    return {
        "columns": columns,
        "rows": row_values,
        "row_count": len(rows),
        "error": "",
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


@app.get("/api/settings")
async def get_settings():
    return {
        "current_model": llm.get_deployment(),
        "available_models": llm.AVAILABLE_MODELS,
    }


class SetModelRequest(BaseModel):
    model: str


@app.put("/api/settings/model")
async def set_model(req: SetModelRequest):
    llm.set_deployment(req.model)
    return {"current_model": llm.get_deployment()}


# Serve index.html at root
@app.get("/")
async def index():
    return FileResponse("static/index.html")


# Serve other static files
app.mount("/static", StaticFiles(directory="static"), name="static")
