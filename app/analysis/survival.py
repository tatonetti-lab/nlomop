"""Kaplan-Meier survival analysis."""

import logging

import numpy as np
from lifelines import KaplanMeierFitter

from app import db
from app.analysis import register
from app.analysis.models import AnalysisResult

log = logging.getLogger(__name__)


@register("survival")
async def survival_analysis(params: dict) -> AnalysisResult:
    schema = db.get_schema()
    cohort_ids = params.get("cohort_concept_ids", [])
    time_horizon_years = params.get("time_horizon_years", 5)
    time_horizon_days = time_horizon_years * 365

    if not cohort_ids:
        raise ValueError("cohort_concept_ids is required")

    id_list = ", ".join(str(int(i)) for i in cohort_ids)
    queries_used: list[str] = []
    warnings: list[str] = []

    # Query 1: Build cohort — patients with any of the cohort concepts,
    # using first occurrence as index date
    cohort_sql = f"""
        SELECT co.person_id,
               MIN(co.condition_start_date) AS index_date
        FROM {schema}.condition_occurrence co
        JOIN {schema}.concept_ancestor ca
          ON ca.descendant_concept_id = co.condition_concept_id
        WHERE ca.ancestor_concept_id IN ({id_list})
        GROUP BY co.person_id
    """
    queries_used.append(cohort_sql.strip())

    # Also check drug_era in case the concept IDs are drugs
    drug_cohort_sql = f"""
        SELECT de.person_id,
               MIN(de.drug_era_start_date) AS index_date
        FROM {schema}.drug_era de
        JOIN {schema}.concept_ancestor ca
          ON ca.descendant_concept_id = de.drug_concept_id
        WHERE ca.ancestor_concept_id IN ({id_list})
        GROUP BY de.person_id
    """

    cohort_rows = await db.execute_query(cohort_sql)

    # If no condition matches, try drugs
    if not cohort_rows:
        queries_used.append(drug_cohort_sql.strip())
        cohort_rows = await db.execute_query(drug_cohort_sql)

    if not cohort_rows:
        raise ValueError(
            f"No patients found for concept IDs {id_list}. "
            "Check that these are valid condition or drug concept IDs."
        )

    # Build person_id -> index_date mapping
    cohort = {r["person_id"]: r["index_date"] for r in cohort_rows}
    person_ids = list(cohort.keys())
    pid_list = ", ".join(str(p) for p in person_ids)

    # Query 2: Get death dates
    death_sql = f"""
        SELECT person_id, death_date
        FROM {schema}.death
        WHERE person_id IN ({pid_list})
    """
    queries_used.append(death_sql.strip())
    death_rows = await db.execute_query(death_sql)
    deaths = {r["person_id"]: r["death_date"] for r in death_rows}

    # Query 3: Get observation period end for censoring
    obs_sql = f"""
        SELECT person_id, MAX(observation_period_end_date) AS end_date
        FROM {schema}.observation_period
        WHERE person_id IN ({pid_list})
        GROUP BY person_id
    """
    queries_used.append(obs_sql.strip())
    obs_rows = await db.execute_query(obs_sql)
    obs_end = {r["person_id"]: r["end_date"] for r in obs_rows}

    # Compute durations and event indicators
    durations = []
    events = []
    for pid in person_ids:
        idx = cohort[pid]
        if pid in deaths:
            dur = (deaths[pid] - idx).days
            event = True
        elif pid in obs_end:
            dur = (obs_end[pid] - idx).days
            event = False
        else:
            continue  # no follow-up info

        # Clamp to time horizon
        if dur > time_horizon_days:
            dur = time_horizon_days
            event = False
        if dur <= 0:
            continue

        durations.append(dur)
        events.append(event)

    if len(durations) < 10:
        warnings.append(f"Only {len(durations)} patients with valid follow-up data.")

    if not durations:
        raise ValueError("No patients with valid follow-up data for survival analysis.")

    # Fit Kaplan-Meier
    kmf = KaplanMeierFitter()
    kmf.fit(durations, event_observed=events, timeline=range(0, time_horizon_days + 1, 30))

    n_patients = len(durations)
    n_events = sum(events)
    median_survival = kmf.median_survival_time_

    # Extract survival at yearly intervals
    summary = {
        "n_patients": n_patients,
        "n_events": n_events,
        "median_survival_days": (
            round(float(median_survival)) if np.isfinite(median_survival) else None
        ),
    }

    for yr in range(1, time_horizon_years + 1):
        day = yr * 365
        if day <= time_horizon_days:
            try:
                surv = kmf.predict(day)
                summary[f"survival_at_{yr}yr"] = round(float(surv), 3)
            except Exception:
                pass

    # Build detail table: time points with survival, CI, at-risk
    detail_columns = ["Day", "Survival Probability", "CI Lower (95%)", "CI Upper (95%)"]
    detail_rows = []

    ci = kmf.confidence_interval_survival_function_
    sf = kmf.survival_function_

    # Sample at yearly + 6-month intervals
    time_points = sorted(
        set([0] + [yr * 365 for yr in range(1, time_horizon_years + 1)]
            + [yr * 365 + 182 for yr in range(time_horizon_years)])
    )
    time_points = [t for t in time_points if t <= time_horizon_days]

    for t in time_points:
        # Find closest timeline index
        idx = sf.index[sf.index <= t]
        if len(idx) == 0:
            continue
        closest = idx[-1]
        surv_val = float(sf.iloc[:, 0].loc[closest])
        ci_low = float(ci.iloc[:, 0].loc[closest])
        ci_high = float(ci.iloc[:, 1].loc[closest])
        detail_rows.append([t, round(surv_val, 4), round(ci_low, 4), round(ci_high, 4)])

    return AnalysisResult(
        analysis_type="survival",
        summary=summary,
        detail_columns=detail_columns,
        detail_rows=detail_rows,
        queries_used=queries_used,
        warnings=warnings,
    )
