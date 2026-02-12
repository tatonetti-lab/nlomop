"""Measurement-measurement correlation analysis."""

import logging

import numpy as np
from scipy import stats

from app import db
from app.analysis import register, resolve_label
from app.analysis.models import AnalysisResult

log = logging.getLogger(__name__)


@register("correlation")
async def correlation_analysis(params: dict) -> AnalysisResult:
    schema = db.get_schema()
    meas_a_ids = params.get("measurement_a_concept_ids", [])
    meas_b_ids = params.get("measurement_b_concept_ids", [])
    same_day = params.get("same_day", True)

    if not meas_a_ids:
        raise ValueError("measurement_a_concept_ids is required")
    if not meas_b_ids:
        raise ValueError("measurement_b_concept_ids is required")

    meas_a_list = ", ".join(str(int(i)) for i in meas_a_ids)
    meas_b_list = ", ".join(str(int(i)) for i in meas_b_ids)
    queries_used: list[str] = []
    warnings: list[str] = []

    # Resolve human-readable labels
    label_a = params.get("measurement_a_label") or await resolve_label(meas_a_ids, "Measurement A")
    label_b = params.get("measurement_b_label") or await resolve_label(meas_b_ids, "Measurement B")

    if same_day:
        # Join on same patient + same date
        pair_sql = f"""
            SELECT
                a.person_id,
                a.value_as_number AS value_a,
                b.value_as_number AS value_b,
                a.measurement_date
            FROM {schema}.measurement a
            JOIN {schema}.concept_ancestor ca_a
              ON ca_a.descendant_concept_id = a.measurement_concept_id
            JOIN {schema}.measurement b
              ON b.person_id = a.person_id
              AND b.measurement_date = a.measurement_date
            JOIN {schema}.concept_ancestor ca_b
              ON ca_b.descendant_concept_id = b.measurement_concept_id
            WHERE ca_a.ancestor_concept_id IN ({meas_a_list})
              AND ca_b.ancestor_concept_id IN ({meas_b_list})
              AND a.value_as_number IS NOT NULL
              AND b.value_as_number IS NOT NULL
            LIMIT 10000
        """
    else:
        # Use closest measurements per patient (avg per patient)
        pair_sql = f"""
            WITH avg_a AS (
                SELECT m.person_id, AVG(m.value_as_number) AS value_a
                FROM {schema}.measurement m
                JOIN {schema}.concept_ancestor ca
                  ON ca.descendant_concept_id = m.measurement_concept_id
                WHERE ca.ancestor_concept_id IN ({meas_a_list})
                  AND m.value_as_number IS NOT NULL
                GROUP BY m.person_id
            ),
            avg_b AS (
                SELECT m.person_id, AVG(m.value_as_number) AS value_b
                FROM {schema}.measurement m
                JOIN {schema}.concept_ancestor ca
                  ON ca.descendant_concept_id = m.measurement_concept_id
                WHERE ca.ancestor_concept_id IN ({meas_b_list})
                  AND m.value_as_number IS NOT NULL
                GROUP BY m.person_id
            )
            SELECT a.person_id, a.value_a, b.value_b
            FROM avg_a a
            JOIN avg_b b ON a.person_id = b.person_id
            LIMIT 10000
        """
    queries_used.append(pair_sql.strip())
    rows = await db.execute_query(pair_sql)

    if len(rows) < 3:
        raise ValueError(
            f"Only {len(rows)} paired measurements found. Need at least 3 for correlation."
        )

    if len(rows) < 20:
        warnings.append(f"Small sample size ({len(rows)} pairs). Results may not be reliable.")

    vals_a = np.array([float(r["value_a"]) for r in rows])
    vals_b = np.array([float(r["value_b"]) for r in rows])

    # Pearson correlation
    pearson_r, pearson_p = stats.pearsonr(vals_a, vals_b)

    # Spearman correlation
    spearman_r, spearman_p = stats.spearmanr(vals_a, vals_b)

    summary = {
        "n_pairs": len(rows),
        "pearson_r": round(float(pearson_r), 4),
        "pearson_p": round(float(pearson_p), 6),
        "spearman_r": round(float(spearman_r), 4),
        "spearman_p": round(float(spearman_p), 6),
        f"mean_{label_a}": round(float(np.mean(vals_a)), 2),
        f"mean_{label_b}": round(float(np.mean(vals_b)), 2),
        "same_day_pairing": same_day,
    }

    # Detail: show summary statistics for each measurement
    detail_columns = ["Statistic", label_a, label_b]
    detail_rows = [
        ["N", len(rows), len(rows)],
        ["Mean", round(float(np.mean(vals_a)), 2), round(float(np.mean(vals_b)), 2)],
        ["Std Dev", round(float(np.std(vals_a, ddof=1)), 2), round(float(np.std(vals_b, ddof=1)), 2)],
        ["Min", round(float(np.min(vals_a)), 2), round(float(np.min(vals_b)), 2)],
        ["Median", round(float(np.median(vals_a)), 2), round(float(np.median(vals_b)), 2)],
        ["Max", round(float(np.max(vals_a)), 2), round(float(np.max(vals_b)), 2)],
        ["Pearson r", round(float(pearson_r), 4), ""],
        ["Pearson p-value", round(float(pearson_p), 6), ""],
        ["Spearman r", round(float(spearman_r), 4), ""],
        ["Spearman p-value", round(float(spearman_p), 6), ""],
    ]

    return AnalysisResult(
        analysis_type="correlation",
        summary=summary,
        detail_columns=detail_columns,
        detail_rows=detail_rows,
        queries_used=queries_used,
        warnings=warnings,
    )
