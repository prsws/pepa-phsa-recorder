"""Database access.

Plain parameterised SQL via psycopg 3. Every table and view is
schema-qualified (phsa.x) even though the role's search_path is set
(server-side, spec section 3) — belt and braces.

Rules the code lives by (spec sections 0 and 3):
- The role phsa_app cannot DELETE/TRUNCATE, and can UPDATE only
  superseded_by. Normal use must never trigger the immutability triggers.
- M0 does nothing to the real database except SELECT 1 in /healthz.
- Data-layer functions take a connection and never commit; the request
  layer commits (later milestones).
"""

import logging

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("phsa")


def open_connection(dsn: str):
    """One autocommit=False connection; schema-qualified regardless of role."""
    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
    conn.execute("SET search_path = phsa, public")
    return conn


class AppPool:
    """A psycopg_pool wrapper: pooled, lazily opened, schema-qualified.

    Tests use open_connection() against PHSA_TEST_DSN and roll back; the
    pool here serves the app process (M0: only /healthz).
    """

    def __init__(self, dsn: str):
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"row_factory": dict_row},
            # configure may not leave the connection in a transaction
            # (psycopg_pool discards such connections); search_path is
            # set per session in the server role definition instead.
        )

    def get(self):
        """A pooled connection in an open transaction (autocommit off)."""
        if self._pool.closed:
            self._pool.open(wait=10)
        return self._pool.getconn()

    def put(self, conn) -> None:
        self._pool.putconn(conn)

    def close(self) -> None:
        self._pool.close()
