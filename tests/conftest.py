"""Shared fixtures. Every test runs inside one transaction that is rolled
back at the end; the app role cannot delete anyway, so no row may be left.

Spec section 2: tests skip with a clear message only when PHSA_TEST_DSN is
unset. If the DSN is set and the connection fails, the tests FAIL with the
first line of the error — a broken connection must not be skipped.
"""

import os
import sys

import psycopg
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config, load_config_for_tests  # noqa: E402
from app.db import open_connection  # noqa: E402

# The app speaks to PostgreSQL through libpq (psycopg), which otherwise
# consults the login shell's environment: PG* variables, PGPASSFILE and
# pg_service.conf. For deterministic tests (a "broken" DSN must fail for
# the reason the DSN itself gives), clear those here. The app's own config
# loading (PHSA_*) is unaffected.
for _var in [
    "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGHOSTADDR",
    "PGOPTIONS", "PGREALM",
]:
    os.environ.pop(_var, None)

SKIP_MSG = "PHSA_TEST_DSN is not set; skipping database tests (tests only run against the test database)"


@pytest.fixture
def config():
    """Config pointed at the TEST database (M0 writes nothing to the real one).

    With PHSA_TEST_DSN unset, load_config_for_tests returns a Config whose
    test_dsn is None; the database tests then skip with SKIP_MSG, and the
    app fixture still works with a stub pool.
    """
    return load_config_for_tests()


@pytest.fixture
def app(config):
    from app import create_app

    a = create_app(config)
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(config):
    """A connection to phsa_test, open in a transaction, rolled back after.

    Skips only when PHSA_TEST_DSN is unset (spec section 2). If the DSN is
    set and the connection fails, the test fails with the first line of
    the error.
    """
    if not config.test_dsn:
        pytest.skip(SKIP_MSG)
    c = open_connection(config.test_dsn)  # raises -> the test fails, no skip
    try:
        yield c
    finally:
        c.rollback()
        c.close()


class _SavepointProxy:
    """Wraps the shared test connection so the request's commit/rollback
    cannot leave the test's transaction behind.

    A COMMIT is final: once the request handler commits, the fixture's
    later `conn.rollback()` undoes nothing, and the role cannot delete the
    row — the test database would fill up. So a "request connection" is a
    proxy around the shared connection that:

    - commit() does NOTHING (spec section 9: "commit becomes a no-op");
      nothing reaches the database outside the test's transaction;
    - rollback() runs `ROLLBACK TO SAVEPOINT req`, which undoes exactly
      this request's work and re-arms the savepoint for the next request;
    - every other attribute (execute, fetchone, ...) is passed through
      via `__getattr__` to the real connection.

    The savepoint `req` is created by `_SharedPool.get()`, before the
    proxy is returned. The fixture still does the real `rollback()` (which
    rolls the whole transaction back, undoing every request, because no
    commit ever made anything final) and `close()` at the end.
    """

    def __init__(self, conn):
        self._conn = conn

    def commit(self):
        # Spec section 9: in HTTP tests a commit is a no-op.
        pass

    def rollback(self):
        self._conn.execute("ROLLBACK TO SAVEPOINT req")

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _SharedPool:
    """A stand-in for the app's pool that hands out ONE shared connection.

    Spec section 9: HTTP tests share the test's transaction by overriding
    the connection factory. AppPool is the one connection-factory seam the
    app has (it exposes get()/put() and the conninfo setter); the app code
    itself gains no further test hooks. The shared connection is the one
    opened by the `tx_conn` fixture, so every request in the test runs in
    the test's transaction.

    `get()` opens the savepoint `req` and returns a _SavepointProxy, not
    the raw connection, so the request's commit() is a no-op and its
    rollback() undoes only that request (ROLLBACK TO SAVEPOINT req). The
    fixture's final rollback() rolls everything back; nothing is ever
    committed, and the role could not delete a row anyway.
    """

    def __init__(self, conn):
        self._conn = conn

    def get(self):
        self._conn.execute("SAVEPOINT req")
        return _SavepointProxy(self._conn)

    def put(self, conn):
        pass  # nothing to do: the connection belongs to the test's transaction

    def close(self):
        pass  # nothing to do: the fixture closes the connection


@pytest.fixture
def tx_conn(config, app):
    """The transaction shared by an HTTP test (see _SharedPool).

    Skips only when PHSA_TEST_DSN is unset (spec section 2); a failed
    connection fails the test. The Flask app's pool is replaced by a
    _SharedPool wrapping this connection.
    """
    if not config.test_dsn:
        pytest.skip(SKIP_MSG)
    c = open_connection(config.test_dsn)  # raises -> the test fails, no skip
    app.extensions["phsa_pool"] = _SharedPool(c)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def client_tx(app, tx_conn):
    """A Flask test client whose requests share `tx_conn`'s transaction.

    `tx_conn` must be declared before this fixture so the pool swap happens
    first (fixture order follows declaration order).
    """
    return app.test_client()
