import asyncio
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


def _build_ssh_config(src: DataSource) -> dict | None:
    """Extract SSH tunnel config dict from a DataSource, or None if SSH is off."""
    if not src.use_ssh or not src.ssh_host:
        return None
    return {
        "ssh_host": src.ssh_host,
        "ssh_port": src.ssh_port,
        "ssh_user": src.ssh_user,
        "ssh_key_path": src.ssh_key_path,
        "ssh_password": src.ssh_password,
        "db_host": src.host,
        "db_port": src.port,
    }


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
        use_ssh=src.use_ssh,
        ssh_host=src.ssh_host,
        ssh_port=src.ssh_port,
        ssh_user=src.ssh_user,
        ssh_key_path=src.ssh_key_path,
        ssh_password=_mask_password(src.ssh_password),
    )


_catalog_status: str = "idle"  # "idle" | "loading" | "ready" | "error:<msg>"


async def _load_concept_cache() -> None:
    """Fetch concept catalog from current pool and update cache."""
    global _catalog_status
    _catalog_status = "loading"
    try:
        log.info("Loading concept catalog...")
        concepts = await db.fetch_concept_catalog()
        catalog_text = build_catalog_text(concepts)
        set_catalog(catalog_text)
        _catalog_status = "ready"
        log.info("Concept catalog loaded (%d concepts)", len(concepts))
    except Exception as exc:
        _catalog_status = f"error:{exc}"
        log.warning("Failed to load concept catalog: %s", exc)


def _load_concept_cache_background() -> None:
    """Fire-and-forget: schedule concept cache loading as a background task."""
    asyncio.create_task(_load_concept_cache())


async def _try_connect(src: DataSource) -> bool:
    """Try to open a pool for a source. Returns True on success.

    Concept cache loading is deferred to a background task so startup
    and activation are fast even over slow SSH tunnels.
    """
    try:
        ssh_config = _build_ssh_config(src)
        await db.open_pool(
            conninfo=src.conninfo, schema=src.schema, ssh_config=ssh_config
        )
        _load_concept_cache_background()
        return True
    except Exception as exc:
        log.warning("Failed to connect to '%s': %s", src.name, exc)
        await db.close_pool()
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = seed_from_env()
    sources = list_sources()
    active = get_active_source()
    connected = False

    # 1. Try the active source first
    if active:
        log.info("Trying active source: %s", active.name)
        connected = await _try_connect(active)

    # 2. If that failed, try each remaining source as fallback
    if not connected:
        for src in sources:
            if active and src.id == active.id:
                continue  # already tried
            log.info("Falling back to source: %s", src.name)
            if await _try_connect(src):
                set_active_source_id(src.id)
                connected = True
                break

    if connected:
        log.info("Database connected (source: %s)", get_active_source().name)
    else:
        log.warning(
            "No data source connected. The app will start but queries "
            "will fail until a working source is activated via Settings."
        )

    yield

    # Shutdown
    await db.close_pool()


app = FastAPI(title="nlomop", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health():
    connected = db.is_pool_ready()
    return {"status": "ok", "db_connected": connected}


@app.get("/api/catalog-status")
async def catalog_status():
    """Return the current concept catalog loading status."""
    return {"status": _catalog_status}


@app.post("/api/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    if not db.is_pool_ready():
        return QueryResponse(
            question=req.question,
            explanation="No database connected. Open Settings and activate a data source.",
            error="No database connected",
        )
    return await agent.answer(req.question)


@app.post("/api/sql")
async def run_sql(req: SqlRequest):
    """Execute raw SQL for the SQL IDE panel (read-only, same safety as /api/query)."""
    if not db.is_pool_ready():
        return {"columns": [], "rows": [], "error": "No database connected. Open Settings and activate a data source.", "elapsed_s": 0}
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
        use_ssh=req.use_ssh,
        ssh_host=req.ssh_host,
        ssh_port=req.ssh_port,
        ssh_user=req.ssh_user,
        ssh_key_path=req.ssh_key_path,
        ssh_password=req.ssh_password,
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
    ssh_config = _build_ssh_config(src)
    try:
        await db.switch_source(
            conninfo=src.conninfo, schema=src.schema, ssh_config=ssh_config
        )
    except Exception as exc:
        await db.close_pool()
        raise HTTPException(status_code=502, detail=f"Connection failed: {exc}")

    # Load concept catalog in the background — don't block the response
    _load_concept_cache_background()

    return _source_to_out(src, source_id)


@app.post("/api/datasources/test", response_model=DataSourceTestResponse)
async def api_test_datasource(req: DataSourceTestRequest):
    conninfo = f"host={req.host} port={req.port} dbname={req.dbname} user={req.user}"
    if req.password:
        conninfo += f" password={req.password}"

    ssh_config = None
    if req.use_ssh and req.ssh_host:
        ssh_config = {
            "ssh_host": req.ssh_host,
            "ssh_port": req.ssh_port,
            "ssh_user": req.ssh_user,
            "ssh_key_path": req.ssh_key_path,
            "ssh_password": req.ssh_password,
            "db_host": req.host,
            "db_port": req.port,
        }

    ok, message = await db.test_connection(conninfo, req.schema, ssh_config=ssh_config)
    return DataSourceTestResponse(ok=ok, message=message)


# Serve index.html at root (no-cache so browser always gets latest)
@app.get("/")
async def index():
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache"},
    )


# Serve other static files (no-cache for development)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def add_no_cache_to_static(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
