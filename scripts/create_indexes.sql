-- Optional performance indexes for nlomop queries
-- Run with: psql -h localhost -p 5432 -U TatonettiN -d synthea10 -f scripts/create_indexes.sql

SET search_path TO cdm_synthea;

CREATE INDEX IF NOT EXISTS idx_measurement_concept ON measurement(measurement_concept_id);
CREATE INDEX IF NOT EXISTS idx_measurement_person ON measurement(person_id);
CREATE INDEX IF NOT EXISTS idx_condition_concept ON condition_occurrence(condition_concept_id);
CREATE INDEX IF NOT EXISTS idx_drug_exposure_concept ON drug_exposure(drug_concept_id);
CREATE INDEX IF NOT EXISTS idx_observation_concept ON observation(observation_concept_id);
CREATE INDEX IF NOT EXISTS idx_procedure_concept ON procedure_occurrence(procedure_concept_id);
