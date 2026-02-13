import logging
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

log = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_active_schema: str = settings.db.schema_
_tunnel: Any = None  # SSHTunnelForwarder or None
_running_query_pid: int | None = None  # PID of currently executing query (for cancel)


def _create_tunnel(ssh_config: dict) -> Any:
    """Create and start an SSH tunnel. Returns the SSHTunnelForwarder instance."""
    from sshtunnel import SSHTunnelForwarder

    kwargs: dict[str, Any] = {
        "ssh_address_or_host": (ssh_config["ssh_host"], ssh_config["ssh_port"]),
        "remote_bind_address": (ssh_config["db_host"], ssh_config["db_port"]),
    }
    if ssh_config.get("ssh_user"):
        kwargs["ssh_username"] = ssh_config["ssh_user"]
    if ssh_config.get("ssh_key_path"):
        kwargs["ssh_pkey"] = str(Path(ssh_config["ssh_key_path"]).expanduser())
    if ssh_config.get("ssh_password"):
        kwargs["ssh_password"] = ssh_config["ssh_password"]

    tunnel = SSHTunnelForwarder(**kwargs)
    tunnel.start()
    log.info(
        "SSH tunnel started: localhost:%d -> %s:%d via %s:%d",
        tunnel.local_bind_port,
        ssh_config["db_host"],
        ssh_config["db_port"],
        ssh_config["ssh_host"],
        ssh_config["ssh_port"],
    )
    return tunnel


def _stop_tunnel() -> None:
    """Stop the active SSH tunnel if one exists."""
    global _tunnel
    if _tunnel is not None:
        try:
            _tunnel.stop()
            log.info("SSH tunnel stopped")
        except Exception:
            log.warning("Error stopping SSH tunnel", exc_info=True)
        _tunnel = None


def is_pool_ready() -> bool:
    """Return True if the database pool is open and usable."""
    return _pool is not None


async def open_pool(
    conninfo: str | None = None,
    schema: str | None = None,
    ssh_config: dict | None = None,
) -> None:
    """Open a connection pool. Uses .env defaults if no args provided.

    If ssh_config is provided, an SSH tunnel is created first and the
    connection is routed through localhost:<tunnel_port>.
    Adds connect_timeout=10 to prevent hanging on unreachable hosts.
    """
    global _pool, _active_schema, _tunnel

    effective_conninfo = conninfo or settings.db.conninfo

    # Add a connect timeout so bad sources fail fast instead of hanging
    if "connect_timeout" not in effective_conninfo:
        effective_conninfo += " connect_timeout=10"

    if ssh_config:
        _tunnel = _create_tunnel(ssh_config)
        # Rewrite conninfo to go through the tunnel
        import re

        effective_conninfo = re.sub(
            r"host=\S+", f"host=127.0.0.1", effective_conninfo
        )
        effective_conninfo = re.sub(
            r"port=\S+", f"port={_tunnel.local_bind_port}", effective_conninfo
        )

    _pool = AsyncConnectionPool(
        conninfo=effective_conninfo,
        min_size=1,
        max_size=5,
        open=False,
    )
    await _pool.open()
    if schema:
        _active_schema = schema
    log.info("Database pool opened (schema=%s)", _active_schema)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("Database pool closed")
    _stop_tunnel()


async def switch_source(
    conninfo: str,
    schema: str,
    ssh_config: dict | None = None,
) -> None:
    """Close current pool and open a new one for a different data source."""
    await close_pool()
    await open_pool(conninfo=conninfo, schema=schema, ssh_config=ssh_config)


def get_schema() -> str:
    """Return the active source's schema name."""
    return _active_schema


def _get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized")
    return _pool


async def execute_query(sql: str) -> list[dict[str, Any]]:
    """Execute a read-only SQL query with timeout. Returns list of row dicts."""
    global _running_query_pid
    pool = _get_pool()
    timeout_s = settings.db.query_timeout_s
    schema = _active_schema

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # Track PID for cancel support
            await cur.execute("SELECT pg_backend_pid()")
            pid_row = await cur.fetchone()
            _running_query_pid = pid_row["pg_backend_pid"] if pid_row else None
            try:
                await cur.execute(f"SET statement_timeout TO '{timeout_s}s'")
                await cur.execute("SET default_transaction_read_only TO on")
                await cur.execute(f"SET search_path TO {schema}")
                await cur.execute(sql)
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                _running_query_pid = None


async def cancel_query() -> bool:
    """Cancel the currently running query via pg_cancel_backend(). Returns True if sent."""
    pid = _running_query_pid
    if pid is None:
        return False
    pool = _get_pool()
    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT pg_cancel_backend(%s)", (pid,))
                row = await cur.fetchone()
                return row["pg_cancel_backend"] if row else False
    except Exception:
        log.warning("Failed to cancel query (pid=%s)", pid, exc_info=True)
        return False


async def get_table_indexes(tables: list[str]) -> dict[str, list[str]]:
    """Return {table_name: [indexdef, ...]} for the given tables.

    Queries pg_indexes to find which indexes actually exist, so we can
    avoid suggesting indexes the user already created.
    """
    if not tables:
        return {}
    pool = _get_pool()
    schema = _active_schema
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT tablename, indexdef FROM pg_indexes "
                "WHERE schemaname = %s AND tablename = ANY(%s)",
                (schema, tables),
            )
            rows = await cur.fetchall()
            result: dict[str, list[str]] = {}
            for row in rows:
                result.setdefault(row["tablename"], []).append(row["indexdef"])
            return result


async def explain_query(sql: str) -> list[dict[str, Any]]:
    """Run EXPLAIN (FORMAT JSON) on a query without executing it.

    Returns the parsed JSON plan. Uses a short 5s timeout since EXPLAIN
    only runs the planner (no actual execution).
    """
    pool = _get_pool()
    schema = _active_schema

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SET statement_timeout TO '5s'")
            await cur.execute(f"SET search_path TO {schema}")
            await cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
            rows = await cur.fetchall()
            # EXPLAIN FORMAT JSON returns a single row with a single column
            if rows:
                plan_col = list(rows[0].values())[0]
                # psycopg may return it already parsed or as a string
                if isinstance(plan_col, str):
                    import json
                    return json.loads(plan_col)
                return plan_col
            return []


async def fetch_concept_catalog() -> list[dict[str, Any]]:
    """Fetch all distinct concept IDs actually used in clinical tables."""
    pool = _get_pool()
    schema = _active_schema

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
    schema = _active_schema

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


async def test_connection(
    conninfo: str,
    schema: str,
    ssh_config: dict | None = None,
) -> tuple[bool, str]:
    """Test a database connection. Returns (success, message).

    If ssh_config is provided, creates a temporary tunnel for the test
    and tears it down afterward.
    """
    import psycopg

    tunnel = None
    effective_conninfo = conninfo

    try:
        if ssh_config:
            tunnel = _create_tunnel(ssh_config)
            import re

            effective_conninfo = re.sub(
                r"host=\S+", f"host=127.0.0.1", effective_conninfo
            )
            effective_conninfo = re.sub(
                r"port=\S+", f"port={tunnel.local_bind_port}", effective_conninfo
            )

        async with await psycopg.AsyncConnection.connect(effective_conninfo) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"SET search_path TO {schema}")
                await cur.execute(
                    f"SELECT COUNT(*) AS n FROM {schema}.person LIMIT 1"
                )
                row = await cur.fetchone()
                n = row["n"] if row else 0
                suffix = " (via SSH tunnel)" if ssh_config else ""
                return True, f"Connected{suffix}. {n:,} patients in {schema}.person."
    except Exception as e:
        return False, str(e)
    finally:
        if tunnel is not None:
            try:
                tunnel.stop()
            except Exception:
                pass
