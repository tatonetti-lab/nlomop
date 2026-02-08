# Product Spec: Natural Language EHR Query Agent

## What This Is

A conversational tool that lets a user ask questions about electronic health records in plain English and get back answers — tables, counts, charts — without writing SQL. The user types something like "What percentage of diabetic patients are on metformin?" and the system figures out the right OMOP CDM concepts, writes the SQL, runs it, and presents the result.

## Who It's For

Biomedical researchers, clinicians, and data analysts who understand clinical questions but don't know (or don't want to write) SQL against the OMOP Common Data Model. The tool should feel like talking to a knowledgeable database analyst who happens to have instant access to the data.

## The Database

The backend is a PostgreSQL database (`synthea10`) containing synthetic patient records in OMOP CDM v5.4 format. See `DATA_DICTIONARY.md` for the complete schema reference, query patterns, and concept ID mappings. Key facts:

- **11,463 patients** with full lifetime records (birth through 2025)
- **14.9M measurements**, 8M observations, 4.9M cost records, 3.1M procedures, 1.7M drug exposures, 463K conditions
- **~6M vocabulary concepts** mapping human terms to standard codes via the `concept` table
- **Hierarchical concept relationships** via `concept_ancestor` (38M rows) enabling queries like "all types of diabetes"
- PostgreSQL on localhost:5432, database `synthea10`, schema `cdm_synthea`, user `TatonettiN`, no password

## Core Workflow

```
User question (natural language)
        │
        ▼
┌─────────────────┐
│  Concept Resolution  │  ← Map clinical terms to OMOP concept_ids
│                       │    using the concept table + concept_ancestor
└───────┬─────────┘
        │
        ▼
┌─────────────────┐
│  SQL Generation  │  ← Build a PostgreSQL query against cdm_synthea
└───────┬─────────┘
        │
        ▼
┌─────────────────┐
│  Query Execution │  ← Run against synthea10, capture results
└───────┬─────────┘
        │
        ▼
┌─────────────────┐
│  Answer Formatting  │  ← Present as text, table, or chart
└─────────────────┘
```

## Functional Requirements

### Must Have

1. **Accept natural language questions** about patients, conditions, drugs, procedures, measurements, demographics, visits, and costs.

2. **Resolve clinical terms to OMOP concepts** automatically. When the user says "diabetes", the system should search the `concept` table, identify the right standard concept(s), and decide whether to use `concept_ancestor` for hierarchical inclusion. This is the hardest and most important part — if concept resolution is wrong, the SQL will return wrong answers.

3. **Generate correct SQL** against the OMOP CDM schema. The system must understand the table relationships documented in `DATA_DICTIONARY.md`:
   - Clinical facts live in domain-specific tables joined by `person_id`
   - Every clinical table uses `*_concept_id` columns as foreign keys to `concept`
   - Drug concepts have a hierarchy: Ingredient → Clinical Drug → Branded Drug (use `concept_ancestor` to traverse)
   - The `visit_occurrence_id` column links clinical events to encounters
   - Demographic labels (gender, race) require CASE expressions, not JOINs (see DATA_DICTIONARY.md "Important" note under `person`)

4. **Execute the SQL** against the database and return results.

5. **Present results clearly**: simple counts as sentences, multi-row results as formatted tables, distributions as tables with percentages. The output should directly answer the question asked.

6. **Show the SQL** that was generated, so the user can verify, learn, or modify it.

### Should Have

7. **Clarify ambiguous questions** before querying. If the user asks about "blood pressure", ask whether they mean systolic, diastolic, or both. If "diabetes" could mean Type 1 or Type 2, either ask or default to the broader parent concept with `concept_ancestor`.

8. **Handle follow-up questions** that reference prior context: "Now break that down by gender" or "What about for patients over 65?"

9. **Suggest related questions** after answering, to guide exploration: "You might also want to ask: What medications are these patients on?"

### Nice to Have

10. **Simple visualizations** — bar charts for distributions, trends over time. Can be ASCII/text-based or HTML.

11. **Cohort building mode** — let the user iteratively refine a patient population ("start with diabetics... now filter to those with HbA1c > 7%... now show me their medications").

12. **Query explanation** — briefly explain *why* the SQL was written the way it was, especially when using `concept_ancestor` or temporal joins.

## Concept Resolution Strategy

This is the critical intelligence layer. Suggested approach:

1. **Extract clinical terms** from the user's question (e.g., "diabetes", "metformin", "blood pressure").

2. **Search the concept table** using `ILIKE` on `concept_name`, filtered to `standard_concept = 'S'` and the appropriate `domain_id`.

3. **Disambiguate** when multiple matches exist:
   - If one concept is clearly more general (higher in hierarchy), prefer it with `concept_ancestor` for inclusive queries.
   - If the user's intent is specific (e.g., "Type 2 diabetes" not just "diabetes"), use the exact concept.
   - When genuinely ambiguous, ask the user.

4. **Cache/remember common mappings** across the session to avoid repeated lookups.

5. **Handle drug hierarchy**: Users say ingredient names ("metformin") but `drug_exposure` stores Clinical Drug concepts ("metformin 500 MG Oral Tablet"). Always use `concept_ancestor` to bridge from ingredient to all formulations. The `drug_era` table is already at ingredient level and may be more appropriate for some queries.

## Technical Constraints

- **Read-only access** to the database. Never write, update, or delete.
- **Query timeout**: Set a reasonable timeout (30-60 seconds). Some queries on the large tables (measurement: 14.9M rows) can be slow without indexes. See DATA_DICTIONARY.md for suggested indexes.
- **No secondary indexes** on large tables by default. The system should be aware that full table scans on `measurement` or `observation` will be slow and prefer indexed access paths where possible (e.g., filtering on a specific `person_id` or using `concept_ancestor` JOINs that constrain the result set early).
- **Large vocabulary tables**: `concept_relationship` (46M rows) and `concept_ancestor` (38M rows). Always filter these tightly — never scan them without a WHERE clause on `ancestor_concept_id`, `descendant_concept_id`, or `relationship_id`.

## Example Interactions

**Simple count:**
> User: How many patients have diabetes?
> Agent: Searching for "diabetes" in the concept table... Found concept 4008576 "Diabetes mellitus" (parent concept). Using concept_ancestor to include all subtypes.
> ```sql
> SELECT count(DISTINCT co.person_id)
> FROM cdm_synthea.condition_occurrence co
> JOIN cdm_synthea.concept_ancestor ca ON co.condition_concept_id = ca.descendant_concept_id
> WHERE ca.ancestor_concept_id = 4008576;
> ```
> **Result: 1,247 patients** (10.9% of the 11,463 patient population) have a diabetes diagnosis.

**Cross-domain:**
> User: What drugs are most commonly prescribed to patients with hypertension?
> Agent: [resolves hypertension → concept_id, joins condition_occurrence to drug_exposure on person_id, groups by drug concept, returns top 10 with counts]

**Measurement with threshold:**
> User: How many patients have had a BMI over 30?
> Agent: [resolves BMI → measurement_concept_id 3038553, filters value_as_number > 30, counts distinct person_id]

**Temporal:**
> User: What's the average time between a diabetes diagnosis and the first metformin prescription?
> Agent: [joins condition_occurrence to drug_exposure on person_id with date ordering, computes interval]

**Follow-up:**
> User: Break that down by gender.
> Agent: [takes the previous query, adds JOIN to person, groups by gender_concept_id with CASE expression]

## Technology Guidance

This spec is deliberately technology-agnostic for the application layer. Use whatever stack makes sense — Python, TypeScript, a CLI tool, a web app, or even a Claude Code slash command. The only hard dependency is PostgreSQL access via `psycopg2`, `pg` (node-postgres), JDBC, or `psql`.

The LLM layer (for NL understanding and SQL generation) can be Claude via the Anthropic API, called directly or via a framework. The `DATA_DICTIONARY.md` file is designed to be usable as system prompt context — it contains everything the LLM needs to write correct OMOP CDM queries.

## Files to Include in the New Project

| File | Purpose |
|---|---|
| `PRODUCT_SPEC.md` | This file. Product requirements and design. |
| `DATA_DICTIONARY.md` | Complete database schema reference, query patterns, concept ID mappings. |
