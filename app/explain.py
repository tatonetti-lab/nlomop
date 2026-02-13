"""EXPLAIN plan analysis for pre-flight query cost estimation."""

import logging
from typing import Any

log = logging.getLogger(__name__)

# Tables with enough rows that a sequential scan is worth warning about
LARGE_TABLES: dict[str, int] = {
    "measurement": 14_900_000,
    "observation": 8_000_000,
    "cost": 4_900_000,
    "concept_relationship": 46_000_000,
    "concept_ancestor": 38_000_000,
}

# Suggested indexes: table -> {column, sql}
# Used to check whether the index already exists before suggesting it.
INDEX_SUGGESTIONS: dict[str, dict[str, str]] = {
    "measurement": {
        "column": "measurement_concept_id",
        "sql": "CREATE INDEX IF NOT EXISTS idx_measurement_concept ON measurement(measurement_concept_id);",
    },
    "observation": {
        "column": "observation_concept_id",
        "sql": "CREATE INDEX IF NOT EXISTS idx_observation_concept ON observation(observation_concept_id);",
    },
    "condition_occurrence": {
        "column": "condition_concept_id",
        "sql": "CREATE INDEX IF NOT EXISTS idx_condition_concept ON condition_occurrence(condition_concept_id);",
    },
    "drug_exposure": {
        "column": "drug_concept_id",
        "sql": "CREATE INDEX IF NOT EXISTS idx_drug_exposure_concept ON drug_exposure(drug_concept_id);",
    },
    "procedure_occurrence": {
        "column": "procedure_concept_id",
        "sql": "CREATE INDEX IF NOT EXISTS idx_procedure_concept ON procedure_occurrence(procedure_concept_id);",
    },
}

# Threshold for warning about a seq scan (estimated rows)
_SEQ_SCAN_ROW_THRESHOLD = 100_000
# Threshold for warning about total plan cost
# 25M roughly correlates to queries that approach the default 30s timeout.
# A 10M-cost query typically completes in ~10s on this hardware.
_HIGH_COST_THRESHOLD = 25_000_000


def _has_column_index(table: str, column: str, existing_indexes: dict[str, list[str]]) -> bool:
    """Check if any existing index on `table` covers `column`."""
    for indexdef in existing_indexes.get(table, []):
        if column in indexdef:
            return True
    return False


def analyze_plan(
    plan_json: list[dict[str, Any]],
    existing_indexes: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Analyze an EXPLAIN (FORMAT JSON) result and return warnings.

    Args:
        plan_json: The parsed EXPLAIN JSON output.
        existing_indexes: {table: [indexdef, ...]} from pg_indexes.
            When provided, seq scan warnings are suppressed for tables
            that already have the relevant index, and only missing
            indexes are suggested.

    Returns dict with keys:
        warnings: list[str]       - human-readable warning messages
        estimated_cost: float     - total estimated cost from planner
        estimated_rows: int       - top-level estimated rows
        seq_scans: list[dict]     - seq scan nodes found on large tables
        index_suggestions: list[str] - CREATE INDEX statements for missing indexes
    """
    if not plan_json:
        return {
            "warnings": [],
            "estimated_cost": 0,
            "estimated_rows": 0,
            "seq_scans": [],
            "index_suggestions": [],
        }

    if existing_indexes is None:
        existing_indexes = {}

    top = plan_json[0].get("Plan", {})
    total_cost = top.get("Total Cost", 0)
    estimated_rows = top.get("Plan Rows", 0)

    seq_scans: list[dict[str, Any]] = []
    _walk_plan(top, seq_scans)

    warnings: list[str] = []
    suggestions: list[str] = []
    seen_tables: set[str] = set()

    for scan in seq_scans:
        table = scan["table"]
        rows = scan["rows"]
        if table in seen_tables:
            continue
        seen_tables.add(table)

        suggestion_info = INDEX_SUGGESTIONS.get(table)
        if suggestion_info:
            has_index = _has_column_index(table, suggestion_info["column"], existing_indexes)
            if not has_index:
                # No index exists — warn and suggest
                warnings.append(
                    f"Sequential scan on {table} (~{rows:,} estimated rows). "
                    f"No index on {suggestion_info['column']}."
                )
                suggestions.append(suggestion_info["sql"])
            # If index exists, planner chose seq scan deliberately — don't warn
        else:
            # Large table without a known suggestion — still warn
            warnings.append(
                f"Sequential scan on {table} (~{rows:,} estimated rows). "
                f"This may be slow."
            )

    if total_cost > _HIGH_COST_THRESHOLD:
        # Identify the most expensive table scans to give specific advice
        all_scans: list[dict[str, Any]] = []
        _collect_table_scans(top, all_scans)
        # Deduplicate by table, keeping the highest cost per table
        by_table: dict[str, dict[str, Any]] = {}
        for s in all_scans:
            t = s["table"]
            if t not in by_table or s["cost"] > by_table[t]["cost"]:
                by_table[t] = s
        expensive = sorted(by_table.values(), key=lambda s: s["cost"], reverse=True)[:3]

        if expensive:
            table_list = ", ".join(
                f"{s['table']} (~{s['rows']:,} rows via {s['type']})"
                for s in expensive
            )
            warnings.append(
                f"High estimated query cost ({total_cost:,.0f}). "
                f"Most expensive table scans: {table_list}. "
                f"Try adding a date range (e.g. WHERE ... > '2020-01-01') "
                f"or narrowing the patient cohort to reduce data scanned."
            )
        else:
            warnings.append(
                f"High estimated query cost ({total_cost:,.0f}). "
                f"Try adding a date range or narrowing filters to reduce data scanned."
            )

    return {
        "warnings": warnings,
        "estimated_cost": total_cost,
        "estimated_rows": estimated_rows,
        "seq_scans": seq_scans,
        "index_suggestions": suggestions,
    }


def _walk_plan(node: dict[str, Any], seq_scans: list[dict[str, Any]]) -> None:
    """Recursively walk the plan tree collecting Seq Scan nodes on large tables."""
    node_type = node.get("Node Type", "")
    if node_type == "Seq Scan":
        table = node.get("Relation Name", "")
        rows = node.get("Plan Rows", 0)
        if table in LARGE_TABLES or rows >= _SEQ_SCAN_ROW_THRESHOLD:
            seq_scans.append({
                "table": table,
                "rows": rows,
                "cost": node.get("Total Cost", 0),
            })

    # Recurse into child plans
    for child in node.get("Plans", []):
        _walk_plan(child, seq_scans)


_SCAN_NODE_TYPES = {
    "Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan",
}


def _collect_table_scans(node: dict[str, Any], scans: list[dict[str, Any]]) -> None:
    """Recursively collect ALL table scan nodes (any scan type) with their costs."""
    node_type = node.get("Node Type", "")
    table = node.get("Relation Name", "")
    if table and node_type in _SCAN_NODE_TYPES:
        scans.append({
            "table": table,
            "type": node_type.replace(" Scan", "").replace(" Only", " Only"),
            "rows": node.get("Plan Rows", 0),
            "cost": node.get("Total Cost", 0),
        })

    for child in node.get("Plans", []):
        _collect_table_scans(child, scans)
