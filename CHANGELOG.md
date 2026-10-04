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
  `PHSA_DSN` in M0.
- Vendored `static/vendor/htmx.min.js` (4.0.0) and `pico.min.css` (2.1.1)
  with `static/vendor/VENDORED.md` (name, version, URL, sha256).
- M0 tests written: `/healthz` 200; grant proofs against `phsa_test`
  (DELETE/TRUNCATE denied, UPDATE of non-`superseded_by` denied,
  `superseded_by` update permitted, INSERT into `record_confirmation` denied).

Status: **partially verified** — the `/healthz` contract (200 on `SELECT 1`,
503 otherwise) is covered by tests that run without a database (stub pool) and
pass; the 5 grant tests and the live `/healthz` 200 test are written but skip
on this machine because both DSNs fail with
`FATAL: password authentication failed for user "phsa_app"`. See QUESTIONS.md.
They will run and be proven as soon as the database accepts the credentials.
