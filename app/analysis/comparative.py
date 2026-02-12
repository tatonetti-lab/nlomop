"""Comparative effectiveness analysis — two drug cohorts."""

import logging

import numpy as np
from scipy import stats

from app import db
from app.analysis import register, resolve_label
from app.analysis.models import AnalysisResult

log = logging.getLogger(__name__)


@register("comparative")
async def comparative_analysis(params: dict) -> AnalysisResult:
    schema = db.get_schema()
    drug_a_ids = params.get("drug_a_concept_ids", [])
    drug_b_ids = params.get("drug_b_concept_ids", [])
    outcome_ids = params.get("outcome_concept_ids", [])
    followup_days = params.get("followup_days", 365)
    outcome_domain = params.get("outcome_domain", "auto")

    if not drug_a_ids:
        raise ValueError("drug_a_concept_ids is required")
    if not drug_b_ids:
        raise ValueError("drug_b_concept_ids is required")
    if not outcome_ids:
        raise ValueError("outcome_concept_ids is required")

    # Resolve human-readable labels from concept table
    label_a = params.get("drug_a_label") or await resolve_label(drug_a_ids, "Drug A")
    label_b = params.get("drug_b_label") or await resolve_label(drug_b_ids, "Drug B")

    drug_a_list = ", ".join(str(int(i)) for i in drug_a_ids)
    drug_b_list = ", ".join(str(int(i)) for i in drug_b_ids)
    outcome_list = ", ".join(str(int(i)) for i in outcome_ids)
    queries_used: list[str] = []
    warnings: list[str] = []

    # Query 1: Cohort A — first drug era start
    cohort_a_sql = f"""
        SELECT de.person_id, MIN(de.drug_era_start_date) AS index_date
        FROM {schema}.drug_era de
        JOIN {schema}.concept_ancestor ca
          ON ca.descendant_concept_id = de.drug_concept_id
        WHERE ca.ancestor_concept_id IN ({drug_a_list})
        GROUP BY de.person_id
    """
    queries_used.append(cohort_a_sql.strip())
    cohort_a_rows = await db.execute_query(cohort_a_sql)

    if not cohort_a_rows:
        raise ValueError(f"No patients found for {label_a}.")

    # Query 2: Cohort B — first drug era start
    cohort_b_sql = f"""
        SELECT de.person_id, MIN(de.drug_era_start_date) AS index_date
        FROM {schema}.drug_era de
        JOIN {schema}.concept_ancestor ca
          ON ca.descendant_concept_id = de.drug_concept_id
        WHERE ca.ancestor_concept_id IN ({drug_b_list})
        GROUP BY de.person_id
    """
    queries_used.append(cohort_b_sql.strip())
    cohort_b_rows = await db.execute_query(cohort_b_sql)

    if not cohort_b_rows:
        raise ValueError(f"No patients found for {label_b}.")

    cohort_a = {r["person_id"]: r["index_date"] for r in cohort_a_rows}
    cohort_b = {r["person_id"]: r["index_date"] for r in cohort_b_rows}

    # Exclude patients in both cohorts (assign to whichever they started first)
    overlap = set(cohort_a.keys()) & set(cohort_b.keys())
    for pid in overlap:
        if cohort_a[pid] <= cohort_b[pid]:
            del cohort_b[pid]
        else:
            del cohort_a[pid]

    if len(overlap) > 0:
        warnings.append(
            f"{len(overlap)} patients were on both drugs; assigned to whichever started first."
        )

    all_pids = set(cohort_a.keys()) | set(cohort_b.keys())
    pid_list = ", ".join(str(p) for p in all_pids)

    # Determine outcome domain: condition or measurement
    if outcome_domain == "auto":
        domain_sql = f"""
            SELECT domain_id FROM {schema}.concept
            WHERE concept_id IN ({outcome_list}) LIMIT 1
        """
        domain_rows = await db.execute_query(domain_sql)
        outcome_domain = domain_rows[0]["domain_id"] if domain_rows else "Condition"

    if outcome_domain == "Measurement":
        # Measurement-based: compare mean values within followup per cohort
        meas_sql = f"""
            SELECT m.person_id, AVG(m.value_as_number) AS avg_value
            FROM {schema}.measurement m
            JOIN {schema}.concept_ancestor ca
              ON ca.descendant_concept_id = m.measurement_concept_id
            WHERE ca.ancestor_concept_id IN ({outcome_list})
              AND m.person_id IN ({pid_list})
              AND m.value_as_number IS NOT NULL
            GROUP BY m.person_id
        """
        queries_used.append(meas_sql.strip())
        meas_rows = await db.execute_query(meas_sql)
        meas_map = {r["person_id"]: float(r["avg_value"]) for r in meas_rows}

        vals_a = [meas_map[p] for p in cohort_a if p in meas_map]
        vals_b = [meas_map[p] for p in cohort_b if p in meas_map]

        if len(vals_a) < 2 or len(vals_b) < 2:
            raise ValueError(
                f"Not enough measurement data ({len(vals_a)} for {label_a}, "
                f"{len(vals_b)} for {label_b})."
            )

        mean_a = float(np.mean(vals_a))
        mean_b = float(np.mean(vals_b))
        t_stat, p_value = stats.ttest_ind(vals_a, vals_b)
        test_name = "Independent t-test"

        summary = {
            f"n_{label_a}": len(vals_a),
            f"n_{label_b}": len(vals_b),
            f"mean_{label_a}": round(mean_a, 2),
            f"mean_{label_b}": round(mean_b, 2),
            "difference": round(mean_a - mean_b, 2),
            "t_statistic": round(float(t_stat), 3),
            "p_value": round(float(p_value), 6),
            "test_used": test_name,
            "followup_days": followup_days,
        }

        detail_columns = ["Group", "N Patients", "Mean Value", "Std Dev"]
        detail_rows = [
            [label_a, len(vals_a), round(mean_a, 2), round(float(np.std(vals_a, ddof=1)), 2)],
            [label_b, len(vals_b), round(mean_b, 2), round(float(np.std(vals_b, ddof=1)), 2)],
        ]

    else:
        # Condition-based: event rate comparison
        outcome_sql = f"""
            SELECT co.person_id, MIN(co.condition_start_date) AS outcome_date
            FROM {schema}.condition_occurrence co
            JOIN {schema}.concept_ancestor ca
              ON ca.descendant_concept_id = co.condition_concept_id
            WHERE ca.ancestor_concept_id IN ({outcome_list})
              AND co.person_id IN ({pid_list})
            GROUP BY co.person_id
        """
        queries_used.append(outcome_sql.strip())
        outcome_rows = await db.execute_query(outcome_sql)
        outcomes = {r["person_id"]: r["outcome_date"] for r in outcome_rows}

        def count_events(cohort: dict) -> tuple[int, int]:
            n_total = len(cohort)
            n_events = 0
            for pid, idx_date in cohort.items():
                if pid in outcomes:
                    days = (outcomes[pid] - idx_date).days
                    if 0 < days <= followup_days:
                        n_events += 1
            return n_total, n_events

        n_a, events_a = count_events(cohort_a)
        n_b, events_b = count_events(cohort_b)

        rate_a = events_a / n_a if n_a > 0 else 0
        rate_b = events_b / n_b if n_b > 0 else 0

        table = [
            [events_a, n_a - events_a],
            [events_b, n_b - events_b],
        ]

        if min(events_a, events_b, n_a - events_a, n_b - events_b) < 5:
            odds_ratio, p_value = stats.fisher_exact(table)
            test_name = "Fisher's exact test"
        else:
            chi2, p_value, dof, expected = stats.chi2_contingency(table)
            test_name = "Chi-squared test"

        relative_risk = rate_a / rate_b if rate_b > 0 else float("inf")

        summary = {
            f"n_{label_a}": n_a,
            f"n_{label_b}": n_b,
            f"events_{label_a}": events_a,
            f"events_{label_b}": events_b,
            f"rate_{label_a}": round(rate_a, 4),
            f"rate_{label_b}": round(rate_b, 4),
            "relative_risk": round(float(relative_risk), 3),
            "p_value": round(float(p_value), 6),
            "test_used": test_name,
            "followup_days": followup_days,
        }

        detail_columns = ["Group", "N Patients", "N Events", "Event Rate"]
        detail_rows = [
            [label_a, n_a, events_a, round(rate_a, 4)],
            [label_b, n_b, events_b, round(rate_b, 4)],
        ]

    return AnalysisResult(
        analysis_type="comparative",
        summary=summary,
        detail_columns=detail_columns,
        detail_rows=detail_rows,
        queries_used=queries_used,
        warnings=warnings,
    )
