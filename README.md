# PHSA Recorder (CRS)

A small LAN web app to enter and correct one subject's record in the PHSA
recorder database. Its first job is to load the synthetic fixture person
("Don Efra", SUBJ-01) by hand-equivalent entry.

It **records what a document says**. It never diagnoses, triages, recommends,
scores risk, or interprets.

## Python

Python 3.13.5 (interpreter already on the LXC).

## Install and run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in the real DSNs; chmod 600 .env
.venv/bin/python -c "from app import create_app; create_app().run(host='127.0.0.1', port=8470)"
```

For a real (LAN) run use gunicorn:

```sh
PHSA_HOST=0.0.0.0 .venv/bin/gunicorn --bind 0.0.0.0:8470 'app:create_app()'
```

Tests (need the test database):

```sh
.venv/bin/python -m pytest
```

## Environment variables

| Variable | Meaning | Default |
|---|---|---|
| `PHSA_DSN` | libpq DSN for the real database (`phsa`) | none — the app refuses to start without it |
| `PHSA_TEST_DSN` | DSN for `phsa_test` (tests only) | none — tests skip with a clear message |
| `PHSA_HOST` | bind address | `127.0.0.1` |
| `PHSA_PORT` | bind port | `8470` |

The DSNs contain the password. They come from the environment (or the
git-ignored `.env`, loaded via `python-dotenv`) and are never printed, logged,
or copied.

## `content_sha256` canonicalization rule

Provisional rule: `content_sha256` is computed **in the database** as

```sql
sha256(convert_to(%(json)::jsonb::text, 'UTF8'))
```

from the same JSON string that is stored in `resource_raw` — i.e. the hash of
PostgreSQL's own `jsonb` text output. There is no application-side
canonicalization.

## Append-only rules

The app's verbs are **Create, Read, Supersede**. There is no Update and no
Delete. To change a record, a new version is inserted and the old row's
`superseded_by` pointer column is set (the only UPDATE the app can issue; the
database role has UPDATE on that column only, and the immutable tables refuse
any UPDATE via triggers). Marking a record wrong is also a supersede
(`verification_status`/`status` = `entered-in-error`).

Every row the app writes has `is_test_data = true` — a constant in code, not a
form field or switch.

## Fixture-only guarantee

The only person this app loads in v1 is the synthetic fixture SUBJ-01
(spec section 8). All its rows are test data, flagged as such, with the HTEST
tag inside `resource_raw`. Every page carries the banner:
**TEST DATA ONLY. Synthetic fixtures. Not a medical record.**

## Layout

- `app/` — Flask app: `config.py`, `db.py`, `routes.py`, `queries.py`, `fhir.py`, `validation.py`, `templates/`
- `static/` — `app.css`, `vendor/` (vendored htmx 4.0.0 + Pico 2.1.1, see `vendor/VENDORED.md`)
- `scripts/`, `seed/` — seed script and its data (M5)
- `tests/` — pytest; all tests run inside a transaction that is rolled back
- `phsa_v0.3.sql` — the database schema (read-only reference, source of truth)
