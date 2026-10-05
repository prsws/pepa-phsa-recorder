"""Runs LAST (the filename sorts after every other test_*.py in tests/):
proof that no test left a row behind.

The test database must be empty. The app role cannot DELETE or TRUNCATE
(spec section 3), so a row leaked by an earlier test could never be removed
in a later one — this test opens a NEW connection to PHSA_TEST_DSN (a
transaction that is rolled back, writing nothing) and asserts that the
tables the M1/M2 tests write to hold 0 rows each.

Every connection in this check is read-only, so it is safe to run after
any other test in the session.
"""

import os

import psycopg
import pytest

from app.config import load_config_for_tests
from app.db import open_connection


def test_no_rows_left_behind():
    config = load_config_for_tests()
    if not config.test_dsn:
        pytest.skip(
            "PHSA_TEST_DSN is not set; skipping database tests "
            "(tests only run against the test database)"
        )

    conn = open_connection(config.test_dsn)  # raises -> the test fails, no skip
    try:
        for table in ("source_record", "subject", "subject_demographics",
                      "condition"):
            n = conn.execute(
                f"SELECT count(*) AS n FROM phsa.{table}"
            ).fetchone()["n"]
            assert n == 0, f"phsa.{table} still holds {n} row(s); a test leaked data"
    finally:
        conn.rollback()
        conn.close()
