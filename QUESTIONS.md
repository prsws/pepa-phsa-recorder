# QUESTIONS.md

## 2026-10-04 (M0): `FATAL: password authentication failed for user "phsa_app"` on both DSNs — RESOLVED

When building M0, every connection attempt with the role `phsa_app` failed
authentication on host 192.168.10.18, port 5432:

```
FATAL:  password authentication failed for user "phsa_app"
```

This happened for both `phsa` and `phsa_test`, using `psycopg` 3.3.6 (pure-Python
driver, libpq not installed) against the DSNs from `.env`, with the password
supplied from the DSN. A control connect with the same host/port/user and **no**
password failed differently, as expected (`fe_sendauth: no password supplied`),
so the server was reachable and was the one from spec section 2; the password in
`.env` was simply not being accepted for this role.

Impact then: the M0 grant tests (spec section 9: DELETE/TRUNCATE denied, UPDATE
of non-`superseded_by` denied, `superseded_by` update permitted, INSERT into
`record_confirmation` denied) and the `/healthz` 200 test could not be
executed. The test code was written and in place.

Per spec section 3 ("if the connection fails, write the exact error in
QUESTIONS.md and stop") the build stopped here rather than touching
`pg_hba.conf`, changing credentials, or otherwise working around it.

**Resolved 2026-10-04 (same day).** The `.env` typo was fixed by the owner.
All 8 M0 tests now run with 0 skipped and pass:

```
tests/test_grants.py::test_delete_forbidden PASSED
tests/test_grants.py::test_truncate_forbidden PASSED
tests/test_grants.py::test_update_non_pointer_column_forbidden PASSED
tests/test_grants.py::test_update_superseded_by_permitted PASSED
tests/test_grants.py::test_record_confirmation_insert_forbidden PASSED
tests/test_healthz.py::test_healthz_200 PASSED
tests/test_healthz_contract.py::test_healthz_503_when_unreachable PASSED
tests/test_healthz_contract.py::test_healthz_200_when_reachable PASSED
========================= 8 passed in 0.16s ==========================
```

(No schema, database, or `.env` changes were made by the builder to achieve
this; only test/app code fixes, none of which affect the database.)

## 2026-10-04 (Pre-M1): run C — `test_healthz_200` fails on the *endpoint* assertion, not with the connection error

Pre-M1 verification run C sets `PHSA_TEST_DSN=postgresql://phsa_app:x@127.0.0.1:1/phsa_test`.
Expected: "database tests FAILED (connection refused), 0 skipped, stub tests
pass."

What happened: the 5 grant tests fail as expected — first line of the error
is `psycopg.OperationalError: connection failed: connection to server at
"127.0.0.1", port 1 failed: Connection refused` (pytest labels an exception
raised in a non-yield fixture `ERROR`, not `FAILED`; they fail to connect and
run nothing). The 3 stub-pool tests pass. But `test_healthz_200` also fails,
with `AssertionError: endpoint returned 503`, *after* its direct probe of the
real database succeeded.

Why: the test's Flask app pool is built from `PHSA_TEST_DSN` (spec section 9:
no test may write to the real database), and the test rebinds that pool to the
(test) DSN before calling the endpoint — so the endpoint's own `SELECT 1` probe
goes at the deliberately broken test DSN and correctly returns 503. That is
the endpoint working *as designed* in response to a broken test DSN, not a
connection failure of the real database. The instruction was to fix the test or
app code rather than the database; making the endpoint ignore a broken DSN (or
the test ignore the endpoint's answer) would change tested behavior, so I have
documented the deviation here instead.

In the normal environment (run A) and with `PHSA_TEST_DSN` unset/empty (run B)
the same test passes. If the expected outcome for run C should instead be
"every test reports the connection-refused error", the resolution is a one-line
change: the healthz test should `pytest.fail` on the endpoint assertion when
`PHSA_TEST_DSN` is set and differs from `PHSA_DSN` — confirm and I will apply it.

**DECLINED by José, 4 Oct 2026. No change. Closed.**

## 2026-10-04 (M1): `test_supersede_demographics_data_layer` fails after the commit-leak fix

Seen on 4 Oct 2026, in the first full verification run after the
commit-no-op fix in `tests/conftest.py` (`_SavepointProxy` + `ROLLBACK TO
SAVEPOINT req`). Both verification runs (the two mandatory full runs)
give the same result: 30 passed, 0 skipped, 1 failed — the no-leak test
(`tests/test_zz_no_leak.py`, last) passes in both runs, so the test
database is left empty; only the one test below fails.

The failing test is `tests/test_demographics.py::test_supersede_demographics_data_layer`
(step 5 of the interrupted M1 pass): `TypeError: 'NoneType' object is
not subscriptable` at the final assertion.

What the test does: creates a subject and two demographics rows
(`first`, `second`); supersedes `first` with a third version — all
uncommitted in the test transaction, no problem; then, still in the same
open transaction, tries to supersede `first` AGAIN. In PostgreSQL this
is a *unique-constraint violation* on
`phsa.subject_demographics.source_record_id` (the `superseded_by`
pointer is a plain FK and allows duplicates, so that part passes):
`supersede_demographics` raises `psycopg.errors.UniqueViolation` when
inserting the new row — its `source_record_id` already belongs to
`second` in the same open transaction — before the pointer UPDATE is
ever reached. The test's docstring states the intended scenario — "the
old row was already superseded; the request becomes HTTP 409" — i.e.
the pointer UPDATE matching 0 rows, which requires the old row to have
been superseded *before* the new transaction began.

The test's comment "409 branch" and the fixture's own
`tx_conn.rollback()` before the final read show the test was *meant* to
cover the 409 branch, but as written it cannot produce it inside one
transaction. The interrupted session apparently stopped right here: the
data layer was never changed, because the data layer is not in the
wrong place — the test is.

The app's 409 branch is exercised and passes in this pass (the HTTP
supersede tests drive it through a prior request that already
superseded the old row — the `SupersedeConflict` path). I did not
change any `app/` file to fix the failing test; the data layer's
contract names only the 409, and making it swallow a
`UniqueViolation` for a scenario its own docs do not name is not a
change I will make on my own judgment. The test stays failing; the
M4 milestone (spec section 9: "superseding a row that was already
superseded returns 409 and writes nothing") is where a proper
data-layer 409 test belongs.
