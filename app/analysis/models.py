from pydantic import BaseModel


class AnalysisResult(BaseModel):
    analysis_type: str
    summary: dict  # key stats (e.g., {"median_survival_days": 365, "p_value": 0.03})
    detail_columns: list[str]  # column headers for detail table
    detail_rows: list[list]  # tabular detail data
    queries_used: list[str]  # SQL queries executed (for transparency)
    warnings: list[str] = []  # data quality notes
