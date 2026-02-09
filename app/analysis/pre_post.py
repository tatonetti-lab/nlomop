"""Pre/post treatment measurement comparison."""

import logging
import math

import numpy as np
from scipy import stats

from app import db
from app.analysis import register
from app.analysis.models import AnalysisResult

log = logging.getLogger(__name__)

_SCHEMA = "cdm_synthea"


@register("pre_post")
async def pre_post_analysis(params: dict) -> AnalysisResult:
    drug_ids = params.get("drug_concept_ids", [])
    meas_ids = params.get("measurement_concept_ids", [])
    window_days = params.get("window_days", 30)

    if not drug_ids:
        raise ValueError("drug_concept_ids is required")
    if not meas_ids:
        raise ValueError("measurement_concept_ids is required")

    drug_list = ", ".join(str(int(i)) for i in drug_ids)
    meas_list = ", ".join(str(int(i)) for i in meas_ids)
    queries_used: list[str] = []
    warnings: list[str] = []

    # Query 1: First drug exposure per patient (using drug_era for ingredient-level)
    drug_sql = f"""
        SELECT person_id, MIN(drug_era_start_date) AS first_drug_date
        FROM {_SCHEMA}.drug_era
        JOIN {_SCHEMA}.concept_ancestor ca
          ON ca.descendant_concept_id = drug_concept_id
        WHERE ca.ancestor_concept_id IN ({drug_list})
        GROUP BY person_id
    """
    queries_used.append(drug_sql.strip())
    drug_rows = await db.execute_query(drug_sql)

    if not drug_rows:
        raise ValueError("No patients found with the specified drug exposure.")

    # Build person -> first_drug_date
    drug_dates = {r["person_id"]: r["first_drug_date"] for r in drug_rows}
    pid_list = ", ".join(str(p) for p in drug_dates.keys())

    # Query 2: Pre-treatment measurements (last value within window before drug start)
    pre_sql = f"""
        SELECT m.person_id,
               m.value_as_number AS value,
               m.measurement_date
        FROM {_SCHEMA}.measurement m
        JOIN {_SCHEMA}.concept_ancestor ca
          ON ca.descendant_concept_id = m.measurement_concept_id
        WHERE ca.ancestor_concept_id IN ({meas_list})
          AND m.person_id IN ({pid_list})
          AND m.value_as_number IS NOT NULL
    """
    queries_used.append(pre_sql.strip())
    meas_rows = await db.execute_query(pre_sql)

    # Separate into pre and post per patient
    pre_values: dict[int, float] = {}
    post_values: dict[int, float] = {}

    for r in meas_rows:
        pid = r["person_id"]
        if pid not in drug_dates:
            continue
        drug_dt = drug_dates[pid]
        meas_dt = r["measurement_date"]
        diff = (meas_dt - drug_dt).days

        # Pre: within window before drug
        if -window_days <= diff < 0:
            # Keep closest to drug date (highest diff, i.e., closest to 0)
            if pid not in pre_values or diff > pre_values.get(f"_pre_diff_{pid}", -9999):
                pre_values[pid] = float(r["value"])
                pre_values[f"_pre_diff_{pid}"] = diff

        # Post: within window after drug
        elif 0 < diff <= window_days:
            # Keep closest to drug date (lowest diff)
            if pid not in post_values or diff < post_values.get(f"_post_diff_{pid}", 9999):
                post_values[pid] = float(r["value"])
                post_values[f"_post_diff_{pid}"] = diff

    # Find patients with both pre and post
    paired_pids = [p for p in pre_values if not str(p).startswith("_") and p in post_values and not str(p).startswith("_")]
    # Clean up: filter only integer keys
    paired_pids = [p for p in drug_dates.keys() if p in pre_values and p in post_values]

    if len(paired_pids) < 2:
        raise ValueError(
            f"Only {len(paired_pids)} patient(s) with both pre and post measurements "
            f"within {window_days} days. Need at least 2 for statistical testing."
        )

    if len(paired_pids) < 20:
        warnings.append(
            f"Small sample size ({len(paired_pids)} patients). Results may not be reliable."
        )

    pre_arr = np.array([pre_values[p] for p in paired_pids])
    post_arr = np.array([post_values[p] for p in paired_pids])
    changes = post_arr - pre_arr

    # Paired t-test
    t_stat, p_value = stats.ttest_rel(pre_arr, post_arr)

    mean_pre = float(np.mean(pre_arr))
    mean_post = float(np.mean(post_arr))
    mean_change = float(np.mean(changes))
    std_change = float(np.std(changes, ddof=1))
    cohens_d = mean_change / std_change if std_change > 0 else 0.0

    summary = {
        "n_patients": len(paired_pids),
        "mean_pre": round(mean_pre, 2),
        "mean_post": round(mean_post, 2),
        "mean_change": round(mean_change, 2),
        "std_change": round(std_change, 2),
        "t_statistic": round(float(t_stat), 3),
        "p_value": round(float(p_value), 6),
        "cohens_d": round(cohens_d, 3),
        "window_days": window_days,
    }

    # Detail table: per-patient values (limited to 50)
    detail_columns = ["Patient ID", "Pre Value", "Post Value", "Change"]
    detail_rows = []
    for p in paired_pids[:50]:
        detail_rows.append([
            p,
            round(pre_values[p], 2),
            round(post_values[p], 2),
            round(post_values[p] - pre_values[p], 2),
        ])

    if len(paired_pids) > 50:
        warnings.append(f"Showing 50 of {len(paired_pids)} patients in detail table.")

    return AnalysisResult(
        analysis_type="pre_post",
        summary=summary,
        detail_columns=detail_columns,
        detail_rows=detail_rows,
        queries_used=queries_used,
        warnings=warnings,
    )
