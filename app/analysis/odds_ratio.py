"""Odds ratio / relative risk analysis."""

import logging
import math

from scipy import stats

from app import db
from app.analysis import register, resolve_label
from app.analysis.models import AnalysisResult

log = logging.getLogger(__name__)

_SCHEMA = "cdm_synthea"


@register("odds_ratio")
async def odds_ratio_analysis(params: dict) -> AnalysisResult:
    exposure_ids = params.get("exposure_concept_ids", [])
    outcome_ids = params.get("outcome_concept_ids", [])

    if not exposure_ids:
        raise ValueError("exposure_concept_ids is required")
    if not outcome_ids:
        raise ValueError("outcome_concept_ids is required")

    exposure_list = ", ".join(str(int(i)) for i in exposure_ids)
    outcome_list = ", ".join(str(int(i)) for i in outcome_ids)
    queries_used: list[str] = []
    warnings: list[str] = []

    # Resolve human-readable labels
    exposure_label = params.get("exposure_label") or await resolve_label(exposure_ids, "Exposed")
    outcome_label = params.get("outcome_label") or await resolve_label(outcome_ids, "Outcome")

    # Query: Build 2x2 contingency table in one query
    # exposed = has condition/drug matching exposure_ids
    # outcome = has condition matching outcome_ids
    contingency_sql = f"""
        WITH total_patients AS (
            SELECT DISTINCT person_id FROM {_SCHEMA}.person
        ),
        exposed AS (
            SELECT DISTINCT co.person_id
            FROM {_SCHEMA}.condition_occurrence co
            JOIN {_SCHEMA}.concept_ancestor ca
              ON ca.descendant_concept_id = co.condition_concept_id
            WHERE ca.ancestor_concept_id IN ({exposure_list})
        ),
        outcome AS (
            SELECT DISTINCT co.person_id
            FROM {_SCHEMA}.condition_occurrence co
            JOIN {_SCHEMA}.concept_ancestor ca
              ON ca.descendant_concept_id = co.condition_concept_id
            WHERE ca.ancestor_concept_id IN ({outcome_list})
        )
        SELECT
            COUNT(*) FILTER (WHERE e.person_id IS NOT NULL AND o.person_id IS NOT NULL) AS exposed_outcome,
            COUNT(*) FILTER (WHERE e.person_id IS NOT NULL AND o.person_id IS NULL) AS exposed_no_outcome,
            COUNT(*) FILTER (WHERE e.person_id IS NULL AND o.person_id IS NOT NULL) AS unexposed_outcome,
            COUNT(*) FILTER (WHERE e.person_id IS NULL AND o.person_id IS NULL) AS unexposed_no_outcome
        FROM total_patients tp
        LEFT JOIN exposed e ON tp.person_id = e.person_id
        LEFT JOIN outcome o ON tp.person_id = o.person_id
    """
    queries_used.append(contingency_sql.strip())
    rows = await db.execute_query(contingency_sql)

    if not rows:
        raise ValueError("Could not build contingency table.")

    r = rows[0]
    a = r["exposed_outcome"]  # exposed + outcome
    b = r["exposed_no_outcome"]  # exposed + no outcome
    c = r["unexposed_outcome"]  # unexposed + outcome
    d = r["unexposed_no_outcome"]  # unexposed + no outcome

    table = [[a, b], [c, d]]
    total = a + b + c + d

    if a + b == 0:
        raise ValueError("No patients found with the exposure.")
    if a + c == 0:
        raise ValueError("No patients found with the outcome.")

    # Choose test based on cell counts
    if min(a, b, c, d) < 5:
        or_val, p_value = stats.fisher_exact(table)
        test_name = "Fisher's exact test"
    else:
        chi2, p_value, dof, expected = stats.chi2_contingency(table)
        or_val = (a * d) / (b * c) if b * c > 0 else float("inf")
        test_name = "Chi-squared test"

    # 95% CI for log(OR) using Woolf's method
    if all(x > 0 for x in [a, b, c, d]):
        log_or = math.log(or_val) if or_val > 0 and math.isfinite(or_val) else 0
        se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
        ci_lower = math.exp(log_or - 1.96 * se)
        ci_upper = math.exp(log_or + 1.96 * se)
    else:
        ci_lower = None
        ci_upper = None
        warnings.append("Could not compute confidence interval (zero cell count).")

    no_exposure_label = f"No {exposure_label}"
    summary = {
        "odds_ratio": round(float(or_val), 3) if math.isfinite(or_val) else None,
        "ci_lower_95": round(ci_lower, 3) if ci_lower is not None else None,
        "ci_upper_95": round(ci_upper, 3) if ci_upper is not None else None,
        "p_value": round(float(p_value), 6),
        "test_used": test_name,
        "total_patients": total,
        f"n_with_{exposure_label}": a + b,
        f"n_with_{outcome_label}": a + c,
    }

    detail_columns = ["", outcome_label, f"No {outcome_label}", "Total"]
    detail_rows = [
        [exposure_label, a, b, a + b],
        [no_exposure_label, c, d, c + d],
        ["Total", a + c, b + d, total],
    ]

    return AnalysisResult(
        analysis_type="odds_ratio",
        summary=summary,
        detail_columns=detail_columns,
        detail_rows=detail_rows,
        queries_used=queries_used,
        warnings=warnings,
    )
