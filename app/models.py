from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str


class SqlRequest(BaseModel):
    sql: str


class ConceptUsed(BaseModel):
    id: int
    name: str


class QueryResponse(BaseModel):
    question: str
    thinking: str = ""
    sql: str = ""
    explanation: str = ""
    columns: list[str] = []
    rows: list[list] = []
    row_count: int = 0
    concepts_used: list[ConceptUsed] = []
    error: str = ""
    elapsed_s: float = 0.0
    model: str = ""
    analysis_result: dict | None = None
    analysis_queries: list[str] = []
