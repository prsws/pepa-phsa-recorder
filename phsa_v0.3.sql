-- PHSA recorder store — DRAFT v0.3 (proposal, not applied anywhere)
-- Scope: what the SUBJ-01 bundle carried — Patient, Condition, MedicationRequest.
-- Target: PostgreSQL 18 (uuidv7() is built in). Relational use; no pgvector needed.
--
-- Changes in v0.3:
--   6. The Home Assistant identity link is OPTIONAL. The v0.2 commit-time requirement is gone:
--      no blockers ahead of knowing where the link will lead.
--
-- Changes in v0.2:
--   5. subject_ha_identity: an optional link from a subject to a stable Home Assistant identity.
--      Declared by the provisioner, per HA instance, superseded rather than deleted.
--      It says WHICH HA identity belongs to the subject. It grants no authority and does not
--      decide whose record a spoken question refers to; those decisions come later.
--
-- Changes from v0 (made in v0.1, after outside review):
--   1. subject split into a stable identity anchor + per-source demographic claims.
--   2. GIN index on resource_raw removed; add it back only when a query needs it.
--   3. medication_request gains authored_on (the date the prescription was written).
--   4. Confirmation moved to its own append-only table; source_record is now fully immutable.
--
-- Principles carried over from Pepa:
--   * The archive is authoritative; typed tables are a rebuildable projection of source_record.
--   * Append-only: no deleted_at, no UPDATE of raw data, supersession instead of deletion.
--   * Epistemic class on every record (observation / interpretation / prediction).
--   * Values extracted from a scan or proposed by a model are interpretations until a human confirms.
--   * Nothing here diagnoses, triages or recommends. It records what documents say.
--
-- Known limit: confirmed_by is free text. The design stops accidental promotion of an
-- unconfirmed value; it does not stop a writer from typing someone else's name.
--
-- Binary attachments (e.g. base64 in DocumentReference) must be moved to PaperlessMD before
-- ingest, leaving a hash and a pointer in the raw JSON. The raw layer cannot be scrubbed later.

BEGIN;

CREATE SCHEMA IF NOT EXISTS phsa;
SET LOCAL search_path = phsa, public;

-- ---------------------------------------------------------------------------
-- Enumerations
-- ---------------------------------------------------------------------------
CREATE TYPE epistemic_class AS ENUM ('observation', 'interpretation', 'prediction');

CREATE TYPE extraction_method AS ENUM (
  'fhir_native',      -- structured export from a portal or bundle, no extraction step
  'manual_entry',     -- typed in by a human
  'ocr_extracted',    -- parsed from an OCR'd document
  'llm_assisted'      -- proposed by a model, awaiting human approval
);

-- ---------------------------------------------------------------------------
-- source_record: verbatim archive layer. One row per imported FHIR resource.
-- Fully immutable. The origin key makes re-import a no-op.
-- ---------------------------------------------------------------------------
CREATE TABLE source_record (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  source_system        text        NOT NULL,   -- 'fasten-manual-upload', 'paperlessmd', ...
  source_resource_type text        NOT NULL,   -- 'Patient' | 'Condition' | 'MedicationRequest' ...
  source_resource_id   text        NOT NULL,   -- id inside the source (bundle id, paperless doc id)
  resource_raw         jsonb       NOT NULL,   -- original FHIR JSON, never edited
  content_sha256       bytea       NOT NULL,   -- hash of the canonical raw JSON
  paperless_doc_id     integer,                -- hard link to the authoritative document
  extraction_method    extraction_method NOT NULL,
  epistemic_class      epistemic_class   NOT NULL DEFAULT 'observation',  -- class AT INGEST
  is_test_data         boolean     NOT NULL DEFAULT false,  -- true when meta.security has HTEST
  ingested_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_system, source_resource_type, source_resource_id),
  -- Anything extracted or model-proposed enters as an interpretation. Always.
  CONSTRAINT extracted_enters_as_interpretation CHECK (
    extraction_method IN ('fhir_native', 'manual_entry')
    OR epistemic_class = 'interpretation'
  )
);

CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on % is refused: table is append-only', TG_OP, TG_TABLE_NAME;
END $$;

CREATE TRIGGER source_record_immutable
  BEFORE UPDATE OR DELETE ON source_record
  FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- ---------------------------------------------------------------------------
-- record_confirmation: a human approved an extracted value. Append-only ledger.
-- A mistaken confirmation is answered with a new row (action = 'withdrawn'), not an edit.
-- ---------------------------------------------------------------------------
CREATE TABLE record_confirmation (
  id               uuid PRIMARY KEY DEFAULT uuidv7(),
  source_record_id uuid NOT NULL REFERENCES source_record(id),
  action           text NOT NULL CHECK (action IN ('confirmed', 'withdrawn')),
  confirmed_by     text NOT NULL,
  confirmed_at     timestamptz NOT NULL DEFAULT now(),
  note             text
);
CREATE INDEX record_confirmation_record_idx ON record_confirmation (source_record_id, confirmed_at DESC);

CREATE TRIGGER record_confirmation_immutable
  BEFORE UPDATE OR DELETE ON record_confirmation
  FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- Effective class: an extracted record becomes an observation only while its latest
-- confirmation row says 'confirmed'. Everything else keeps its ingest class.
CREATE VIEW source_record_effective AS
SELECT s.id AS source_record_id,
       s.source_resource_type,
       s.extraction_method,
       s.is_test_data,
       CASE
         WHEN s.extraction_method IN ('fhir_native', 'manual_entry') THEN s.epistemic_class
         WHEN (SELECT c.action FROM record_confirmation c
                WHERE c.source_record_id = s.id
                ORDER BY c.confirmed_at DESC, c.id DESC LIMIT 1) = 'confirmed'
           THEN 'observation'::epistemic_class
         ELSE s.epistemic_class
       END AS effective_class
FROM source_record s;

-- ---------------------------------------------------------------------------
-- subject: the identity anchor. One row per person, however many documents mention them.
-- Deliberately thin. Not an account, not a caregiver grant (those stay separate concepts).
-- ---------------------------------------------------------------------------
CREATE TABLE subject (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  label         text NOT NULL,                -- human-readable handle, not a clinical claim
  fixture_id    text UNIQUE,                  -- 'SUBJ-01' for synthetic fixtures, NULL for real people
  is_test_data  boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- subject_demographics: what each source says about the person. One row per source record;
-- later documents add rows, corrections supersede.
CREATE TABLE subject_demographics (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  subject_id         uuid NOT NULL REFERENCES subject(id),
  source_record_id   uuid NOT NULL UNIQUE REFERENCES source_record(id),
  family_name        text,                    -- two-surname form kept whole (INV-08)
  given_name         text,
  usual_name         text,                    -- e.g. 'Don Efra'
  sex                text CHECK (sex IN ('male', 'female', 'other', 'unknown')),
  birth_year         smallint,
  birth_date         date,
  home_municipality  text,
  preferred_language text,                    -- BCP-47, e.g. 'es'
  active             boolean NOT NULL DEFAULT true,
  superseded_by      uuid REFERENCES subject_demographics(id),
  CONSTRAINT birth_year_matches CHECK (
    birth_date IS NULL OR birth_year IS NULL OR birth_year = EXTRACT(YEAR FROM birth_date)
  )
);
CREATE INDEX subject_demographics_subject_idx ON subject_demographics (subject_id) WHERE superseded_by IS NULL;

-- ---------------------------------------------------------------------------
-- subject_ha_identity: an optional link to the stable Home Assistant identity of a subject.
-- Declared by whoever provisions it (never obtained from a device). Identifiers are only
-- meaningful inside one HA instance, so the instance is part of the key. Prefer the stable
-- internal id over a renamable entity_id string; keep the entity_id as a label.
-- ---------------------------------------------------------------------------
CREATE TABLE subject_ha_identity (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  subject_id         uuid NOT NULL REFERENCES subject(id),
  ha_instance        text NOT NULL,           -- 'prod', 'dev', or 'fixture' for synthetic subjects
  ha_identifier_kind text NOT NULL CHECK (ha_identifier_kind IN ('person_id', 'user_id')),
  ha_identifier      text NOT NULL,           -- the stable id inside that instance
  ha_entity_id       text,                    -- label only, e.g. 'person.efrain'; may be renamed
  declared_by        text NOT NULL,
  declared_at        timestamptz NOT NULL DEFAULT now(),
  note               text,
  superseded_by      uuid REFERENCES subject_ha_identity(id)
);
-- One HA identity belongs to at most one subject at a time.
CREATE UNIQUE INDEX subject_ha_identity_current_uq
  ON subject_ha_identity (ha_instance, ha_identifier_kind, ha_identifier)
  WHERE superseded_by IS NULL;
CREATE INDEX subject_ha_identity_subject_idx
  ON subject_ha_identity (subject_id) WHERE superseded_by IS NULL;

-- ---------------------------------------------------------------------------
-- condition: a problem-list entry as stated by a source. A record, not a verdict.
-- ---------------------------------------------------------------------------
CREATE TABLE condition (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  subject_id           uuid NOT NULL REFERENCES subject(id),
  source_record_id     uuid NOT NULL UNIQUE REFERENCES source_record(id),
  code_text            text NOT NULL,         -- wording as the source wrote it
  code_system          text,                  -- NULL until a source supplies a coded value
  code                 text,
  clinical_status      text NOT NULL CHECK (clinical_status IN
                         ('active', 'recurrence', 'relapse', 'inactive', 'remission', 'resolved')),
  verification_status  text CHECK (verification_status IN
                         ('unconfirmed', 'provisional', 'differential', 'confirmed', 'refuted', 'entered-in-error')),
  onset_text           text,                  -- e.g. 'approximately 14 years'
  onset_date           date,
  note                 text,
  superseded_by        uuid REFERENCES condition(id),
  CONSTRAINT code_pair CHECK ((code IS NULL) = (code_system IS NULL))
);
CREATE INDEX condition_subject_idx ON condition (subject_id) WHERE superseded_by IS NULL;

-- ---------------------------------------------------------------------------
-- medication_request: what was prescribed (FHIR MedicationRequest).
-- Not the same as "being taken" or "picked up"; those get their own tables later.
-- ---------------------------------------------------------------------------
CREATE TABLE medication_request (
  id                     uuid PRIMARY KEY DEFAULT uuidv7(),
  subject_id             uuid NOT NULL REFERENCES subject(id),
  source_record_id       uuid NOT NULL UNIQUE REFERENCES source_record(id),
  medication_text        text NOT NULL,       -- e.g. 'Metformin 500 mg'
  medication_code_system text,
  medication_code        text,                -- RxNorm etc. when a source supplies it
  status                 text NOT NULL CHECK (status IN
                           ('active', 'on-hold', 'ended', 'stopped', 'completed',
                            'cancelled', 'entered-in-error', 'draft', 'unknown')),
  intent                 text NOT NULL DEFAULT 'order',
  authored_on            date,                -- when the prescription was written; NULL if unknown
  sig_text               text,                -- the instruction as written
  dose_value             numeric,
  dose_unit              text,
  dose_unit_code         text,                -- UCUM, e.g. 'mg', '[IU]'
  route_text             text,
  frequency              smallint,
  period                 numeric,             -- FHIR periods can be fractional
  period_unit            text CHECK (period_unit IN ('s','min','h','d','wk','mo','a')),
  timing_when            text[],              -- FHIR event timing codes: MORN, HS, ...
  note                   text,
  safety_critical        boolean NOT NULL DEFAULT true,   -- exempt from decay and modulation
  superseded_by          uuid REFERENCES medication_request(id),
  CONSTRAINT med_code_pair CHECK ((medication_code IS NULL) = (medication_code_system IS NULL)),
  CONSTRAINT dose_pair     CHECK ((dose_value IS NULL) = (dose_unit IS NULL))
);
CREATE INDEX medication_request_subject_idx ON medication_request (subject_id) WHERE superseded_by IS NULL;

-- ---------------------------------------------------------------------------
-- Views: current (non-superseded) rows, with the effective epistemic class attached
-- ---------------------------------------------------------------------------
CREATE VIEW current_subject_demographics AS
  SELECT d.*, e.effective_class
  FROM subject_demographics d
  JOIN source_record_effective e ON e.source_record_id = d.source_record_id
  WHERE d.superseded_by IS NULL;

CREATE VIEW current_condition AS
  SELECT c.*, e.effective_class
  FROM condition c
  JOIN source_record_effective e ON e.source_record_id = c.source_record_id
  WHERE c.superseded_by IS NULL;

CREATE VIEW current_medication_request AS
  SELECT m.*, e.effective_class
  FROM medication_request m
  JOIN source_record_effective e ON e.source_record_id = m.source_record_id
  WHERE m.superseded_by IS NULL;

COMMIT;
