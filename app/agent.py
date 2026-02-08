import json
import logging
import re
import time
from typing import Any

from app import db, llm
from app.models import ConceptUsed, QueryResponse
from app.prompts import build_system_prompt

log = logging.getLogger(__name__)

_BLOCKED = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> str | None:
    """Return an error message if the SQL is not a safe SELECT, else None."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "Empty SQL"
    first_word = stripped.split()[0].upper()
    if first_word not in ("SELECT", "WITH"):
        return f"Only SELECT queries allowed, got: {first_word}"
    if _BLOCKED.search(stripped):
        match = _BLOCKED.search(stripped)
        return f"Blocked SQL keyword: {match.group() if match else 'unknown'}"
    return None


def _parse_llm_json(text: str) -> dict[str, Any]:
    """Leniently parse JSON from LLM output."""
    text = text.strip()
    # Try raw JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from code fences
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try finding first { ... }
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from LLM output: {text[:200]}")


async def answer(question: str) -> QueryResponse:
    t0 = time.monotonic()
    model = llm.get_deployment()
    system_prompt = build_system_prompt()

    # First LLM call
    try:
        raw = await llm.chat(system_prompt, question)
    except Exception as e:
        log.exception("LLM call failed")
        return QueryResponse(
            question=question,
            error=f"LLM error: {e}",
            elapsed_s=time.monotonic() - t0,
            model=model,
        )

    # Parse response
    try:
        parsed = _parse_llm_json(raw)
    except ValueError as e:
        return QueryResponse(
            question=question,
            error=str(e),
            elapsed_s=time.monotonic() - t0,
            model=model,
        )

    # Handle concept_search fallback
    if "concept_search" in parsed and "sql" not in parsed:
        term = parsed["concept_search"]
        log.info("Concept search fallback for: %s", term)
        try:
            results = await db.search_concepts(term)
            if not results:
                return QueryResponse(
                    question=question,
                    thinking=parsed.get("thinking", ""),
                    error=f"No concepts found matching '{term}'",
                    elapsed_s=time.monotonic() - t0,
                )
            # Format results and re-prompt
            concept_text = "\n".join(
                f"- {r['concept_id']}: {r['concept_name']} (domain={r['domain_id']}, vocab={r['vocabulary_id']}, class={r['concept_class_id']})"
                for r in results
            )
            retry_msg = (
                f"The system searched for '{term}' and found these concepts:\n"
                f"{concept_text}\n\n"
                f"Now answer the original question using the appropriate concept(s):\n{question}"
            )
            raw = await llm.chat(system_prompt, retry_msg)
            parsed = _parse_llm_json(raw)
        except Exception as e:
            log.exception("Concept search fallback failed")
            return QueryResponse(
                question=question,
                thinking=parsed.get("thinking", ""),
                error=f"Concept search failed: {e}",
                elapsed_s=time.monotonic() - t0,
            )

    sql = parsed.get("sql", "")
    thinking = parsed.get("thinking", "")
    explanation = parsed.get("explanation", "")
    concepts_raw = parsed.get("concept_ids_used", [])

    concepts_used = []
    for c in concepts_raw:
        if isinstance(c, dict) and "id" in c and "name" in c:
            concepts_used.append(ConceptUsed(id=c["id"], name=c["name"]))

    if not sql:
        return QueryResponse(
            question=question,
            thinking=thinking,
            explanation=explanation,
            concepts_used=concepts_used,
            error="LLM did not return SQL",
            elapsed_s=time.monotonic() - t0,
            model=model,
        )

    # Validate SQL
    err = _validate_sql(sql)
    if err:
        return QueryResponse(
            question=question,
            thinking=thinking,
            sql=sql,
            explanation=explanation,
            concepts_used=concepts_used,
            error=f"SQL validation failed: {err}",
            elapsed_s=time.monotonic() - t0,
            model=model,
        )

    # Execute query
    try:
        rows = await db.execute_query(sql)
    except Exception as e:
        err_str = str(e)
        if "canceling statement due to statement timeout" in err_str:
            err_str = "Query timed out (exceeded 30s). Try a more specific question."
        elif "cannot execute" in err_str and "read-only" in err_str:
            err_str = "Query blocked: only read-only SELECT queries are allowed."
        return QueryResponse(
            question=question,
            thinking=thinking,
            sql=sql,
            explanation=explanation,
            concepts_used=concepts_used,
            error=f"Query error: {err_str}",
            elapsed_s=time.monotonic() - t0,
            model=model,
        )

    # Format results
    columns = list(rows[0].keys()) if rows else []
    row_values = [_serialize_row(r, columns) for r in rows]

    return QueryResponse(
        question=question,
        thinking=thinking,
        sql=sql,
        explanation=explanation,
        columns=columns,
        rows=row_values,
        row_count=len(rows),
        concepts_used=concepts_used,
        elapsed_s=round(time.monotonic() - t0, 2),
        model=model,
    )


def _serialize_row(row: dict[str, Any], columns: list[str]) -> list:
    """Convert a row dict to a list, making values JSON-serializable."""
    result = []
    for col in columns:
        val = row[col]
        if isinstance(val, (int, float, str, bool, type(None))):
            result.append(val)
        else:
            result.append(str(val))
    return result
