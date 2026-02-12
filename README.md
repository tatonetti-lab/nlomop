# nlomop

Natural Language OMOP — a conversational tool that translates plain-English questions about electronic health records into SQL queries against an [OMOP CDM v5.4](https://ohdsi.github.io/CommonDataModel/) PostgreSQL database.

The system maps clinical terms to OMOP concept IDs, generates SQL via Azure OpenAI, executes it against the database, and presents results in a chat UI. For statistical questions (survival analysis, odds ratios, etc.), it routes to pre-built Python analysis functions instead of raw SQL.

## Features

- **Natural language to SQL** — ask clinical questions in plain English, get SQL results
- **Concept resolution** — automatically maps terms like "diabetes" or "metformin" to OMOP concept IDs using a startup-loaded concept cache
- **Statistical analyses** — Kaplan-Meier survival, pre/post treatment comparison, comparative effectiveness, odds ratios, and correlation analysis via pre-built Python functions (scipy, lifelines)
- **SQL IDE** — direct SQL editor with syntax highlighting for ad-hoc queries
- **Multi-data-source management** — add, edit, test, and switch between multiple OMOP databases at runtime through the UI
- **Read-only safety** — all queries are read-only with configurable timeouts

## Stack

- **Backend**: Python, FastAPI, psycopg v3 (async), Azure OpenAI
- **Frontend**: vanilla HTML/CSS/JS
- **Statistics**: scipy, lifelines, numpy
- **Database**: PostgreSQL with OMOP CDM v5.4 schema

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL with an OMOP CDM v5.4 schema loaded
- Azure OpenAI API access

### Setup

```bash
git clone git@github.com:tatonetti-lab/nlomop.git
cd nlomop
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required environment variables:

| Variable | Description |
|---|---|
| `NLOMOP_AZURE_OPENAI__ENDPOINT` | Azure OpenAI endpoint URL |
| `NLOMOP_AZURE_OPENAI__API_KEY` | API key |
| `NLOMOP_AZURE_OPENAI__DEPLOYMENT` | Model deployment name (default: `gpt-5-mini`) |
| `NLOMOP_DB__HOST` | PostgreSQL host (default: `localhost`) |
| `NLOMOP_DB__PORT` | PostgreSQL port (default: `5432`) |
| `NLOMOP_DB__NAME` | Database name |
| `NLOMOP_DB__USER` | Database user |
| `NLOMOP_DB__PASSWORD` | Database password |
| `NLOMOP_DB__SCHEMA` | OMOP CDM schema name (default: `cdm_synthea`) |

### Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

On first startup, the app seeds a `data_sources.json` file from your `.env` database config and loads the concept cache. Additional data sources can be managed through the settings UI.

## Project Structure

```
app/
  main.py           FastAPI app, lifespan, API routes
  config.py         Pydantic Settings from .env
  db.py             Async connection pool, query execution, source switching
  llm.py            Azure OpenAI wrapper
  agent.py          Orchestrator: question -> LLM -> SQL/analysis -> response
  prompts.py        System prompt builder
  models.py         Pydantic request/response schemas
  concept_cache.py  Concept catalog loaded at startup
  datasources.py    Data source CRUD and JSON persistence
  analysis/
    __init__.py     Registry and dispatcher
    models.py       AnalysisResult model
    survival.py     Kaplan-Meier survival analysis
    pre_post.py     Pre/post treatment comparison (paired t-test)
    comparative.py  Two-group comparative effectiveness
    odds_ratio.py   2x2 contingency table with odds ratio
    correlation.py  Pearson + Spearman correlation
static/
  index.html        Chat UI
  app.js            Frontend logic
  style.css         Styles
```

## Statistical Analyses

| Analysis | Method | Example Question |
|---|---|---|
| Survival | Kaplan-Meier (lifelines) | "5-year survival of patients with diabetes" |
| Pre/Post | Paired t-test (scipy) | "Effect of statins on cholesterol within 30 days" |
| Comparative | Chi-squared / t-test | "Compare ACE inhibitors vs ARBs for BP outcomes" |
| Odds Ratio | Fisher's exact / Chi-squared | "Odds ratio of CKD given diabetes" |
| Correlation | Pearson + Spearman | "Correlation between BMI and systolic BP" |

The LLM identifies the analysis type and parameters; backend functions handle the computation.

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial 4.0 International License](LICENSE). Non-commercial use is permitted; commercial use is not.
