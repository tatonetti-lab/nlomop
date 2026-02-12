import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import agent, db, llm
from app.concept_cache import build_catalog_text, set_catalog
from app.datasources import (
    DataSource,
    add_source,
    delete_source,
    get_active_source,
    get_active_source_id,
    get_source,
    list_sources,
    seed_from_env,
    set_active_source_id,
    update_source,
)
from app.models import (
    DataSourceIn,
    DataSourceOut,
    DataSourceTestRequest,
    DataSourceTestResponse,
    QueryRequest,
    QueryResponse,
    SqlRequest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _mask_password(pw: str) -> str:
    if not pw:
        return ""
    return pw[0] + "*" * (len(pw) - 1) if len(pw) > 1 else "*"


def _source_to_out(src: DataSource, active_id: str) -> DataSourceOut:
    return DataSourceOut(
        id=src.id,
        name=src.name,
        host=src.host,
        port=src.port,
        dbname=src.dbname,
        user=src.user,
        password=_mask_password(src.password),
        schema=src.schema,
        description=src.description,
        is_active=(src.id == active_id),
    )


async def _load_concept_cache() -> None:
    """Fetch concept catalog from current pool and update cache."""
    log.info("Loading concept catalog...")
    concepts = await db.fetch_concept_catalog()
    catalog_text = build_catalog_text(concepts)
    set_catalog(catalog_text)
    log.info("Concept catalog loaded")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed data sources, open pool for active source, load concept cache
    store = seed_from_env()
    active = get_active_source()

    if active:
        log.info("Opening database pool for source: %s", active.name)
        await db.open_pool(conninfo=active.conninfo, schema=active.schema)
    else:
        log.info("No data source configured, opening pool from .env defaults")
        await db.open_pool()

    await _load_concept_cache()

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


# ── Data source API routes ──


@app.get("/api/datasources")
async def api_list_datasources():
    sources = list_sources()
    active_id = get_active_source_id()
    return [_source_to_out(s, active_id) for s in sources]


@app.post("/api/datasources")
async def api_add_datasource(req: DataSourceIn):
    src = DataSource(
        name=req.name,
        host=req.host,
        port=req.port,
        dbname=req.dbname,
        user=req.user,
        password=req.password,
        schema=req.schema,
        description=req.description,
    )
    created = add_source(src)
    active_id = get_active_source_id()
    return _source_to_out(created, active_id)


@app.put("/api/datasources/{source_id}")
async def api_update_datasource(source_id: str, req: DataSourceIn):
    updates = req.model_dump()
    updated = update_source(source_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Data source not found")
    active_id = get_active_source_id()
    return _source_to_out(updated, active_id)


@app.delete("/api/datasources/{source_id}")
async def api_delete_datasource(source_id: str):
    active_id = get_active_source_id()
    if source_id == active_id:
        raise HTTPException(status_code=400, detail="Cannot delete the active data source")
    ok = delete_source(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Data source not found")
    return {"ok": True}


@app.put("/api/datasources/{source_id}/activate")
async def api_activate_datasource(source_id: str):
    src = get_source(source_id)
    if not src:
        raise HTTPException(status_code=404, detail="Data source not found")

    set_active_source_id(source_id)
    await db.switch_source(conninfo=src.conninfo, schema=src.schema)
    await _load_concept_cache()

    return _source_to_out(src, source_id)


@app.post("/api/datasources/test", response_model=DataSourceTestResponse)
async def api_test_datasource(req: DataSourceTestRequest):
    conninfo = f"host={req.host} port={req.port} dbname={req.dbname} user={req.user}"
    if req.password:
        conninfo += f" password={req.password}"
    ok, message = await db.test_connection(conninfo, req.schema)
    return DataSourceTestResponse(ok=ok, message=message)


# Serve index.html at root
@app.get("/")
async def index():
    return FileResponse("static/index.html")


# Serve other static files
app.mount("/static", StaticFiles(directory="static"), name="static")
