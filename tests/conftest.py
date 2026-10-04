"""Shared fixtures. Every test runs inside one transaction that is rolled
back at the end; the app role cannot delete anyway, so no row may be left.

If PHSA_TEST_DSN is not set, the database tests skip with a clear message.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config, load_config_for_tests  # noqa: E402
from app.db import open_connection  # noqa: E402

SKIP_MSG = "PHSA_TEST_DSN is not set; skipping database tests (tests only run against the test database)"


@pytest.fixture
def config():
    """Config pointed at the TEST database (M0 writes nothing to the real one)."""
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
    """A connection to phsa_test, open in a transaction, rolled back after."""
    if not config.test_dsn:
        pytest.skip(SKIP_MSG)
    try:
        c = open_connection(config.test_dsn)
    except Exception as e:
        first = (str(e).splitlines() or [type(e).__name__])[0]
        pytest.skip(f"cannot connect to PHSA_TEST_DSN: {first}; see QUESTIONS.md")
    try:
        yield c
    finally:
        c.rollback()
        c.close()
