# PHSA Recorder: CRS loader app (create / read / supersede), spec v0.4

Changes in v0.4: the five seed-data assumptions in section 12 were confirmed by the owner on 4 Oct 2026, as written; the spec is no longer a draft. No data-model change and no change to the seed values.
Changes in v0.3: no `pg_hba.conf` change is required (section 3); the seed script's `--commit` step now asks for a backup confirmation (section 8). No data-model change.
Changes in v0.2: the app's verbs are now Create / Read / Supersede (CRS); "edit" and "delete" wording is removed everywhere (routes, labels, tests). No data-model change.

Audience: a coding agent (Hermes / OpenCode on a local Qwen model) that has NOT seen the conversation this came from.
Everything it needs is in this file plus `phsa_v0.3.sql` (the database schema, which is the source of truth).

---

## 0. Rules for the builder (read first, obey always)

1. **Do not change the database schema.** Never run DDL. If the schema looks wrong or blocks you, write the problem in `QUESTIONS.md` and carry on with something else.
2. **Blank means blank.** Never invent, infer, "normalize", or fill in a value that is not written in this spec or typed by the user. Missing data stays `NULL`.
3. **Append-only: the app's verbs are Create, Read, Supersede (CRS). There is no Update and no Delete.** Never issue `DELETE` or `TRUNCATE`, and never `UPDATE` anything except the single `superseded_by` pointer column. To change a record, *supersede* it: insert a new version, then point the old one at it. Do not use the words "edit", "update" or "delete" in routes, function names, button labels or messages; use "supersede" (or "new version"). Marking something wrong is also a supersede (4.3), not a fourth verb.
4. **Test data only.** Every row the app writes has `is_test_data = true`. There is no checkbox, field, query parameter or config switch that changes this. It is a constant in code.
5. **No secrets in the repo.** The database password lives only in a `.env` file (git-ignored, `chmod 600`) or the process environment. Never print it, log it, or put it in a test.
6. **Use only the dependencies listed in section 2.** No ORM, no JavaScript build step, no npm at runtime, no CDN at runtime (vendored files only).
7. **Do not look up, search for, or "verify" anything about the fixture person.** The fixture rule is: never search to confirm a fixture entity is fictional. Use only the values in section 8.
8. **Work milestone by milestone** (section 10). Finish one, run its tests, commit, then stop and report. Do not start the next milestone in the same pass unless told to.
9. If this spec contradicts itself or the schema, the schema wins. Record the conflict in `QUESTIONS.md`.

---

## 1. What this is, and what it is not

A small web app, used by one person on the home LAN, to enter and correct a subject's record in the PHSA recorder database. Its first job is to load one synthetic person ("Don Efra", fixture SUBJ-01) by hand-equivalent entry so the schema gets exercised through real code.

It records what a document says. It never diagnoses, triages, recommends, scores risk, or interprets. No screen, label or message may suggest clinical meaning. Do not add "alerts", "warnings", "risk" badges, drug-interaction hints, or similar.

Out of scope for v1: login/accounts, the Home Assistant identity link (`subject_ha_identity`), the confirmation ledger (`record_confirmation`), file/document upload, import of FHIR bundles, any real person's data, deployment (systemd, reverse proxy, TLS).

---

## 2. Environment and stack

| Item | Value |
|---|---|
| App host | the OpenCode LXC on pve3, `192.168.10.42` (Debian 13) |
| Database host | pg18 VM, `192.168.10.18`, port 5432, PostgreSQL 18 |
| Databases | `phsa` (real) and `phsa_test` (tests only), identical schema |
| DB login | role `phsa_app` (limited grants, section 3) |
| Language | Python 3 (use the interpreter the LXC already has; record `python --version` in the README) |
| Web | Flask 3 + Jinja2 (autoescape ON) |
| DB driver | `psycopg[binary,pool]` (psycopg 3), plain parameterised SQL, no ORM |
| Server | `gunicorn` for running; Flask's dev server for development |
| Tests | `pytest` |
| Frontend | htmx **4.0.0** and Pico CSS **2.1.1**, both vendored under `static/vendor/` |

Pin exact versions in `requirements.txt` at install time (`pip freeze` of only the packages above and their dependencies).

Configuration (environment variables, read once at startup, `.env` supported via a 10-line loader or `python-dotenv`; if you use `python-dotenv`, add it to the list and say so in the README):

| Variable | Meaning | Default |
|---|---|---|
| `PHSA_DSN` | libpq DSN for the real database | none; app refuses to start without it |
| `PHSA_TEST_DSN` | DSN for `phsa_test` | none; tests skip with a clear message if unset |
| `PHSA_HOST` | bind address | `127.0.0.1` (José sets the LAN address when running) |
| `PHSA_PORT` | bind port | `8470` |

DSNs contain the password; they come from the environment only. `.env.example` ships with placeholders and no real values.

**Vendored frontend files.** Put these under `static/vendor/` and record file name, version, source URL and `sha256` of each in `static/vendor/VENDORED.md`:
- `htmx.min.js` from `https://unpkg.com/htmx.org@4.0.0/dist/htmx.min.js`
- `pico.min.css` from `https://cdn.jsdelivr.net/npm/@picocss/pico@2.1.1/css/pico.min.css`

The page references them as `/static/vendor/...`. Nothing is loaded from the internet when a page is viewed. Do not use the classless Pico variant or any colour-theme variant.

---

## 3. Database access (done by José, not by the builder)

The builder does **not** run any of this. It is here so the app's assumptions are explicit. José creates the role and the test database; the builder receives two DSNs.

```sql
-- as postgres, connected to database phsa
CREATE ROLE phsa_app LOGIN;
\password phsa_app

GRANT CONNECT ON DATABASE phsa TO phsa_app;
GRANT USAGE   ON SCHEMA phsa   TO phsa_app;
GRANT SELECT, INSERT ON
  phsa.source_record, phsa.subject, phsa.subject_demographics,
  phsa.condition, phsa.medication_request TO phsa_app;
GRANT UPDATE (superseded_by) ON
  phsa.subject_demographics, phsa.condition, phsa.medication_request TO phsa_app;
GRANT SELECT ON phsa.source_record_effective, phsa.current_subject_demographics,
  phsa.current_condition, phsa.current_medication_request TO phsa_app;
ALTER ROLE phsa_app IN DATABASE phsa SET search_path = phsa, public;

-- test database: create it, apply phsa_v0.3.sql to it, then repeat the same grants there
-- (replace "phsa" in GRANT CONNECT ... and ALTER ROLE ... IN DATABASE with phsa_test)
```

**No `pg_hba.conf` change is needed.** The existing catch-all rule on pg18 (`host all all 0.0.0.0/0 md5`) already admits `phsa_app` from the LXC, and `listen_addresses` is already `*`. The owner has decided to leave network access as it is; a full security review is planned for later. The builder must not change, propose changes to, or work around `pg_hba.conf`; if the connection fails, write the exact error in `QUESTIONS.md` and stop.

Consequences the app must live with:
- The role cannot `DELETE` or `TRUNCATE`, and can `UPDATE` only `superseded_by`. This is intentional. Tests must prove it (M0).
- Because the role cannot delete, **tests must never commit**. See section 9 (transaction-rollback pattern).
- Schema-qualify every table and view in SQL (`phsa.condition`), even though the role's `search_path` is set. Belt and braces.

---

## 4. Data model facts the app depends on

The schema file is authoritative; this is a reading guide.

- `phsa.source_record`: immutable archive. One row per saved version of any record. Columns the app writes: `source_system`, `source_resource_type`, `source_resource_id`, `resource_raw` (jsonb), `content_sha256` (bytea), `extraction_method`, `is_test_data`. Defaults it relies on: `id` (uuidv7), `epistemic_class` ('observation'), `ingested_at`. Unique on (`source_system`, `source_resource_type`, `source_resource_id`).
- `phsa.subject`: identity anchor: `label` (required), `fixture_id` (unique, nullable), `is_test_data`.
- `phsa.subject_demographics`, `phsa.condition`, `phsa.medication_request`: typed rows. Each has `subject_id`, a **unique** `source_record_id` (exactly one typed row per source record), and a nullable `superseded_by` pointing at the same table.
- `phsa.current_*` views: only rows where `superseded_by IS NULL`, plus `effective_class`. All "current" screens read these views.
- Triggers forbid UPDATE/DELETE on `source_record` and `record_confirmation`. A violation raises a PostgreSQL exception; the app must never trigger one in normal use.

### 4.1 The write recipe for every create (one transaction)

1. `new_id = SELECT uuidv7()::text` (from the database).
2. Build the FHIR-shaped JSON for the record (section 5). Its `id` must equal `new_id`. It must include the HTEST tag (section 5.0).
3. Insert into `phsa.source_record`:
   - `source_system` = `'phsa-crs-manual'` (constant)
   - `source_resource_type` = `'Patient'`, `'Condition'` or `'MedicationRequest'`
   - `source_resource_id` = `new_id`
   - `resource_raw` = the JSON
   - `content_sha256` = `sha256(convert_to(%s::jsonb::text, 'UTF8'))`, computed **in the database** from the same JSON string. This is the provisional canonicalization rule: hash of PostgreSQL's own `jsonb` text output. State this rule in the README and in a comment next to the SQL.
   - `extraction_method` = `'manual_entry'`
   - `is_test_data` = `true`
   - leave `epistemic_class` at its default
   - `RETURNING id`
4. Insert the typed row with `source_record_id` = the returned id.
5. Commit once at the end of the request. On any error, roll back everything.

### 4.2 The recipe for every supersede

1. Do 4.1 for the new version (new source record, new typed row, same `subject_id`).
2. `UPDATE phsa.<table> SET superseded_by = %(new)s WHERE id = %(old)s AND superseded_by IS NULL`.
3. If that updated exactly 0 rows (someone else already superseded it), roll back and return HTTP 409 with a plain message.
4. Commit.

### 4.3 Marking a record wrong is also a supersede (`entered-in-error`)

A new version, copied from the current one, with `verification_status = 'entered-in-error'` (conditions) or `status = 'entered-in-error'` (medication requests), then supersede as in 4.2. Keep all other values, including `clinical_status`. Demographics have no such action; correct them by superseding with the right values.

---

## 5. FHIR-shaped JSON the app writes

Keep these minimal. Include a key only when the value was entered; never write `null` or empty strings.

### 5.0 Common to all three

```json
{ "resourceType": "...", "id": "<new_id>",
  "meta": { "security": [ { "system": "http://terminology.hl7.org/CodeSystem/v3-ActReason", "code": "HTEST" } ] } }
```

### 5.1 Patient (for `subject_demographics`)

`name`: one entry `{"use":"official","family":..., "given":[...]}` if family or given entered; a second entry `{"use":"usual","text":...}` if a usual name was entered. `gender` (from `sex`: male/female/other/unknown). `birthDate` only if a full date was entered. `address`: `[{"city": home_municipality}]` if entered. `communication`: `[{"language": {"text": preferred_language}}]` if entered.

### 5.2 Condition

`clinicalStatus` = `{"coding":[{"system":"http://terminology.hl7.org/CodeSystem/condition-clinical","code":<clinical_status>}]}`; `verificationStatus` the same shape with system `.../condition-ver-status`, only if entered; `code` = `{"text": code_text}` plus `coding` only if both `code_system` and `code` entered; `subject` = `{"reference": "Patient/<subject_id>"}`; `onsetString` / `onsetDateTime` only if entered; `note` = `[{"text": ...}]` only if entered.

### 5.3 MedicationRequest

`status`, `intent`, `medicationCodeableConcept` = `{"text": medication_text}` (plus `coding` only if code pair entered), `subject` as above, `authoredOn` only if entered, `note` only if entered, and one `dosageInstruction` entry containing only what was entered: `text` (= `sig_text`), `route` = `{"text":...}`, `timing.repeat` = `{frequency, period, periodUnit, when:[...]}` with only the keys entered, `doseAndRate` = `[{"doseQuantity":{"value":..., "unit":..., "system":"http://unitsofmeasure.org", "code": dose_unit_code}}]` (omit `system` and `code` when `dose_unit_code` was not entered). Omit `dosageInstruction` entirely if nothing in it was entered.

---

## 6. Screens and routes

Every state-changing route is `POST`. Every page works as a normal full-page request first (M1 to M5); htmx enhancement is added last (M6).

| Route | Purpose |
|---|---|
| `GET /` | list subjects (label, fixture id, counts of current conditions and medications); link to create |
| `GET,POST /subjects/new` | create subject: `label` (required), `fixture_id` (optional text). `is_test_data` is not on the form |
| `GET /subjects/<id>` | overview: current demographics, current conditions, current medication requests; links to add / supersede / history |
| `GET,POST /subjects/<id>/demographics` | create demographics (if none) or supersede them |
| `GET,POST /subjects/<id>/conditions/new` | add condition |
| `GET,POST /conditions/<id>/supersede` | supersede (new version) |
| `POST /conditions/<id>/mark-error` | entered-in-error (4.3) |
| `GET,POST /subjects/<id>/medications/new` | add medication request |
| `GET,POST /medications/<id>/supersede` | supersede (new version) |
| `POST /medications/<id>/mark-error` | entered-in-error |
| `GET /subjects/<id>/history` | every version of every typed row for this subject, oldest first, with a "current / superseded by ..." marker |
| `GET /source-records/<id>` | read-only view: type, source_system, ingested_at, effective_class, is_test_data, `content_sha256` as hex, pretty-printed `resource_raw` |
| `GET /healthz` | `200 ok` if `SELECT 1` works, else `503` |

Lists hide `entered-in-error` rows by default and show a link "show entered-in-error (n)". The history page and source-record page show everything.

### 6.1 Page rules

- Button and link labels use the CRS verbs: **Create**, **Supersede (new version)**, **Mark entered-in-error**. Never "Edit", "Save changes", "Update" or "Delete".
- Every page carries a fixed banner: **"TEST DATA ONLY. Synthetic fixtures. Not a medical record."**
- Pico conventions: `<main class="container">`, `<nav>`, `<article>` for panels, `<table class="striped">`, forms as `<label>` wrapping the input, field errors as `<small>` under the field with `aria-invalid="true"` on the input. Light/dark follows the browser. Custom CSS goes in one small `static/app.css`; do not override Pico variables except where unavoidable. (Pico v2 custom properties are prefixed `--pico-`.)
- Show values exactly as stored. Dates as ISO (`YYYY-MM-DD`). Show `effective_class` as a small neutral tag.
- Server-side validation mirrors the database constraints so errors are friendly and never reach the database as exceptions:
  - `birth_year` and `birth_date`, if both given, must agree.
  - `dose_value` and `dose_unit` are both given or both blank.
  - `code_system` and `code` are both given or both blank (conditions and medications).
  - enums come from `<select>` lists matching the schema CHECK lists exactly; an empty option means blank where the column is nullable.
  - `frequency` positive integer (smallint range); `period` positive number.
  - `timing_when` multi-select from: `MORN, NOON, AFT, EVE, NIGHT, HS, WAKE, C, CM, CD, CV, AC, ACM, ACD, ACV, PC, PCM, PCD, PCV` (one constant in code, easy to edit).
  - required: `label`; `code_text`; `clinical_status`; `medication_text`; `status`.
- Validation failure: re-render the form with the user's input kept, **HTTP 422**, no database write.
- An unexpected database error: HTTP 500, a plain error page that shows no SQL and no connection details, and a server-side log line without the password.
- `POST` safety (this is a LAN app with no login, so keep it light): reject a `POST` whose `Origin` header is present and does not match the request's own scheme + host with HTTP 403. Jinja2 autoescape stays on everywhere; no `|safe` on user data.

---

## 7. htmx 4 and Pico: where they are used, and what to watch for

htmx is used in **three places only** (M6). Everything else is a plain link or form.
1. **Inline supersede:** on the subject page, a "Supersede" link swaps that row into a form (`GET` returns a fragment); saving returns the new version's row (`200`) or the form with errors (`422`).
2. **Mark entered-in-error:** a button with `hx-confirm`; the response replaces the row.
3. **Add condition / add medication** from the subject page: the new row appears without a full reload.

Detect a fragment request with the `HX-Request` header; without it, return the full page or a redirect-after-post. Both paths must remain working and tested.

**This is htmx 4, not htmx 2.** A model's default memory is htmx 2. These differences matter (source: the official htmx 4 migration guide):
- Requests use `fetch()`, not XMLHttpRequest.
- **Attribute inheritance is explicit.** An attribute on a parent no longer applies to children unless written with the `:inherited` suffix (for example `hx-confirm:inherited="..."`). Put each `hx-*` attribute directly on the element that needs it, and do not use `:inherited`.
- **htmx 4 swaps every response except `204` and `304`**, including 4xx and 5xx. That is what we want for `422` (validation form re-render). For `5xx` we do not want an error page swapped into the row: set `htmx.config.noSwap = [204, 304, '5xx']` in one small script after htmx loads, and show a plain "something went wrong" line on `htmx:response:error`.
- `hx-delete` no longer sends enclosing form fields. We do not use `hx-delete` at all (nothing is ever deleted).
- Renamed or removed: `hx-disable` now means "disable during request" (the old skip-processing attribute is `hx-ignore`); `hx-disabled-elt` is gone; `hx-vars` is gone (use `hx-vals`); `hx-ext` is gone. Event names follow `htmx:before:request`, `htmx:after:swap`, `htmx:response:error`, not the camelCase htmx 2 names.
- Default request timeout is 60 s.
- Set `hx-target` and `hx-swap` explicitly on every htmx element. Do not rely on defaults.

Unverified at spec time, so check in a real browser during M6 and note the result in `QUESTIONS.md`: that the `HX-Request` request header is still sent by htmx 4, and that setting `htmx.config.noSwap` from a script behaves as the migration guide shows.

---

## 8. SUBJ-01 seed data (synthetic fixture v0.21, 2 Sep 2026)

These are the only values the seed script may use. Anything not listed is blank.

**Subject**: `label` = `Efraín Colón Berríos (SUBJ-01, synthetic)`, `fixture_id` = `SUBJ-01`.

**Demographics**: `family_name` = `Colón Berríos` (both surnames, kept whole as a single family name: identity must never be inferred from the last surname token), `given_name` = `Efraín`, `usual_name` = `Don Efra`, `sex` = `male`, `home_municipality` = `Toa Alta`, `preferred_language` = `es`. `birth_year`, `birth_date`: blank (the fixture gives an age, 68, not a year).

**Conditions** (all: `clinical_status` = `active`, `verification_status` blank, `code_system`/`code` blank):

| # | `code_text` | `onset_text` | `note` |
|---|---|---|---|
| 1 | Type 2 diabetes | ~14 years | Insulin-dependent |
| 2 | Diabetic peripheral neuropathy | | Reduced foot sensation, unsteady on uneven ground |
| 3 | Hypertension | | Controlled |
| 4 | CKD stage 3a | | eGFR ~52 |
| 5 | Diabetic retinopathy, mild | | Low-light acuity loss |
| 6 | BPH | | On alpha-blocker |
| 7 | Hearing loss, high-frequency | | Mild, uncorrected |

**Medication requests** (all: `status` = `active`, `intent` = `order`, `authored_on`, `route_text`, codes blank, `safety_critical` left at its default):

| # | `medication_text` | `dose_value` | `dose_unit` | `frequency` | `period` | `period_unit` | `timing_when` | `sig_text` | `note` |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Metformin 500 mg | 500 | mg | 2 | 1 | d | | 500 mg BID with meals | |
| 2 | Insulin glargine 22 units | 22 | units | 1 | 1 | d | NIGHT | 22 units nightly | Refrigerated |
| 3 | Losartan 50 mg | 50 | mg | 1 | 1 | d | MORN | 50 mg daily AM | |
| 4 | Amlodipine 5 mg | 5 | mg | 1 | 1 | d | MORN | 5 mg daily AM | |
| 5 | Atorvastatin 20 mg | 20 | mg | 1 | 1 | d | NIGHT | 20 mg nightly | |
| 6 | Tamsulosin 0.4 mg | 0.4 | mg | 1 | 1 | d | NIGHT | 0.4 mg nightly | |
| 7 | Aspirin 81 mg | 81 | mg | 1 | 1 | d | | 81 mg daily | |

`dose_unit_code`: `mg` for the mg rows; blank for `units`.

**Deliberately not loaded** (the fixture has these, but they are scenario-design commentary, not record content): the fixture's "Clinical Note" column and the medication remarks such as dose caps, confusion pairs and orthostatic-risk remarks. Do not add them anywhere, not even in `note`.

**Seed script** (`scripts/seed_subj01.py`):
- Uses the app's own data-layer functions (same code path as the screens).
- **Dry run by default:** opens a transaction against `PHSA_TEST_DSN`, loads everything, prints counts, rolls back.
- `--commit --target phsa` writes to the real database. It refuses unless both flags are present, and it prints what database host/name it is about to write to, then asks two questions and requires the user to type `yes` to each: (1) "Is this the database you intend to write to?" and (2) "Has a fresh backup of the database VM been taken since the last schema change?". Any other answer exits without writing.
- If a subject with `fixture_id = 'SUBJ-01'` already exists in the target, it changes nothing and says so.

---

## 9. Tests

`pytest`, in `tests/`. Data-layer functions take a connection argument and **never commit**; the Flask request handler commits at the end of a successful request. Tests open a connection to `phsa_test`, begin a transaction, run the code, and **roll back at the end of every test**. HTTP tests use Flask's test client with the connection factory overridden so all requests share the test's transaction (commit becomes a no-op there). No test leaves a row behind; the role could not delete it anyway.

Required tests, by milestone:

- **M0 (skeleton and grants):** `/healthz` is 200. Using `phsa_app` against `phsa_test`: `DELETE FROM phsa.condition` raises `insufficient_privilege`; `UPDATE phsa.condition SET code_text = 'x'` raises `insufficient_privilege`; `UPDATE phsa.condition SET superseded_by = ...` is permitted; `TRUNCATE` raises `insufficient_privilege`; `INSERT` into `phsa.record_confirmation` raises `insufficient_privilege`.
- **M1:** create subject; label required; `is_test_data` is true in the row whatever the form posts (post a forged `is_test_data=false` and assert it is ignored); duplicate `fixture_id` shows a friendly error, not a 500; demographics create writes one `source_record` and one typed row with matching ids; `birth_year`/`birth_date` mismatch gives 422 and writes nothing.
- **M2/M3:** create condition / medication request; every blank optional field is `NULL` in the row and absent from `resource_raw`; code-pair and dose-pair violations give 422 and write nothing; `resource_raw.id` equals `source_resource_id`; `resource_raw.meta.security` contains HTEST; `content_sha256` equals `sha256(convert_to(resource_raw::text,'UTF8'))` recomputed in a test query.
- **M4:** supersede creates a new source record and typed row, and the old row's `superseded_by` equals the new row's id; the old row no longer appears in `current_*` and does appear in history; superseding a row that was already superseded returns 409 and writes nothing; mark-error produces a new version with the entered-in-error status and keeps `clinical_status`; entered-in-error rows are hidden in lists by default; no code path executes `DELETE` or an `UPDATE` of any column other than `superseded_by` (grep the source in a test), and no route path, function name, variable name or template text contains the words "edit", "update" or "delete" (the one SQL statement `UPDATE phsa.<table> SET superseded_by ...` in `queries.py`, and comments explaining the rule, are the only permitted occurrences).
- **M5:** the seed dry run on `phsa_test` produces 1 subject, 1 demographics row, 7 conditions, 7 medication requests, **15** source records, all with `is_test_data` true and `extraction_method = 'manual_entry'`, all visible in the `current_*` views with `effective_class = 'observation'`; a second run reports "already seeded" and changes nothing; the loaded values match section 8 exactly (test compares against a copy of the section 8 data kept in `seed/subj01.json`); none of the "deliberately not loaded" text appears anywhere.
- **M6:** fragment responses for the three htmx interactions; the full-page paths still pass their M1 to M5 tests unchanged.

---

## 10. Milestones (stop after each)

| | Deliverable |
|---|---|
| M0 | repo skeleton, config loading, DB access, `/healthz`, vendored files with `VENDORED.md`, the grant tests |
| M1 | subjects and demographics (create, view, supersede) |
| M2 | conditions (create, view) |
| M3 | medication requests (create, view) |
| M4 | supersede for conditions and medications, mark-error, history page, source-record page |
| M5 | `seed_subj01.py` and its tests (dry run only; **do not run with `--commit`**) |
| M6 | htmx enhancements (section 7), browser check of the two unverified htmx points |

After each milestone: all tests green, a short entry in `CHANGELOG.md`, one git commit, then a report (what was built, what was skipped, anything in `QUESTIONS.md`).

Repo layout (suggested): `app/` (factory, routes, `db.py`, `queries.py`, `fhir.py`, `validation.py`, templates), `static/` (`app.css`, `vendor/`), `scripts/`, `seed/`, `tests/`, `README.md`, `CHANGELOG.md`, `QUESTIONS.md`, `.env.example`, `.gitignore` (must include `.env`).

README must cover: purpose, how to run, env variables, the canonicalization rule for `content_sha256`, the append-only rules, and the fixture-only guarantee.

---

## 11. Known gaps in the schema the builder must not "fix"

These are known and deferred by the owner; the builder must not work around them in app code or propose DDL: a `TRUNCATE` is not blocked by the immutability triggers (the app role simply lacks the privilege); there is no writer/reader role split; there is no rule for re-importing a corrected resource that reuses a source id (manual entries get fresh ids, so it does not arise here); `confirmed_by` is free text; typed rows are not tied by a key to the matching `source_resource_type`, so the app must pair them correctly itself.

---

## 12. Seed-data decisions (confirmed by José, 4 Oct 2026)

These were open assumptions through v0.3. All five are confirmed as written; the builder treats them as settled and does not reopen them.

1. `clinical_status` and medication `status` are loaded as `active`. The fixture lists these as the person's current problems and medications but states no status field, and the schema requires one.
2. `timing_when`: "Daily AM" becomes `MORN` and "Nightly" becomes `NIGHT`. "With meals" (metformin) and "daily" (aspirin) are left blank rather than guessed; the wording is kept in `sig_text`.
3. `preferred_language` = `es`, from "Spanish-dominant".
4. The subject `label` is `Efraín Colón Berríos (SUBJ-01, synthetic)`.
5. The scenario-design columns of the fixture are excluded (section 8).
