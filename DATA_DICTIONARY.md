# OMOP CDM Data Dictionary for AI Agent Development

This document describes the schema and contents of the `synthea10` PostgreSQL database — an OMOP Common Data Model v5.4 instance populated with 10,000 synthetic patients from Synthea. It is written for an AI developer building a system that translates natural language questions about patient health records into SQL queries.

## Connection

```
host:     localhost
port:     5432
database: synthea10
user:     TatonettiN
password: (empty)
schema:   cdm_synthea
```

All tables live in the `cdm_synthea` schema. Prefix table references accordingly (e.g., `cdm_synthea.person`) or set the search path:

```sql
SET search_path TO cdm_synthea;
```

---

## How OMOP CDM Works (The Key Idea)

Every clinical fact — a diagnosis, a lab result, a prescription, a procedure — is stored as a row in a domain-specific table, linked to a **standard concept** via an integer `concept_id`. The `concept` vocabulary table is the Rosetta Stone: it maps concept IDs to human-readable names, codes, and hierarchical relationships.

A natural language query like *"patients with diabetes on metformin"* becomes:

1. Look up "diabetes" in `concept` → `concept_id = 201826` (Type 2 diabetes mellitus)
2. Look up "metformin" in `concept` → `concept_id = 1503297` (metformin)
3. Join `condition_occurrence` and `drug_exposure` on `person_id` filtering by those concept IDs

The vocabulary tables also support hierarchical queries — e.g., finding all *types* of diabetes using `concept_ancestor`.

---

## Table Inventory and Row Counts

### Clinical Data Tables

| Table | Rows | Description |
|---|---|---|
| `person` | 11,463 | One row per patient. Demographics. |
| `death` | 1,373 | One row per deceased patient. |
| `observation_period` | 11,463 | One row per patient. Time span of available data. |
| `visit_occurrence` | 1,748,376 | Healthcare encounters (outpatient, ER, inpatient). |
| `visit_detail` | 1,748,376 | Granular visit details (1:1 with visit_occurrence in this dataset). |
| `condition_occurrence` | 463,572 | Diagnoses recorded during encounters. |
| `condition_era` | 460,378 | Aggregated continuous condition periods (derived). |
| `drug_exposure` | 1,663,443 | Medication prescriptions and administrations. |
| `drug_era` | 400,625 | Aggregated continuous drug exposure periods (derived). |
| `procedure_occurrence` | 3,084,877 | Procedures performed. |
| `measurement` | 14,880,118 | Lab results, vital signs, and other quantitative observations. |
| `observation` | 8,025,240 | Non-measurement observations (SDOH, screening instruments, etc.). |
| `device_exposure` | 142,014 | Medical devices used. |
| `cost` | 4,907,740 | Financial records linked to clinical events. |
| `payer_plan_period` | 422,329 | Insurance coverage periods. |
| `location` | 1,179 | Geographic locations (cities/addresses). |
| `care_site` | 1,179 | Healthcare facilities. |
| `provider` | 1,179 | Individual healthcare providers. |

### Vocabulary Tables

| Table | Rows (approx.) | Description |
|---|---|---|
| `concept` | ~6M | Master dictionary of all medical concepts. |
| `concept_relationship` | ~46M | Relationships between concepts (maps-to, is-a, etc.). |
| `concept_ancestor` | ~38M | Hierarchical ancestry for navigating concept hierarchies. |
| `concept_synonym` | ~3M | Alternate names for concepts. |
| `vocabulary` | ~60 | Vocabulary metadata (SNOMED, LOINC, RxNorm, etc.). |
| `domain` | ~50 | Domain definitions (Condition, Drug, Measurement, etc.). |
| `concept_class` | ~400 | Concept class definitions (Clinical Finding, Ingredient, etc.). |
| `relationship` | ~700 | Relationship type definitions. |
| `drug_strength` | ~3M | Ingredient strengths for drug concepts. |

---

## The `concept` Table — The Central Vocabulary

This is the single most important table for translating natural language to SQL. Every `*_concept_id` column in every clinical table is a foreign key to `concept.concept_id`.

### Schema

| Column | Type | Description |
|---|---|---|
| `concept_id` | integer | Primary key. The integer used in all clinical tables. |
| `concept_name` | varchar | Human-readable name (e.g., "Type 2 diabetes mellitus"). |
| `domain_id` | varchar | Which clinical table this concept belongs to: Condition, Drug, Procedure, Measurement, Observation, Device, etc. |
| `vocabulary_id` | varchar | Source vocabulary: SNOMED, LOINC, RxNorm, ICD10CM, NDC, etc. |
| `concept_class_id` | varchar | Granularity within the vocabulary: Clinical Finding, Ingredient, Clinical Drug, Lab Test, etc. |
| `standard_concept` | varchar | `'S'` = standard concept (use these for queries), `'C'` = classification, NULL = non-standard/source. |
| `concept_code` | varchar | The code in the source vocabulary (e.g., SNOMED code "73211009" for "Diabetes mellitus"). |
| `valid_start_date` | date | When this concept became valid. |
| `valid_end_date` | date | When this concept was retired (2099-12-31 if still active). |

### How to Search for Concepts

To find the concept_id for a clinical term, search `concept_name` with `ILIKE`:

```sql
-- Find concept IDs for "diabetes"
SELECT concept_id, concept_name, domain_id, vocabulary_id, concept_class_id
FROM cdm_synthea.concept
WHERE concept_name ILIKE '%diabetes%'
  AND standard_concept = 'S'
  AND domain_id = 'Condition'
ORDER BY concept_name;
```

Always filter on `standard_concept = 'S'` — non-standard concepts are source codes that may not appear in clinical tables.

### Standard Concept Counts by Domain

| Domain | Standard Concepts |
|---|---|
| Drug | 2,048,864 |
| Observation | 129,496 |
| Condition | 105,324 |
| Measurement | 93,912 |
| Procedure | 55,173 |
| Device | 27,019 |
| Meas Value | 25,146 |
| Unit | 1,039 |

### Primary Vocabularies

| Vocabulary | Standard Concepts | Used For |
|---|---|---|
| RxNorm / RxNorm Extension | ~2M | Drugs (ingredients, clinical drugs, branded drugs) |
| SNOMED | ~349K | Conditions, procedures, observations, measurements |
| LOINC | ~119K | Lab tests and measurements |
| NDC | ~12K | National Drug Codes |
| CVX | ~237 | Vaccines |

---

## The `concept_ancestor` Table — Hierarchical Queries

This table encodes the full ancestry tree of OMOP concepts. It enables queries like "find all patients with *any type* of diabetes" without enumerating every specific diabetes subtype.

### Schema

| Column | Type | Description |
|---|---|---|
| `ancestor_concept_id` | integer | The parent/ancestor concept. |
| `descendant_concept_id` | integer | The child/descendant concept. |
| `min_levels_of_separation` | integer | Shortest path length in the hierarchy. |
| `max_levels_of_separation` | integer | Longest path length in the hierarchy. |

### Usage Pattern

```sql
-- Find all patients with any type of diabetes
-- (concept_id 201826 = "Type 2 diabetes mellitus", but we want ALL diabetes)
SELECT DISTINCT co.person_id
FROM cdm_synthea.condition_occurrence co
JOIN cdm_synthea.concept_ancestor ca
  ON co.condition_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 4008576;  -- "Diabetes mellitus" (parent concept)
```

Every concept is its own ancestor at `min_levels_of_separation = 0`, so this pattern always includes exact matches too.

---

## The `concept_relationship` Table

Links concepts to each other via named relationships (e.g., "Maps to", "Is a", "Has ingredient").

### Schema

| Column | Type | Description |
|---|---|---|
| `concept_id_1` | integer | Source concept. |
| `concept_id_2` | integer | Target concept. |
| `relationship_id` | varchar | Type of relationship: "Maps to", "Is a", "Has ingredient", etc. |

### Common Uses

```sql
-- Find what standard concept a source code maps to
SELECT c2.concept_id, c2.concept_name
FROM cdm_synthea.concept_relationship cr
JOIN cdm_synthea.concept c2 ON cr.concept_id_2 = c2.concept_id
WHERE cr.concept_id_1 = <source_concept_id>
  AND cr.relationship_id = 'Maps to';

-- Find all ingredients of a drug
SELECT c2.concept_id, c2.concept_name
FROM cdm_synthea.concept_relationship cr
JOIN cdm_synthea.concept c2 ON cr.concept_id_2 = c2.concept_id
WHERE cr.concept_id_1 = <drug_concept_id>
  AND cr.relationship_id = 'Has ingredient';
```

---

## Clinical Table Schemas

### `person` — Patient Demographics

| Column | Type | Description |
|---|---|---|
| `person_id` | integer PK | Unique patient identifier. The universal join key. |
| `gender_concept_id` | integer | 8507 = Male, 8532 = Female. |
| `year_of_birth` | integer | Birth year. |
| `month_of_birth` | integer | Birth month (nullable). |
| `day_of_birth` | integer | Birth day (nullable). |
| `birth_datetime` | timestamp | Full birth timestamp. |
| `race_concept_id` | integer | 8527 = White, 8516 = Black or African American, 8515 = Asian, 0 = Unknown. |
| `ethnicity_concept_id` | integer | 38003563 = Hispanic or Latino, 38003564 = Not Hispanic or Latino. |
| `location_id` | integer | FK to `location`. |
| `person_source_value` | varchar | Synthea patient UUID. |
| `gender_source_value` | varchar | Original gender code ("M"/"F"). |
| `race_source_value` | varchar | Original race string ("white", "black", "asian"). |
| `ethnicity_source_value` | varchar | Original ethnicity string ("hispanic", "nonhispanic"). |

**Demographics breakdown**: ~50% Male / 50% Female. ~69% White, ~18% Black, ~10% Asian, ~3% Unknown. ~20% Hispanic.

**Important**: The Gender, Race, and Ethnicity vocabularies were not included in this Athena vocabulary download. The concept IDs above (8507, 8532, 8527, etc.) are the correct, well-known OMOP standard concept IDs and are used correctly in the `person` table, but they do not have corresponding rows in the local `concept` table. Do not JOIN `person.gender_concept_id` to `concept` — instead, use CASE expressions or a hardcoded mapping:

```sql
CASE gender_concept_id WHEN 8507 THEN 'Male' WHEN 8532 THEN 'Female' END AS gender
```

### `observation_period` — When Data is Available

| Column | Type | Description |
|---|---|---|
| `observation_period_id` | integer PK | |
| `person_id` | integer | FK to `person`. One row per person. |
| `observation_period_start_date` | date | Earliest available data for this patient. |
| `observation_period_end_date` | date | Latest available data for this patient. |

Date range spans from 1920 to 2025. Average observation period is ~27,000 days (~74 years) because Synthea generates full lifetime records.

### `visit_occurrence` — Healthcare Encounters

| Column | Type | Description |
|---|---|---|
| `visit_occurrence_id` | integer PK | Unique encounter identifier. |
| `person_id` | integer | FK to `person`. |
| `visit_concept_id` | integer | Type of visit (see below). |
| `visit_start_date` | date | Encounter start date. |
| `visit_end_date` | date | Encounter end date. |
| `visit_type_concept_id` | integer | Always 32827 ("EHR encounter record"). |
| `provider_id` | integer | FK to `provider`. |
| `care_site_id` | integer | FK to `care_site`. |
| `visit_source_value` | varchar | Synthea encounter UUID. |
| `preceding_visit_occurrence_id` | integer | FK to the previous visit for this patient. |

**Visit types in this dataset:**

| visit_concept_id | Name | Count | % |
|---|---|---|---|
| 9202 | Outpatient Visit | 1,647,345 | 94.2% |
| 9203 | Emergency Room Visit | 86,570 | 5.0% |
| 9201 | Inpatient Visit | 14,461 | 0.8% |

### `condition_occurrence` — Diagnoses

| Column | Type | Description |
|---|---|---|
| `condition_occurrence_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `condition_concept_id` | integer | FK to `concept` (domain_id = 'Condition'). **The main query column.** |
| `condition_start_date` | date | When the condition was first recorded. |
| `condition_end_date` | date | When the condition resolved (nullable). |
| `condition_type_concept_id` | integer | Always 32827 ("EHR encounter record"). |
| `visit_occurrence_id` | integer | FK to `visit_occurrence`. Links diagnosis to encounter. |
| `condition_source_value` | varchar | Original SNOMED code string. |
| `condition_source_concept_id` | integer | SNOMED concept_id for the source code. |

463,572 rows. Covers ~160 distinct conditions across Synthea's disease modules.

### `condition_era` — Aggregated Condition Periods

Derived table that merges overlapping `condition_occurrence` records into continuous eras.

| Column | Type | Description |
|---|---|---|
| `condition_era_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `condition_concept_id` | integer | FK to `concept`. |
| `condition_era_start_date` | date | Start of the continuous condition period. |
| `condition_era_end_date` | date | End of the continuous condition period. |
| `condition_occurrence_count` | integer | Number of occurrences merged into this era. |

Useful for questions about disease duration, chronic conditions, and temporal queries.

### `drug_exposure` — Medications

| Column | Type | Description |
|---|---|---|
| `drug_exposure_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `drug_concept_id` | integer | FK to `concept` (domain_id = 'Drug'). **The main query column.** |
| `drug_exposure_start_date` | date | Prescription/administration date. |
| `drug_exposure_end_date` | date | End of exposure. |
| `drug_type_concept_id` | integer | 32838 ("EHR prescription") or 32827 ("EHR encounter record"). |
| `quantity` | numeric | Amount prescribed. |
| `days_supply` | integer | Days the prescription covers. |
| `visit_occurrence_id` | integer | FK to `visit_occurrence`. |
| `drug_source_value` | varchar | Original RxNorm code. |

1,663,443 rows. Drug concepts in this table are at the **Clinical Drug** level (e.g., "amoxicillin 500 MG Oral Capsule"), not the ingredient level. To query by ingredient name (e.g., "amoxicillin"), use `concept_ancestor`:

```sql
-- Find all drug exposures for amoxicillin (any formulation)
SELECT de.*
FROM cdm_synthea.drug_exposure de
JOIN cdm_synthea.concept_ancestor ca
  ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 723;  -- amoxicillin (Ingredient)
```

### `drug_era` — Aggregated Drug Periods

| Column | Type | Description |
|---|---|---|
| `drug_era_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `drug_concept_id` | integer | FK to `concept`. At the **Ingredient** level. |
| `drug_era_start_date` | date | Start of continuous drug exposure. |
| `drug_era_end_date` | date | End of continuous drug exposure. |
| `drug_exposure_count` | integer | Number of exposures merged. |
| `gap_days` | integer | Total gap days tolerated in the merge. |

400,625 rows. Unlike `drug_exposure`, drug eras roll up to the **ingredient** level, making them better for questions like "how long was the patient on amoxicillin?"

### `procedure_occurrence` — Procedures

| Column | Type | Description |
|---|---|---|
| `procedure_occurrence_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `procedure_concept_id` | integer | FK to `concept` (domain_id = 'Procedure'). |
| `procedure_date` | date | When the procedure was performed. |
| `procedure_type_concept_id` | integer | Always 32827 ("EHR encounter record"). |
| `visit_occurrence_id` | integer | FK to `visit_occurrence`. |
| `procedure_source_value` | varchar | Original SNOMED code. |

3,084,877 rows.

### `measurement` — Labs and Vitals

The largest clinical table. Contains lab results, vital signs, and other quantitative clinical observations.

| Column | Type | Description |
|---|---|---|
| `measurement_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `measurement_concept_id` | integer | FK to `concept` (domain_id = 'Measurement'). Typically LOINC-based. |
| `measurement_date` | date | When the measurement was taken. |
| `measurement_type_concept_id` | integer | Always 32827 ("EHR encounter record"). |
| `value_as_number` | numeric | The numeric result (e.g., 6.8 for HbA1c). Nullable. |
| `value_as_concept_id` | integer | Coded result for categorical measurements (e.g., positive/negative). |
| `unit_concept_id` | integer | FK to `concept` for the unit (e.g., mg/dL, mmHg). |
| `range_low` | numeric | Normal range lower bound (often null in this dataset). |
| `range_high` | numeric | Normal range upper bound (often null in this dataset). |
| `visit_occurrence_id` | integer | FK to `visit_occurrence`. |
| `measurement_source_value` | varchar | Original LOINC code. |
| `unit_source_value` | varchar | Unit as a string (e.g., "mg/dL", "cm"). |
| `value_source_value` | varchar | Original value as a string. |

14,880,118 rows. Common measurements include body height, body weight, BMI, blood pressure (systolic/diastolic), glucose, cholesterol (total/HDL/LDL), HbA1c, creatinine, and standard blood panel components.

**Querying measurements with values:**

```sql
-- Average BMI across all patients
SELECT round(avg(value_as_number)::numeric, 1) AS avg_bmi
FROM cdm_synthea.measurement
WHERE measurement_concept_id = 3038553  -- Body mass index
  AND value_as_number IS NOT NULL;
```

### `observation` — Other Clinical Observations

Catch-all for clinical facts that don't fit Condition/Drug/Procedure/Measurement. In this Synthea dataset, dominated by social determinants of health (SDOH) and screening instruments.

| Column | Type | Description |
|---|---|---|
| `observation_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `observation_concept_id` | integer | FK to `concept` (domain_id = 'Observation'). |
| `observation_date` | date | When the observation was recorded. |
| `observation_type_concept_id` | integer | 38000280 ("Observation recorded from EHR") or 32827. |
| `value_as_number` | numeric | Numeric value (if applicable). |
| `value_as_string` | varchar | String value (if applicable). |
| `value_as_concept_id` | integer | Coded value (if applicable). |
| `visit_occurrence_id` | integer | FK to `visit_occurrence`. |

8,025,240 rows.

### `device_exposure` — Medical Devices

| Column | Type | Description |
|---|---|---|
| `device_exposure_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `device_concept_id` | integer | FK to `concept` (domain_id = 'Device'). |
| `device_exposure_start_date` | date | When the device was applied/implanted. |
| `device_exposure_end_date` | date | When the device was removed (nullable). |
| `visit_occurrence_id` | integer | FK to `visit_occurrence`. |

142,014 rows.

### `death` — Mortality

| Column | Type | Description |
|---|---|---|
| `person_id` | integer PK | FK to `person`. |
| `death_date` | date | Date of death. |
| `death_type_concept_id` | integer | Source of death info. |
| `cause_concept_id` | integer | FK to `concept` for cause of death. |
| `cause_source_value` | varchar | Original cause code. |

1,373 rows (12% mortality rate — reasonable for lifetime synthetic records).

### `cost` — Financial Data

| Column | Type | Description |
|---|---|---|
| `cost_id` | integer PK | |
| `cost_event_id` | integer | FK to the clinical event (procedure, drug, visit, etc.). |
| `cost_domain_id` | varchar | Which domain the cost is for: "Procedure", "Drug", "Visit", etc. |
| `total_charge` | numeric | Billed amount. |
| `total_cost` | numeric | Actual cost. |
| `total_paid` | numeric | Amount paid. |
| `paid_by_payer` | numeric | Insurance portion. |
| `paid_by_patient` | numeric | Patient out-of-pocket. |
| `payer_plan_period_id` | integer | FK to `payer_plan_period`. |

4,907,740 rows. Join on `cost_event_id` to the PK of the domain table indicated by `cost_domain_id`.

### `payer_plan_period` — Insurance Coverage

| Column | Type | Description |
|---|---|---|
| `payer_plan_period_id` | integer PK | |
| `person_id` | integer | FK to `person`. |
| `payer_plan_period_start_date` | date | Coverage start. |
| `payer_plan_period_end_date` | date | Coverage end. |
| `payer_source_value` | varchar | Payer name string (e.g., insurer name). |

422,329 rows.

---

## Common Query Patterns

### Pattern 1: Count patients with a condition

```sql
SELECT count(DISTINCT person_id)
FROM cdm_synthea.condition_occurrence
WHERE condition_concept_id = 201826;  -- Type 2 diabetes mellitus
```

### Pattern 2: Find a concept by name

```sql
SELECT concept_id, concept_name, domain_id, vocabulary_id
FROM cdm_synthea.concept
WHERE concept_name ILIKE '%hypertension%'
  AND standard_concept = 'S'
ORDER BY concept_name
LIMIT 20;
```

### Pattern 3: Hierarchical condition lookup (all subtypes)

```sql
SELECT DISTINCT co.person_id
FROM cdm_synthea.condition_occurrence co
JOIN cdm_synthea.concept_ancestor ca
  ON co.condition_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 4008576;  -- Diabetes mellitus (parent)
```

### Pattern 4: Drug exposure by ingredient name

```sql
SELECT count(DISTINCT de.person_id) AS patients_on_metformin
FROM cdm_synthea.drug_exposure de
JOIN cdm_synthea.concept_ancestor ca
  ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 1503297;  -- metformin (Ingredient concept)
```

### Pattern 5: Comorbidity (two conditions in same patient)

```sql
SELECT count(DISTINCT a.person_id)
FROM cdm_synthea.condition_occurrence a
JOIN cdm_synthea.condition_occurrence b
  ON a.person_id = b.person_id
WHERE a.condition_concept_id = 201826   -- Type 2 diabetes
  AND b.condition_concept_id = 316866;  -- Hypertensive disorder
```

### Pattern 6: Measurement with threshold

```sql
-- Patients with HbA1c > 6.5%
SELECT DISTINCT person_id
FROM cdm_synthea.measurement
WHERE measurement_concept_id = 3004410  -- Hemoglobin A1c
  AND value_as_number > 6.5;
```

### Pattern 7: Temporal — events within N days of each other

```sql
-- Drugs prescribed within 30 days after a diabetes diagnosis
SELECT DISTINCT de.drug_concept_id, c.concept_name, count(*) AS rx_count
FROM cdm_synthea.condition_occurrence co
JOIN cdm_synthea.drug_exposure de
  ON co.person_id = de.person_id
  AND de.drug_exposure_start_date BETWEEN co.condition_start_date
                                      AND co.condition_start_date + INTERVAL '30 days'
JOIN cdm_synthea.concept c ON de.drug_concept_id = c.concept_id
WHERE co.condition_concept_id = 201826  -- Type 2 diabetes
GROUP BY de.drug_concept_id, c.concept_name
ORDER BY rx_count DESC
LIMIT 10;
```

### Pattern 8: Demographics with clinical data

```sql
-- Age distribution of diabetic patients
SELECT
  CASE
    WHEN EXTRACT(YEAR FROM AGE(co.condition_start_date,
         make_date(p.year_of_birth, COALESCE(p.month_of_birth,1), COALESCE(p.day_of_birth,1))))
         < 40 THEN '<40'
    WHEN EXTRACT(YEAR FROM AGE(co.condition_start_date,
         make_date(p.year_of_birth, COALESCE(p.month_of_birth,1), COALESCE(p.day_of_birth,1))))
         < 60 THEN '40-59'
    ELSE '60+'
  END AS age_at_diagnosis,
  count(DISTINCT co.person_id) AS patients
FROM cdm_synthea.condition_occurrence co
JOIN cdm_synthea.person p ON co.person_id = p.person_id
WHERE co.condition_concept_id = 201826
GROUP BY age_at_diagnosis
ORDER BY age_at_diagnosis;
```

### Pattern 9: Visit-linked query (what happened during an encounter)

```sql
-- All diagnoses and procedures from ER visits for a patient
SELECT 'Condition' AS domain, c.concept_name, co.condition_start_date AS event_date
FROM cdm_synthea.condition_occurrence co
JOIN cdm_synthea.visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
JOIN cdm_synthea.concept c ON co.condition_concept_id = c.concept_id
WHERE vo.person_id = 1 AND vo.visit_concept_id = 9203
UNION ALL
SELECT 'Procedure', c.concept_name, po.procedure_date
FROM cdm_synthea.procedure_occurrence po
JOIN cdm_synthea.visit_occurrence vo ON po.visit_occurrence_id = vo.visit_occurrence_id
JOIN cdm_synthea.concept c ON po.procedure_concept_id = c.concept_id
WHERE vo.person_id = 1 AND vo.visit_concept_id = 9203
ORDER BY event_date;
```

### Pattern 10: Cost analysis

```sql
-- Average cost by visit type
SELECT
  CASE vo.visit_concept_id
    WHEN 9201 THEN 'Inpatient'
    WHEN 9202 THEN 'Outpatient'
    WHEN 9203 THEN 'Emergency Room'
  END AS visit_type,
  round(avg(c.total_cost)::numeric, 2) AS avg_cost,
  count(*) AS cost_records
FROM cdm_synthea.cost c
JOIN cdm_synthea.visit_occurrence vo
  ON c.cost_event_id = vo.visit_occurrence_id
WHERE c.cost_domain_id = 'Visit'
GROUP BY vo.visit_concept_id
ORDER BY avg_cost DESC;
```

---

## Important Concept IDs (Hardcoded Reference)

These are frequently needed and safe to hardcode:

### Visit Types
| concept_id | Name |
|---|---|
| 9201 | Inpatient Visit |
| 9202 | Outpatient Visit |
| 9203 | Emergency Room Visit |

### Gender
| concept_id | Name |
|---|---|
| 8507 | Male |
| 8532 | Female |

### Race
| concept_id | Name |
|---|---|
| 8527 | White |
| 8516 | Black or African American |
| 8515 | Asian |
| 0 | Unknown / No matching concept |

### Ethnicity
| concept_id | Name |
|---|---|
| 38003563 | Hispanic or Latino |
| 38003564 | Not Hispanic or Latino |

### Type Concepts (how the data was recorded)
| concept_id | Name | Used In |
|---|---|---|
| 32827 | EHR encounter record | All tables |
| 32838 | EHR prescription | drug_exposure |
| 38000280 | Observation recorded from EHR | observation |

### Common Condition Concepts (examples present in this data)
| concept_id | Name |
|---|---|
| 201826 | Type 2 diabetes mellitus |
| 316866 | Hypertensive disorder |
| 4008576 | Diabetes mellitus (parent — use with concept_ancestor) |
| 255848 | Pneumonia |
| 257007 | Disorder of lung |
| 80180 | Osteoarthritis |
| 78232 | Fracture of bone |

### Common Measurement Concepts (examples)
| concept_id | Name | Typical Unit |
|---|---|---|
| 3004410 | Hemoglobin A1c/Hemoglobin.total in Blood | % |
| 3038553 | Body mass index | kg/m2 |
| 3036277 | Body height | cm |
| 3025315 | Body weight | kg |
| 3004249 | Systolic blood pressure | mmHg |
| 3012888 | Diastolic blood pressure | mmHg |
| 3000963 | Glucose [Mass/volume] in Blood | mg/dL |
| 3027114 | Cholesterol in LDL | mg/dL |

---

## Data Characteristics and Limitations

1. **Synthetic data**: All patients are generated by Synthea's disease modules. Clinical patterns are realistic but algorithmic — they follow module logic, not real patient variability.

2. **~160 conditions**: Synthea covers common conditions (diabetes, hypertension, COPD, asthma, cancer types, infections, etc.) but is not exhaustive. Rare diseases are generally absent.

3. **No free text**: There are no clinical notes, radiology reports, or unstructured text. All data is structured and coded.

4. **Full lifetime records**: Unlike real claims data, these patients have records from birth to present (or death). Observation periods average ~74 years.

5. **Missing demographic vocabularies**: The Gender, Race, and Ethnicity concept IDs (8507, 8532, 8527, etc.) are used in the `person` table but do not have entries in the local `concept` table. Use CASE expressions instead of JOINs for demographic labels.

6. **No secondary indexes on large tables**: The `measurement` table (14.9M rows) and `observation` table (8M rows) only have primary key indexes. Queries filtering on `person_id` or `measurement_concept_id` will do sequential scans. Consider adding indexes if query performance is critical:
   ```sql
   CREATE INDEX idx_measurement_person ON cdm_synthea.measurement(person_id);
   CREATE INDEX idx_measurement_concept ON cdm_synthea.measurement(measurement_concept_id);
   CREATE INDEX idx_condition_concept ON cdm_synthea.condition_occurrence(condition_concept_id);
   CREATE INDEX idx_drug_exposure_concept ON cdm_synthea.drug_exposure(drug_concept_id);
   ```

7. **concept_ancestor and concept_relationship are large**: 38M and 46M rows respectively. JOINs to these tables can be slow without proper filtering. Always constrain with specific `ancestor_concept_id` or `relationship_id` values.

---

## Example Natural Language Questions This Database Can Answer

**Condition prevalence**: "What percentage of patients have been diagnosed with hypertension?" / "What are the top 10 most common conditions?"

**Drug utilization**: "What is the most prescribed drug for patients with diabetes?" / "How many patients are on 5+ concurrent medications?"

**Lab analysis**: "What is the average HbA1c for diabetic patients?" / "Find patients with LDL cholesterol above 190 mg/dL."

**Temporal/longitudinal**: "What is the average time from diabetes diagnosis to first metformin prescription?" / "What is the 30-day ER readmission rate?"

**Comorbidity**: "What conditions commonly co-occur with heart failure?" / "What is the mortality rate for patients with 3+ chronic conditions?"

**Demographics**: "Is hypertension more prevalent in patients over 60 vs under 60?" / "What is the gender distribution of patients on statins?"

**Cost**: "What is the average cost of an ER visit vs an outpatient visit?" / "What are the most expensive procedures?"

**Cohort building**: "Find patients with diabetes, at least one HbA1c > 7%, and a current metformin prescription."
