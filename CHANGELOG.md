# CHANGELOG

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

### Verification and one remaining failure (see QUESTIONS.md)

The commit-leak fix holds: two full `.venv/bin/python -m pytest -v`
runs, same result, 0 skipped; the no-leak test passes in both and the
database is empty afterwards.

One test still fails, deterministically, in both runs:
`tests/test_demographics.py::test_supersede_demographics_data_layer`
— the pre-existing (interrupted-pass) data-layer 409 test. As written
it cannot produce its intended scenario inside one transaction:
superseding a row that already has a second current version in the
same open transaction hits the `source_record_id` unique constraint
before the pointer UPDATE, so it raises `UniqueViolation` instead of
`SupersedeConflict`; the pointer UPDATE matching 0 rows (the 409
branch) needs the old row superseded before the transaction began. The
app's 409 branch itself is exercised and passes via the HTTP
supersede tests. Full diagnosis and the deferral to M4 are in
`QUESTIONS.md` (2026-10-04 M1 entry); no `app/` file was changed to
"fix" it.

Result of both verification runs (identical): **30 passed, 0 skipped,
1 failed** (`test_supersede_demographics_data_layer`, pre-existing
test-vs-data-layer mismatch, documented above). Nothing was written to
the real database; the test database ends empty. M2 not started.

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
