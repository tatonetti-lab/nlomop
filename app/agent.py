import json
import logging
import re
import time
from typing import Any

from app import db, llm
from app.analysis import run_analysis
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


def _repair_truncated_json(text: str) -> str:
    """Try to close open braces/brackets in truncated JSON."""
    # Escape bare newlines/tabs inside JSON strings (invalid JSON but common in LLM output)
    fixed_chars = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            fixed_chars.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            fixed_chars.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            fixed_chars.append(ch)
            continue
        if in_string and ch == "\n":
            fixed_chars.append("\\n")
            continue
        if in_string and ch == "\r":
            fixed_chars.append("\\r")
            continue
        if in_string and ch == "\t":
            fixed_chars.append("\\t")
            continue
        fixed_chars.append(ch)
    text = "".join(fixed_chars)

    # Count unmatched openers
    opens = 0
    brackets = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            opens += 1
        elif ch == "}":
            opens -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1

    # If we're inside a string, close it
    if in_string:
        text += '"'
    # Close brackets then braces
    text += "]" * max(brackets, 0)
    text += "}" * max(opens, 0)
    return text


class _ParseResult:
    """Wrapper to track whether JSON was repaired from truncated output."""

    def __init__(self, data: dict[str, Any], repaired: bool = False):
        self.data = data
        self.repaired = repaired


def _parse_llm_json(text: str) -> _ParseResult:
    """Leniently parse JSON from LLM output. Returns _ParseResult."""
    text = text.strip()
    # Try raw JSON
    try:
        return _ParseResult(json.loads(text))
    except json.JSONDecodeError:
        pass
    # Try extracting from code fences
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return _ParseResult(json.loads(m.group(1).strip()))
        except json.JSONDecodeError:
            pass
    # Try finding first { ... }
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return _ParseResult(json.loads(m.group()))
        except json.JSONDecodeError:
            pass
    # Always try to repair incomplete JSON as a last resort
    m = re.search(r"\{.*", text, re.DOTALL)
    if m:
        repaired = _repair_truncated_json(m.group())
        try:
            return _ParseResult(json.loads(repaired), repaired=True)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from LLM output: {text[:200]}")


async def answer(question: str) -> QueryResponse:
    t0 = time.monotonic()
    model = llm.get_deployment()
    system_prompt = build_system_prompt()

    # First LLM call
    try:
        raw, finish_reason = await llm.chat(system_prompt, question)
    except Exception as e:
        log.exception("LLM call failed")
        return QueryResponse(
            question=question,
            error=f"LLM error: {e}",
            elapsed_s=time.monotonic() - t0,
            model=model,
        )

    # Parse response — retry with concise prompt if truncated/incomplete
    try:
        result = _parse_llm_json(raw)
        parsed = result.data
        needs_retry = result.repaired and "sql" not in parsed and "analysis" not in parsed
    except ValueError:
        parsed = {}
        needs_retry = True

    if needs_retry:
        log.warning("Response incomplete (no sql/analysis), retrying with concise prompt")
        retry_msg = (
            "IMPORTANT: Keep your response SHORT. Use 1 sentence for thinking.\n"
            "Return ONLY compact JSON with no extra text. The question is:\n" + question
        )
        try:
            raw, _fr = await llm.chat(system_prompt, retry_msg)
            retry_result = _parse_llm_json(raw)
            parsed = retry_result.data
        except Exception as e:
            log.exception("Retry after truncation failed")
            return QueryResponse(
                question=question,
                error=f"Response was truncated and retry failed: {e}",
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
            raw, _fr = await llm.chat(system_prompt, retry_msg)
            result = _parse_llm_json(raw)
            parsed = result.data
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

    # Handle analysis requests
    if "analysis" in parsed:
        analysis_spec = parsed["analysis"]
        try:
            result = await run_analysis(analysis_spec["type"], analysis_spec.get("params", {}))
            return QueryResponse(
                question=question,
                thinking=thinking,
                explanation=explanation,
                concepts_used=concepts_used,
                analysis_result=result.model_dump(),
                analysis_queries=result.queries_used,
                elapsed_s=round(time.monotonic() - t0, 2),
                model=model,
            )
        except Exception as e:
            # Analysis failed — fall back to SQL generation
            log.warning("Analysis failed (%s), falling back to SQL: %s", analysis_spec.get("type"), e)
            fallback_msg = (
                "The statistical analysis approach failed for this question. "
                "Answer it with a regular SQL query instead. "
                "Do NOT use the analysis key. Return only sql.\n\n" + question
            )
            try:
                raw, _fr = await llm.chat(system_prompt, fallback_msg)
                fb_result = _parse_llm_json(raw)
                parsed = fb_result.data
                sql = parsed.get("sql", "")
                thinking = parsed.get("thinking", thinking)
                explanation = parsed.get("explanation", explanation)
                concepts_raw = parsed.get("concept_ids_used", [])
                concepts_used = []
                for c in concepts_raw:
                    if isinstance(c, dict) and "id" in c and "name" in c:
                        concepts_used.append(ConceptUsed(id=c["id"], name=c["name"]))
                # Fall through to SQL execution below
            except Exception as e2:
                log.exception("SQL fallback after analysis failure also failed")
                return QueryResponse(
                    question=question,
                    thinking=thinking,
                    explanation=explanation,
                    concepts_used=concepts_used,
                    error=f"Analysis failed and SQL fallback also failed: {e2}",
                    elapsed_s=round(time.monotonic() - t0, 2),
                    model=model,
                )

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
