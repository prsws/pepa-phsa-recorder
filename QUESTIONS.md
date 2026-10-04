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
