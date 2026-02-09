# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**nlomop** (Natural Language OMOP) is a conversational tool that translates plain-English questions about electronic health records into SQL queries against an OMOP CDM v5.4 PostgreSQL database.

The system maps clinical terms to OMOP concept IDs, generates SQL via Azure OpenAI, executes it against the database, and presents results in a chat UI. For statistical questions (survival analysis, odds ratios, etc.), it routes to pre-built Python analysis functions instead of raw SQL. Stack: Python/FastAPI backend, vanilla HTML/CSS/JS frontend.

## Database

PostgreSQL on `localhost:5432`, database `synthea10`, schema `cdm_synthea`, user `TatonettiN`, no password. 11,463 synthetic patients with full lifetime records.

**Large tables to be aware of**: measurement (14.9M rows), observation (8M rows), cost (4.9M rows), concept_relationship (46M rows), concept_ancestor (38M rows). No secondary indexes on clinical tables by default — queries on these can be slow without tight filtering.

## Critical OMOP CDM Patterns

- **Concept resolution is the hardest part.** Every clinical query requires mapping natural language terms to `concept_id` integers via the `concept` table. Always filter `standard_concept = 'S'` and the appropriate `domain_id`.

- **Drug hierarchy**: Users say ingredient names ("metformin") but `drug_exposure` stores Clinical Drug concepts. Use `concept_ancestor` to bridge from ingredient to all formulations. The `drug_era` table is already at ingredient level.

- **Demographics pitfall**: Gender/race/ethnicity concept IDs (8507, 8532, 8527, etc.) are NOT in the local `concept` table. Never JOIN `person.gender_concept_id` to `concept` — use CASE expressions instead.

- **Hierarchical queries**: Use `concept_ancestor` to find all subtypes (e.g., all types of diabetes via ancestor 4008576). Every concept is its own ancestor at `min_levels_of_separation = 0`.

- **Vocabulary table safety**: Never scan `concept_relationship` (46M) or `concept_ancestor` (38M) without a WHERE clause constraining `ancestor_concept_id`, `descendant_concept_id`, or `relationship_id`.

## Key Reference Files

| File | Purpose |
|---|---|
| `PRODUCT_SPEC.md` | Product requirements, core workflow, functional requirements, example interactions |
| `DATA_DICTIONARY.md` | Complete schema reference with column types, row counts, 10 SQL query patterns, hardcoded concept ID mappings, and suggested indexes |

`DATA_DICTIONARY.md` is designed to be usable as LLM system prompt context for SQL generation.

## Build & Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# Open http://127.0.0.1:8000
```

Requires a `.env` file with `NLOMOP_AZURE_OPENAI__*` and `NLOMOP_DB__*` variables — see `.env.example`.

## Project Structure

- `app/main.py` — FastAPI app, lifespan (DB pool + concept cache init), routes
- `app/config.py` — Pydantic Settings loaded from `.env` with `NLOMOP_` prefix (default deployment: gpt-5-mini)
- `app/db.py` — psycopg v3 async pool, query execution (read-only, 30s timeout)
- `app/llm.py` — AsyncAzureOpenAI wrapper; `chat()` for main queries, `quick_chat()` for cheap utility calls (gpt-4.1-mini)
- `app/concept_cache.py` — Loads all ~1,571 distinct concepts from clinical tables at startup
- `app/prompts.py` — Builds system prompt from instructions + DATA_DICTIONARY.md + concept catalog + analysis menu
- `app/agent.py` — Orchestrator: question → LLM → parse JSON → dispatch to SQL execution or analysis → response. Includes truncated JSON repair, automatic retry on truncation, and SQL fallback when analysis fails.
- `app/models.py` — Pydantic request/response schemas (QueryResponse includes `analysis_result` and `analysis_queries` fields)
- `app/analysis/` — Statistical analysis package (see below)
- `static/` — Chat UI (index.html, style.css, app.js) with help overlay, analysis card rendering

## Statistical Analysis Extension

The system supports pre-built statistical analyses dispatched by the LLM. When a question requires statistical computation, the LLM returns `{"analysis": {"type": "...", "params": {...}}}` instead of `{"sql": "..."}`. The agent dispatches to the appropriate function which runs multiple SQL queries + Python computation (scipy/lifelines).

### Architecture

```
User question → LLM → JSON with "analysis" key
    → agent.py detects analysis request
    → dispatches to app/analysis/<type>.py
    → function runs 2-4 SQL queries via db.execute_query()
    → runs statistical computation (scipy/lifelines)
    → returns AnalysisResult (summary stats + tabular data)
    → frontend renders analysis card
```

### Analysis Types

| Type | File | Method | Example |
|---|---|---|---|
| `survival` | `app/analysis/survival.py` | Kaplan-Meier (lifelines) | "5-year survival of patients with diabetes" |
| `pre_post` | `app/analysis/pre_post.py` | Paired t-test (scipy) | "Effect of statins on cholesterol within 30 days" |
| `comparative` | `app/analysis/comparative.py` | Chi-squared / t-test | "Compare ACE inhibitors vs ARBs for BP outcomes" |
| `odds_ratio` | `app/analysis/odds_ratio.py` | Fisher's exact / Chi-squared | "Odds ratio of CKD given diabetes" |
| `correlation` | `app/analysis/correlation.py` | Pearson + Spearman | "Correlation between BMI and systolic BP" |

### Analysis Package Structure

- `app/analysis/__init__.py` — Registry (`@register` decorator), `run_analysis()` dispatcher, `resolve_label()` for human-readable concept names via cheap LLM call
- `app/analysis/models.py` — `AnalysisResult` Pydantic model (summary dict, detail table, queries used, warnings)
- `app/analysis/survival.py` — Kaplan-Meier with cohort building from conditions or drugs, death/censoring, yearly survival + 95% CI
- `app/analysis/pre_post.py` — Pre/post drug exposure measurement comparison with paired t-test
- `app/analysis/comparative.py` — Two-group comparison; auto-detects condition vs measurement outcomes; resolves drug labels from DB
- `app/analysis/odds_ratio.py` — 2x2 contingency table with odds ratio, 95% CI (Woolf), resolves exposure/outcome labels
- `app/analysis/correlation.py` — Same-day or patient-average measurement pairing, resolves measurement labels

### Key Design Decisions

- **Pre-built functions, not LLM-generated Python**: The LLM identifies the analysis type and parameters; backend functions do the computation. Safer and more predictable.
- **Label resolution via cheap LLM**: When multiple concept IDs map to a group (e.g., several CKD stages), `resolve_label()` calls gpt-4.1-mini to generate a short readable label like "Chronic Kidney Disease" instead of concatenating all names.
- **SQL fallback on analysis failure**: If analysis fails (e.g., wrong analysis type for the question), agent.py re-prompts the LLM to answer with regular SQL.
- **Truncated JSON repair**: LLM responses can be truncated (especially with large system prompts). `_repair_truncated_json()` escapes bare newlines in JSON strings and closes unclosed braces/brackets. If repair yields incomplete data, a retry with a "be concise" prompt fires automatically.

## LLM Configuration

- **Default deployment**: `gpt-5-mini` (set in `.env` as `NLOMOP_AZURE_OPENAI__DEPLOYMENT`)
- **Utility model**: `gpt-4.1-mini` for cheap tasks (label generation) via `llm.quick_chat()`
- **Available models**: gpt-4o-mini, gpt-4.1-mini, gpt-5-mini, gpt-5.2, model-router
- `llm.chat()` returns `(text, finish_reason)` tuple; `llm.quick_chat()` returns just text
- Models in `_USES_MAX_COMPLETION_TOKENS` set use `max_completion_tokens` instead of `max_tokens`

## Database Access

Read-only. Never write, update, or delete. Set a query timeout of 30-60 seconds.

```
psql -h localhost -p 5432 -U TatonettiN -d synthea10
SET search_path TO cdm_synthea;
```

## Env Vars

All prefixed `NLOMOP_`, with `__` separating nested config:
- `NLOMOP_AZURE_OPENAI__ENDPOINT` — Azure OpenAI endpoint URL
- `NLOMOP_AZURE_OPENAI__API_KEY` — API key
- `NLOMOP_AZURE_OPENAI__DEPLOYMENT` — Model deployment name (default: gpt-5-mini)
- `NLOMOP_AZURE_OPENAI__API_VERSION` — API version (default: 2024-12-01-preview)
- `NLOMOP_DB__HOST`, `NLOMOP_DB__PORT`, `NLOMOP_DB__NAME`, `NLOMOP_DB__USER`, `NLOMOP_DB__PASSWORD`, `NLOMOP_DB__SCHEMA`
