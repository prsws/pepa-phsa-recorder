# QUESTIONS.md

## 2026-10-04 (M0): `FATAL: password authentication failed for user "phsa_app"` on both DSNs

When building M0, every connection attempt with the role `phsa_app` fails
authentication on host 192.168.10.18, port 5432:

```
FATAL:  password authentication failed for user "phsa_app"
```

This happens for both `phsa` and `phsa_test`, using `psycopg` 3.3.6 (pure-Python
driver, libpq not installed) against the DSNs from `.env`, with the password
supplied from the DSN. A control connect with the same host/port/user and **no**
password fails differently, as expected (`fe_sendauth: no password supplied`),
so the server is reachable and is the one from spec section 2; the password in
`.env` is simply not being accepted for this role.

Impact: the M0 grant tests (spec section 9: DELETE/TRUNCATE denied, UPDATE of
non-`superseded_by` denied, `superseded_by` update permitted, INSERT into
`record_confirmation` denied) and the `/healthz` 200 test cannot be executed
until the server accepts the password. The test code is written and in place;
it will run as soon as authentication works.

Per spec section 3 ("if the connection fails, write the exact error in
QUESTIONS.md and stop") I have stopped here rather than touching `pg_hba.conf`,
changing credentials, or otherwise working around it. Presumed cause: the
`.env` was written for a different/older password, or the role's password on the
server changed. José: please re-confirm the `phsa_app` password (or the DSNs in
`.env`), then the tests can be run.
