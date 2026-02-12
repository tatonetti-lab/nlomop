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


# ── Data source models ──


class DataSourceIn(BaseModel):
    name: str
    host: str = "localhost"
    port: int = 5432
    dbname: str = ""
    user: str = ""
    password: str = ""
    schema: str = "cdm_synthea"
    description: str = ""


class DataSourceOut(BaseModel):
    id: str
    name: str
    host: str
    port: int
    dbname: str
    user: str
    password: str  # will be masked before returning
    schema: str
    description: str
    is_active: bool = False


class DataSourceTestRequest(BaseModel):
    host: str = "localhost"
    port: int = 5432
    dbname: str = ""
    user: str = ""
    password: str = ""
    schema: str = "cdm_synthea"


class DataSourceTestResponse(BaseModel):
    ok: bool
    message: str
