"""M0: the /healthz endpoint's contract, independent of any database.

/healthz must return 503 when the SELECT 1 cannot be executed (including
when the cleanup rollback on a broken connection itself raises) and 200
when it can. Proven with a stub pool, so it always runs and always passes.
"""


class _StubConn:
    def __init__(self, fail_execute: bool, fail_rollback: bool = False):
        self._fail_execute = fail_execute
        self._fail_rollback = fail_rollback

    def execute(self, _sql):
        if self._fail_execute:
            raise RuntimeError("stub: no database")

    def rollback(self):
        if self._fail_rollback:
            raise RuntimeError("stub: rollback on broken connection")


class _StubPool:
    def __init__(self, fail_execute: bool, fail_rollback: bool = False):
        self._fail_execute = fail_execute
        self._fail_rollback = fail_rollback
        self.put_called_with = None

    def get(self):
        return _StubConn(self._fail_execute, self._fail_rollback)

    def put(self, conn):
        self.put_called_with = conn


def test_healthz_503_when_unreachable(app):
    pool = _StubPool(fail_execute=True)
    app.extensions["phsa_pool"] = pool
    r = app.test_client().get("/healthz")
    assert r.status_code == 503
    assert "503" in r.text


def test_healthz_200_when_reachable(app):
    pool = _StubPool(fail_execute=False)
    app.extensions["phsa_pool"] = pool
    r = app.test_client().get("/healthz")
    assert r.status_code == 200
    assert r.text.strip() == "ok"


def test_healthz_503_when_rollback_raises(app):
    """A broken connection whose rollback() also raises must still yield
    503 (never 500), and the connection must still be handed back."""
    pool = _StubPool(fail_execute=True, fail_rollback=True)
    app.extensions["phsa_pool"] = pool
    r = app.test_client().get("/healthz")
    assert r.status_code == 503
    assert "503" in r.text
    assert pool.put_called_with is not None
