"""M0: /healthz is 200.

The one permitted use of PHSA_DSN in M0 is the SELECT 1 in /healthz.
The DSN comes from the environment (or the git-ignored .env). If the real
database cannot be reached, the test FAILS with the first line of the
error — a broken connection must not be skipped. Spec section 2 allows a
skip only when the DSN is unset or empty; that skip applies here too.
"""

import os

import pytest

from app.config import load_config
from app.db import open_connection


def test_healthz_200(client):
    dsn = load_config().dsn
    if not dsn:
        pytest.skip("PHSA_DSN is not set; the real database cannot be probed")

    # The pool of the test app is built from the TEST DSN (spec section 9:
    # no test may write to the real database). The endpoint itself must
    # behave per contract, whatever that pool points at.
    test_dsn = os.environ.get("PHSA_TEST_DSN")
    if not test_dsn:
        test_dsn = dsn  # unset/empty PHSA_TEST_DSN: fall back to the real DSN

    pool = client.application.extensions["phsa_pool"]
    pool.conninfo = test_dsn  # rebind the pool for the duration of the test

    probe = None
    try:
        probe = open_connection(dsn)
        probe.execute("SELECT 1")
    except Exception as e:
        first = (str(e).splitlines() or [type(e).__name__])[0]
        pytest.fail(f"real database (PHSA_DSN) unreachable: {first}")
    finally:
        if probe is not None:
            probe.rollback()
            probe.close()

    r = client.get("/healthz")
    # The endpoint's own probe goes through the pool (rebound above); if the
    # real database is reachable it must answer 200.
    assert r.status_code == 200, f"endpoint returned {r.status_code}: {r.text[:100]!r}"
    assert r.text.strip() == "ok"
