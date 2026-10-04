# CHANGELOG

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
