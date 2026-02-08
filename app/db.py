import logging
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

log = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def open_pool() -> None:
    global _pool
    _pool = AsyncConnectionPool(
        conninfo=settings.db.conninfo,
        min_size=1,
        max_size=5,
        open=False,
    )
    await _pool.open()
    log.info("Database pool opened")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("Database pool closed")


def _get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool


async def execute_query(sql: str) -> list[dict[str, Any]]:
    """Execute a read-only SQL query with timeout. Returns list of row dicts."""
    pool = _get_pool()
    timeout_s = settings.db.query_timeout_s
    schema = settings.db.schema_

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SET statement_timeout TO '{timeout_s}s'")
            await cur.execute("SET default_transaction_read_only TO on")
            await cur.execute(f"SET search_path TO {schema}")
            await cur.execute(sql)
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def fetch_concept_catalog() -> list[dict[str, Any]]:
    """Fetch all distinct concept IDs actually used in clinical tables."""
    pool = _get_pool()
    schema = settings.db.schema_

    queries = {
        "Condition": f"""
            SELECT DISTINCT co.condition_concept_id AS concept_id, c.concept_name, c.domain_id, c.vocabulary_id
            FROM {schema}.condition_occurrence co
            JOIN {schema}.concept c ON co.condition_concept_id = c.concept_id
            WHERE c.standard_concept = 'S'
        """,
        "Drug (ingredient)": f"""
            SELECT DISTINCT de.drug_concept_id AS concept_id, c.concept_name, c.domain_id, c.vocabulary_id
            FROM {schema}.drug_era de
            JOIN {schema}.concept c ON de.drug_concept_id = c.concept_id
            WHERE c.standard_concept = 'S'
        """,
        "Drug (clinical drug)": f"""
            SELECT DISTINCT dx.drug_concept_id AS concept_id, c.concept_name, c.domain_id, c.vocabulary_id
            FROM {schema}.drug_exposure dx
            JOIN {schema}.concept c ON dx.drug_concept_id = c.concept_id
            WHERE c.standard_concept = 'S'
        """,
        "Measurement": f"""
            SELECT DISTINCT m.measurement_concept_id AS concept_id, c.concept_name, c.domain_id, c.vocabulary_id
            FROM {schema}.measurement m
            JOIN {schema}.concept c ON m.measurement_concept_id = c.concept_id
            WHERE c.standard_concept = 'S'
        """,
        "Observation": f"""
            SELECT DISTINCT o.observation_concept_id AS concept_id, c.concept_name, c.domain_id, c.vocabulary_id
            FROM {schema}.observation o
            JOIN {schema}.concept c ON o.observation_concept_id = c.concept_id
            WHERE c.standard_concept = 'S'
        """,
        "Procedure": f"""
            SELECT DISTINCT po.procedure_concept_id AS concept_id, c.concept_name, c.domain_id, c.vocabulary_id
            FROM {schema}.procedure_occurrence po
            JOIN {schema}.concept c ON po.procedure_concept_id = c.concept_id
            WHERE c.standard_concept = 'S'
        """,
        "Device": f"""
            SELECT DISTINCT de.device_concept_id AS concept_id, c.concept_name, c.domain_id, c.vocabulary_id
            FROM {schema}.device_exposure de
            JOIN {schema}.concept c ON de.device_concept_id = c.concept_id
            WHERE c.standard_concept = 'S'
        """,
    }

    all_concepts: list[dict[str, Any]] = []
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SET search_path TO {schema}")
            await cur.execute("SET statement_timeout TO '120s'")
            for label, sql in queries.items():
                log.info("Loading concepts: %s", label)
                await cur.execute(sql)
                rows = await cur.fetchall()
                all_concepts.extend(dict(r) for r in rows)
                log.info("  → %d concepts", len(rows))

    return all_concepts


async def search_concepts(term: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search the concept table by name for fallback concept resolution."""
    pool = _get_pool()
    schema = settings.db.schema_

    sql = f"""
        SELECT concept_id, concept_name, domain_id, vocabulary_id, concept_class_id
        FROM {schema}.concept
        WHERE concept_name ILIKE %s
          AND standard_concept = 'S'
        ORDER BY concept_name
        LIMIT %s
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SET search_path TO {schema}")
            await cur.execute(sql, (f"%{term}%", limit))
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
