# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**nlomop** (Natural Language OMOP) is a conversational tool that translates plain-English questions about electronic health records into SQL queries against an OMOP CDM v5.4 PostgreSQL database.

The system maps clinical terms to OMOP concept IDs, generates SQL via Azure OpenAI (gpt-4o-mini), executes it against the database, and presents results in a chat UI. Stack: Python/FastAPI backend, vanilla HTML/CSS/JS frontend.

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
- `app/config.py` — Pydantic Settings loaded from `.env` with `NLOMOP_` prefix
- `app/db.py` — psycopg v3 async pool, query execution (read-only, 30s timeout)
- `app/llm.py` — AsyncAzureOpenAI wrapper
- `app/concept_cache.py` — Loads all ~1,571 distinct concepts from clinical tables at startup
- `app/prompts.py` — Builds system prompt from instructions + DATA_DICTIONARY.md + concept catalog
- `app/agent.py` — Orchestrator: question → LLM → parse JSON → validate SQL → execute → response
- `app/models.py` — Pydantic request/response schemas
- `static/` — Chat UI (index.html, style.css, app.js)

## Database Access

Read-only. Never write, update, or delete. Set a query timeout of 30-60 seconds.

```
psql -h localhost -p 5432 -U TatonettiN -d synthea10
SET search_path TO cdm_synthea;
```
