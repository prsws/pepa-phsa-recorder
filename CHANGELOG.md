# CHANGELOG

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
