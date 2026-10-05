# CHANGELOG

## M4c (2026-10-05) — mark a condition or medication request entered-in-error

Mark a record entered-in-error: two data-layer functions in `app/queries.py`
(`mark_condition_entered_in_error`, `mark_medication_request_entered_in_error`)
that reuse the existing supersede functions — a new version is copied from the
current one with the status changed to `entered-in-error`; conflicts
(already superseded, already marked) raise with the fixed spec-0 messages.
Two POST routes in `app/routes.py`, a button on the subject page
(`templates/subject.html`), and 17 tests (`tests/test_mark_error.py`).

## M4b (2026-10-05) — supersede a medication request

The second step of M4. Supersede a medication request: data layer
(`app/queries.py`: `get_medication_request`, `supersede_medication_request`
— recipe 4.2; the single permitted UPDATE is the `superseded_by` pointer,
conflict guard on `superseded_by IS NULL`), route (`app/routes.py`:
`/medications/<id>/supersede` GET/POST, 404 missing, 409 already superseded,
422 validation with the user's input kept, plain 500 page), template mode
(`templates/medication_request_new.html` gains a "supersede" mode), the
"Supersede (new version)" link on the subject page
(`templates/subject.html`), and tests
(`tests/test_medication_supersede.py`, new, including a round-trip test that
shows the current row exactly in the supersede form and posts the same values
back, asserting the new row equals the old row in every compared column).

The round-trip test is what found the decimal defect recorded in the
Post-M3 fix entry below (a dose typed as `0.4` was stored as the float
`0.400000000000000022...`); M4b was finished only after that fix landed.

## Post-M3 fix (2026-10-05) — decimal dose and period values are stored exactly

Written by the reviewer at José's direction, not by the builder.

A defect from M3, found by M4b's round-trip test: a dose typed as `0.4` was
stored as `0.4000000000000000222044604925031308...`. `app/validation.py`
parsed `dose_value` and `period` with `float()`, and `app/queries.py` then
called `Decimal()` on that float, which keeps the float's binary rounding
error. Spec rule 2: never normalize a value. M3's tests used only whole
numbers, so they did not see it. `NaN` and `Infinity` were also accepted as a
dose or a period.

- `app/validation.py`: `dose_value` and `period` are parsed with `Decimal`
  from the typed text (`_positive_decimal`). Rejected with the existing 422
  messages: text that is not a number, NaN, Infinity, zero, negatives, and
  more than 15 significant digits (`MAX_DECIMAL_DIGITS`; up to 15 digits the
  stored document and the typed row hold the same digits).
- `app/queries.py`, `create_medication_request`: `Decimal(str(x))`, so a
  float from any caller is stored as the digits it prints.
- `app/fhir.py`: `_json_number` writes the dose value and the period to the
  document as a plain JSON number (500, 0.4, 0.5). A whole dose is now `500`
  in the document, where it was `500.0`.
- `tests/test_medication_decimals.py` (new, 24 tests): typed row and stored
  document hold the typed digits for 0.4, 0.125, 2.5, 22, 81, 500 and for
  periods 0.5, 1, 1.5, 12; bad values give 422 and write nothing; a float
  handed to the data layer is stored exactly. Without the fix 12 of the 24
  fail.

No schema change. Nothing had been written to the real database, so no
stored value needs correcting. 110 tests pass (86 + 24).

## M4a (2026-10-05) — supersede a condition

The first step of M4. Supersede a condition: data layer (`app/queries.py`:
`get_condition`, `supersede_condition` — recipe 4.2; the single permitted
UPDATE is the `superseded_by` pointer, conflict guard on `superseded_by IS
NULL`), route (`app/routes.py`: `/conditions/<cid>/supersede` GET/POST, 404
missing, 409 already superseded, 422 validation with the user's input kept,
plain 500 page), template mode (`templates/condition_new.html` gains a
"supersede" mode), the "Supersede (new version)" link on the subject page
(`templates/subject.html`), and tests
(`tests/test_condition_supersede.py`, new).

## Pre-M4-fix (2026-10-05) — two small fixes in `app/routes.py` (not milestone work; M4 not started)

1. **A valid demographics POST for a missing subject answers 404, not 500.**
   The demographics route's valid-form path now looks the subject up with
   `queries.get_subject` first (as the condition and medication routes
   already did on their valid-form path) and answers 404
   ("no such subject") before any write. Covers the Pre-M4 note in
   QUESTIONS.md.
2. **The four write blocks finish the connection on every way out.**
   `subject_new`, the demographics POST-write, the condition POST-write and
   the medication POST-write blocks each now call `_finish` exactly once,
   in a `finally:` with `commit=True` only when the write succeeded and
   `commit=False` on every other way out (the 404, the 409 supersede
   conflict, the duplicate-subject 409, the plain 500 error page); the
   older in-line `_finish` calls in those blocks are removed so no path
   calls it twice. Log lines and user-facing messages are unchanged;
   `_finish` and `_request_conn` themselves are unchanged.
   New tests in `tests/test_error_pages.py`: a valid demographics POST for
   a random subject id answers 404 with row counts unchanged; a valid
   demographics POST for a real subject whose `create_demographics` is
   patched to raise `psycopg.OperationalError("boom")` before any SQL
   answers the plain 500 page (no "boom"/"psycopg"/"Traceback") with no
   demographics row written; and, with the pool's `get`/`put` wrapped in
   counters, the number of `put` calls equals the number of `get` calls
   for all six requests (missing-subject 404 and boom 500 on each of the
   three write forms).

## Pre-M4 (2026-10-05) — two fixes found in review (not milestone work; M4 not started)

1. **The stored MedicationRequest JSON now carries `intent`.** Spec section 5.3
   lists `intent` among the keys of the document; "not on the form" meant only
   that the user does not choose it. `app/fhir.py` (`medication_request_json`)
   now always writes `"intent": "order"` and its docstring is corrected (it
   said the opposite). The SQL insert is unchanged: the typed row still gets
   'order' from the schema default, so document and row agree.
   `tests/test_medications.py`: the assertion `"intent" not in raw` is
   replaced by one asserting the stored document's intent is "order" and
   equals the typed row's `intent` column. **The M3 entry's statement that
   `intent` "is never written" is corrected by this entry; the M3 entry
   itself is left as written.**
2. **The error handling in `app/routes.py` no longer raises
   `AttributeError` on database errors.** In 9 places the pattern
   `except Exception as e: if e.code == 404: raise; ...` assumed every
   exception has a `.code`; a database error has none, so the code raised
   `AttributeError` at the moment it was meant to show the plain error
   page. Each spot is now `except HTTPException: raise` (imported from
   `werkzeug.exceptions`; no new dependency) followed by the existing
   `except Exception` handler with its log line and message unchanged;
   every `finally:` block is untouched. New `tests/test_error_pages.py`:
   with `app.queries.get_subject` monkeypatched to raise
   `psycopg.OperationalError("boom")` before any SQL runs, the subject
   overview GET, the demographics form GET and POST, the new-condition
   form GET plus an invalid and a valid POST, and the new-medication
   form GET plus an invalid and a valid POST all return 500 with the
   route's plain message, never "boom"/"psycopg"/"Traceback", and leave
   the row counts unchanged. All 9 places are reached by those requests
   (subject overview; demographics GET, POST-validation, POST-write;
   condition GET, POST-validation, POST-write; medication GET,
   POST-validation, POST-write). The existing 404 tests pass unchanged:
   a missing subject is still a 404, never a 500.
   (On 5 Oct, completing this step, the demographics POST in that file
   was corrected to an INVALID form, and the connection-restoration
   helpers that tolerated unreadable counts were removed: a valid
   demographics POST never looks the subject up first, so it cannot be
   exercised with the get_subject patch. See the note in QUESTIONS.md.)

## M3 (2026-10-05) — medication requests: create and view (spec section 10)

Milestone M3: `GET,POST /subjects/<id>/medications/new` (spec section 6) and
the subject overview listing the subject's current medication requests from
`phsa.current_medication_request`, with an add link. Supersede and
mark-entered-in-error for medication requests are M4 and are not built.
(Continued an interrupted pass; the app-side half was already on disk.)

- `app/fhir.py`: `medication_request_json` (spec 5.3): `status`,
  `medicationCodeableConcept` = `{"text": ...}` plus `coding` only if both
  `medication_code_system` and `medication_code` were entered, `subject` =
  `Patient/<subject_id>`, `authoredOn` only if entered, `note` only if
  entered, and one `dosageInstruction` entry carrying only what was entered
  (`text` = sig_text, `route` = `{"text": ...}`, `timing.repeat` =
  `{frequency, period, periodUnit, when:[...]}` with only the keys entered,
  `doseAndRate` with `doseQuantity` = `{value, unit}` plus
  `system`/`code` only when a UCUM code was entered). `dosageInstruction` is
  omitted entirely when nothing in it was entered. `intent` is not on the
  form and is left to the schema default ('order'), so it is never written.
  Blank means absent, never `null`/`""` (spec rule 2); the HTEST tag comes
  from the shared `build_resource` (5.0).
- `app/validation.py`: `validate_medication_request` — `medication_text` and
  `status` required; `medication_code_system` and `medication_code` both
  given or both blank (the schema's `med_code_pair` CHECK); `dose_value` and
  `dose_unit` both given or both blank (the schema's `dose_pair` CHECK);
  `frequency` a positive integer in smallint range; `period` a positive
  number; `status` and `period_unit` as selects matching the schema CHECK
  lists exactly (`MED_STATUS_OPTIONS` includes `entered-in-error`, kept per
  the owner's 5 Oct 2026 decision; `PERIOD_UNIT_OPTIONS` = s/min/h/d/wk/mo/a);
  `timing_when` a multi-select from `TIMING_WHEN_OPTIONS`, one constant in
  code (the spec 6.1 FHIR event-timing list).
- `app/queries.py`: `list_current_medication_requests` (from the
  `phsa.current_medication_request` view) and `create_medication_request`
  (write recipe 4.1: uuidv7, source record with `MedicationRequest` type,
  typed row; `intent` left to its schema default and `safety_critical` to its
  default, neither on the form; never commits; `is_test_data` a constant
  true). The 4.2 recipe is not added here; that is M4.
- `app/routes.py`: `GET,POST /subjects/<id>/medications/new` — Origin check
  on POST, 422 with the input kept on validation failure and no database
  write, 500 page on unexpected database errors, 302 to the subject page on
  success; the subject overview now lists current medication requests with an
  add link.
- `templates/medication_request_new.html` (new): the form per spec 6.1 —
  `<label>` wrapping the inputs, `<small>` field errors with
  `aria-invalid="true"`, the fixed test-data banner (inherited), button
  labelled **Create**.
- `templates/subject.html`: the medication-requests block now carries the
  **Add a medication request** link.
- `tests/test_medications.py` (new): the medication items of the spec-section-9
  M2/M3 list — create writes one source record + one typed row with matching
  ids (`intent` = 'order', `safety_critical` = true from the schema defaults);
  every blank optional field is NULL in the row and absent from `resource_raw`;
  `dosageInstruction` omitted when nothing in it was entered; code-pair and
  dose-pair violations (system only / code only; value only / unit only) give
  422 and write nothing; required `medication_text` / `status` give 422 and
  write nothing; a status outside the CHECK list 422s; bad `frequency` /
  `period` 422; `resource_raw.id` equals `source_resource_id`;
  `resource_raw.meta.security` contains HTEST; `content_sha256` equals
  `sha256(convert_to(resource_raw::text,'UTF8'))` recomputed in a test query;
  the overview page lists current medication requests and the add link; the
  new-page GET renders; a 404 for an unknown subject.
- `tests/test_zz_no_leak.py`: the no-leak check now also asserts
  `phsa.medication_request` holds 0 rows (still last, still passes).

Two full `.venv/bin/python -m pytest -v` runs, same result, 0 skipped:
**64 passed** (47 pre-M3 + 17 new) — with the no-leak test passing in both,
so the test database ends empty. Nothing was written to the real database.
No new QUESTIONS.md entries were needed this pass (the M2 entry's Question 2
— `entered-in-error` stays a choice when creating a record — was already
decided and recorded).

M4 not started.

## Pre-M3 (2026-10-04) — onset is text or date, never both (spec v0.5)

The owner decided the question raised in `QUESTIONS.md` after M2 (the
`onsetString`/`onsetDateTime` mapping): a condition's onset is given as text
OR as a date, never both (spec v0.5, sections 5.2, 6.1 and 9). No data-model
change: both columns stay. Not milestone work; M3 not started.

- `app/validation.py`: `validate_condition` now errors when `onset_text` and
  `onset_date` are both given; the input is kept, the form re-renders with
  HTTP 422 and an error under the onset fields, and nothing is written (same
  pattern as the `code_system`/`code` pair rule).
- `app/queries.py`: `create_condition` also refuses both onset values,
  raising `ValueError` before any SQL runs, so no caller can bypass the form.
- `templates/condition_new.html`: one line of help text by the onset fields
  ("Fill in one or the other of the two onset fields, not both.").
- `tests/test_conditions.py`: posting both onset values gives 422 and writes
  nothing (row counts before and after); the data layer raises `ValueError`.
  No existing test passed both onset values (they passed one or neither);
  none changed.

## M2 (2026-10-04) — conditions: create and view (spec section 10)

Milestone M2: `GET,POST /subjects/<id>/conditions/new` (spec section 6) and
the subject overview listing the subject's current conditions from
`phsa.current_condition`, with an add link. Supersede and
mark-entered-in-error for conditions are M4 and are not built.

- `app/fhir.py`: `condition_json` (spec 5.2): `clinicalStatus` (always, from
  the form's required select), `verificationStatus` only if entered, `code`
  as `{"text": ...}` plus `coding` only if both `code_system` and `code`
  were entered, `subject` = `Patient/<subject_id>`, `onsetString` /
  `onsetDateTime` / `note` only if entered. Blank means absent, never
  `null`/`""` (spec rule 2); the HTEST tag comes from the shared
  `build_resource` (5.0).
- `app/validation.py`: `validate_condition` — `code_text` and
  `clinical_status` required; `code_system` and `code` both given or both
  blank (the schema's `code_pair` CHECK); both selects match the schema
  CHECK lists exactly (`CLINICAL_STATUS_OPTIONS` =
  active/recurrence/relapse/inactive/remission/resolved;
  `VERIFICATION_STATUS_OPTIONS` = unconfirmed/provisional/differential/
  confirmed/refuted/entered-in-error), an empty option meaning blank where
  the column is nullable; a value outside the list is an error, not a
  silent blank.
- `app/queries.py`: `list_current_conditions` (from the
  `phsa.current_condition` view) and `create_condition` (write recipe 4.1:
  uuidv7, source record with `Condition` type, typed row; never commits;
  `is_test_data` a constant true). The 4.2 recipe is not added here; that is
  M4.
- `app/routes.py`: `GET,POST /subjects/<id>/conditions/new` — Origin check
  on POST, 422 with the input kept on validation failure and no database
  write, 500 page on unexpected database errors, 302 to the subject page on
  success; the subject overview now lists current conditions (wording,
  status, effective class) with a link to add one.
- `templates/condition_new.html` (new): the form per spec 6.1 — `<label>`
  wrapping the inputs, `<small>` field errors with `aria-invalid="true"`,
  the fixed test-data banner (inherited), button labelled **Create**.
- `templates/subject.html`: the conditions block now shows
  `onset_text`/`onset_date` and `note` as stored (ISO dates, blank as "—")
  plus the **Add a condition** link.
- `tests/test_conditions.py` (new): the condition items of the spec-section-9
  M2/M3 list — create writes one source record + one typed row with matching
  ids; every blank optional field is NULL in the row and absent from
  `resource_raw`; code-pair violations (system only / code only) give 422
  and write nothing (row counts before and after); `code_text` and
  `clinical_status` required, 422, nothing written; `resource_raw.id` equals
  `source_resource_id`; `resource_raw.meta.security` contains HTEST;
  `content_sha256` equals `sha256(convert_to(resource_raw::text,'UTF8'))`
  recomputed in a test query; the coded-plus-text case writes both `code`
  and `coding`; the overview page lists current conditions and the add link;
  the new-page GET renders.
- `tests/test_zz_no_leak.py`: the no-leak check now also asserts
  `phsa.condition` holds 0 rows.

Two readings the spec did not pin down are recorded in `QUESTIONS.md`
(2026-10-04 M2 entry): the `onsetString`/`onsetDateTime` column mapping, and
whether the create form should offer `entered-in-error` before M4 lands.

M3 not started.

## M1 (2026-10-04) — subjects and demographics (create, view, supersede), and the test commit-leak fix

M1 proper (spec section 10: subjects and demographics — create, view,
supersede) plus one serious fix to the interrupted M1 work already on
disk.

### The commit leak, and its fix (tests/conftest.py)

The interrupted M1 pass had handed the app the test's OWN connection
(`_SharedPool` in `tests/conftest.py` returned the raw shared
connection). The request handler then called `conn.commit()` on it. A
commit is final: the fixture's later `rollback()` undoes nothing, so
rows were committed to the test database and then broke tests that
expect empty tables — and the app role cannot delete them.

Fixed in `tests/conftest.py` only (no app-code change):

- `_SharedPool.get()` now executes `SAVEPOINT req` on the shared
  connection and returns a small proxy (`_SavepointProxy`), not the raw
  connection.
- the proxy's `commit()` does nothing (spec section 9: "commit becomes
  a no-op" in HTTP tests);
- the proxy's `rollback()` executes `ROLLBACK TO SAVEPOINT req`, which
  undoes exactly that request and re-arms the savepoint for the next;
- every other attribute passes through to the real connection via
  `__getattr__`;
- the fixture still does the real `rollback()` and `close()` at the end.

A new last-running test `tests/test_zz_no_leak.py` opens a NEW
connection to `PHSA_TEST_DSN` and asserts `phsa.source_record`,
`phsa.subject` and `phsa.subject_demographics` each hold 0 rows,
proving no test in the session left a row behind. Both mandatory
verification runs give the same result (see below) and the no-leak
test passes in both: the test database is left empty.

Also: `open_test_transaction` removed from `app/queries.py` — test
helpers live under `tests/`, never under `app/`.

### M1 work (kept from the interrupted pass; verified this pass)

- `app/queries.py` (new data layer): `create_subject`,
  `list_subjects`, `get_subject`, `get_current_demographics`,
  `create_demographics`, `supersede_demographics` (the 4.2 recipe: new
  source record + typed row, then the single permitted
  `UPDATE ... SET superseded_by` pointer; 0 rows matched →
  `SupersedeConflict` → HTTP 409). All schema-qualified, all take a
  connection and never commit. `is_test_data` is a constant `true` in
  every INSERT, never taken from the form.
- `app/fhir.py`: `patient_json` (spec 5.1) with the HTEST tag (5.0);
  blank fields are absent from the JSON, never `null`/`""`.
- `app/validation.py`: `validate_subject` (label required;
  `is_test_data` deliberately ignored) and `validate_demographics`
  (all optional; `birth_year`/`birth_date` must agree if both given —
  mirrors the schema CHECK).
- `app/routes.py`: `GET /` (subject list with current counts +
  Create link), `GET,POST /subjects/new`, `GET /subjects/<id>`
  (overview), `GET,POST /subjects/<id>/demographics` (create if none,
  otherwise supersede). Page rules per spec 6.1: CRS verb labels only,
  the fixed test-data banner in `base.html` (every page), 422 with the
  form re-rendered and the user's input kept, 409 for a
  duplicate `fixture_id` / already-superseded row (friendly text, not a
  500), a plain 500 page on unexpected database errors, and the Origin
  check on every POST.
- `templates/` (new): `subject.html`, `subject_new.html`,
  `demographics.html`, `error.html`; `index.html` extended with the
  counts and the Create link.
- `tests/test_subjects.py`, `tests/test_demographics.py` (new):
  create subject (row content, blank-stays-NULL, `is_test_data`
  constant against a forged form value, 422 label-required writes
  nothing, duplicate fixture_id → 409 friendly, Origin 403);
  demographics create via HTTP (exactly one new source record, ids
  match, `is_test_data` true, HTEST in `resource_raw`),
  create-then-supersede page flow, supersede via HTTP (pointer set,
  current view shows only the new row), 422 birth mismatch writes
  nothing (row counts before/after), agreeing year+date accepted,
  404s, index lists.

### Verification (see QUESTIONS.md for the one test bug found and fixed)

Two full `.venv/bin/python -m pytest -v` runs, same result, 0 skipped:
**31 passed** — with the no-leak test
(`tests/test_zz_no_leak.py`, last) passing in both, so the test
database ends empty. Nothing was written to the real database.

During this pass, `test_supersede_demographics_data_layer` failed
deterministically in the first two runs. Diagnosis (full text in
`QUESTIONS.md`, 2026-10-04 M1 entry): a test bug, not an app bug —
its `tx_conn.rollback()` (commented "discard the failed attempt")
rolled back the ENTIRE test transaction, so the rows created earlier
in the same test were undone and the final lookup saw nothing. Fixed
in `tests/test_demographics.py` only (owner-directed): the refused
call is wrapped in `with tx_conn.transaction():` (a savepoint that
discards exactly that attempt) and the `tx_conn.rollback()` line
deleted. Both verification runs after the fix: 31 passed, 0 skipped.

M2 not started.

## Pre-M1 (2026-10-04) — skip/fail semantics and /healthz robustness

Three fixes to M0 code (no milestone work; M1 not started):

1. **A failed database connection now FAILS the test, not skip it**
   (`tests/conftest.py`, `tests/test_healthz.py`). The connection-exception
   skips are gone: with the DSN set, a broken connection makes the test fail
   with the first line of the error. Spec section 2 allows a skip only when
   the DSN is unset.
2. **An unset (or empty) `PHSA_TEST_DSN` now skips the database tests instead
   of raising** (`app/config.py` `load_config_for_tests`, `tests/conftest.py`).
   It no longer raises `ConfigError`; `test_dsn` is None and the five grant
   tests skip with `SKIP_MSG`. Stub-pool tests that need no database still run
   and pass. (Also handles the `PHSA_TEST_DSN=` empty-but-set case, which
   `os.environ.get(key, default)` does not.)
3. **`/healthz` returns 503, never 500, when the connection is broken**
   (`app/routes.py` `_probe_pool`). A failure of the cleanup `rollback()` on a
   broken connection no longer escapes; the connection is always handed back
   with `pool.put(conn)`. New contract test
   `test_healthz_503_when_rollback_raises` (stub raises on `execute()` AND on
   `rollback()`; asserts 503 and that `put()` was called).

Supporting changes needed to make the three fixes hold:
- `AppPool.conninfo` property/setter: rebind the lazily-opened pool to a new
  DSN (used by the healthz test so the endpoint probe follows the test DSN);
  pool opened with `min_size=0` and `getconn(timeout=10)` so a broken DSN
  fails fast (a few seconds) instead of blocking on the pool's 30 s init
  timeout.
- `tests/conftest.py`: clears `PG*`/`PGPASSFILE`-family environment variables
  so libpq cannot route a "broken" DSN through some other local configuration;
  a broken DSN now fails for the reason the DSN itself gives.

Verification (full outputs in the Pre-M1 report):
- Run A (normal env): 9 passed, 0 skipped.
- Run B (`PHSA_TEST_DSN=`): 5 skipped with `SKIP_MSG`, 4 passed (incl.
  `test_healthz_200` and all stub tests), 0 errors.
- Run C (`PHSA_TEST_DSN=postgresql://phsa_app:x@127.0.0.1:1/phsa_test`):
  0 skipped, 3 passed (stub tests), 5 database tests fail with
  `Connection refused` as the first error line, 1 failed
  (`test_healthz_200`: its endpoint probe follows the broken test DSN and
  correctly returns 503 — see QUESTIONS.md, where this deviation from the
  literal expected "FAILED (connection refused)" outcome is documented).

Note: pytest reports an exception raised in a non-yield fixture as `ERROR`,
not `FAILED` — in run C the five grant tests carry that label with the
connection error as the reason; they failed to connect and ran nothing.


## M0 (2026-10-04) — skeleton, config, DB access, /healthz, vendored files

- Repo skeleton per spec section 10: `app/` (factory, `config.py`, `db.py`,
  `routes.py`, `queries.py`, `fhir.py`, `validation.py`), `templates/`,
  `static/` (`app.css`, `vendor/`), `scripts/`, `seed/`, `tests/`.
- `.venv` created (Python 3.13.5); dependencies from spec section 2 installed
  and pinned in `requirements.txt` (Flask 3.1.3, psycopg[binary,pool] 3.3.6/3.3.3,
  python-dotenv 1.2.4, gunicorn 26.2.0, pytest 9.1.1).
- `python-dotenv` is used for the `.env` loader, per the spec's option.
- Config loading: `PHSA_DSN` required (app refuses to start without it),
  `PHSA_TEST_DSN` optional (tests skip if unset), `PHSA_HOST`/`PHSA_PORT`
  with spec defaults. DSNs are never printed or logged.
- `GET /healthz`: 200 ok on `SELECT 1`, 503 otherwise — the only use of
  `PHSA_DSN` in M0. Placeholder `GET /` renders the base page (Pico, test-data
  banner) with no database query.
- Vendored `static/vendor/htmx.min.js` (4.0.0) and `pico.min.css` (2.1.1)
  with `static/vendor/VENDORED.md` (name, version, URL, sha256).
- M0 tests, all passing (8 passed, 0 skipped) against the live databases:
  - `/healthz` is 200 (live `SELECT 1`); endpoint contract (200/503) also
    covered by stub-pool tests that run without a database.
  - Grant proofs as `phsa_app` against `phsa_test`: `DELETE FROM phsa.condition`
    → `insufficient_privilege`; `TRUNCATE phsa.condition` → `insufficient_privilege`;
    `UPDATE phsa.condition SET code_text = 'x'` → `insufficient_privilege`;
    `UPDATE ... SET superseded_by` → permitted; `INSERT INTO phsa.record_confirmation`
    → `insufficient_privilege`. Each runs in a transaction that is rolled back;
    none commits.
- Fixed during verification: the `.env` credential typo (owner-side, see
  QUESTIONS.md); a pool `configure` that left connections INTRANS (psycopg_pool
  discarded them — search_path is set server-side on the role instead); a
  `putconn(close=...)` argument removed in psycopg-pool 3.3; template folder
  pointing at `app/templates` instead of the repo's `templates/`.
- Status: **M0 complete.** No writes were made to the real database (only the
  `/healthz` SELECT 1). M1 not started.
