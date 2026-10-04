"""M0: the /healthz endpoint's contract, independent of any database.

/healthz must return 503 when the SELECT 1 cannot be executed and 200 when
it can. Proven with a stub pool, so it always runs and always passes.
"""

from app.db import AppPool


class _StubConn:
    def __init__(self, fail: bool):
        self._fail = fail

    def execute(self, _sql):
        if self._fail:
            raise RuntimeError("stub: no database")

    def rollback(self):
        pass


class _StubPool:
    def __init__(self, fail: bool):
        self._fail = fail

    def get(self):
        return _StubConn(self._fail)

    def put(self, _conn):
        pass


def test_healthz_503_when_unreachable(app):
    app.extensions["phsa_pool"] = _StubPool(fail=True)
    r = app.test_client().get("/healthz")
    assert r.status_code == 503
    assert "503" in r.text


def test_healthz_200_when_reachable(app):
    app.extensions["phsa_pool"] = _StubPool(fail=False)
    r = app.test_client().get("/healthz")
    assert r.status_code == 200
    assert r.text.strip() == "ok"
