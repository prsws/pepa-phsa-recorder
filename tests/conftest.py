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
