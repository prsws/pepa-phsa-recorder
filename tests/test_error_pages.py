"""Error-page behaviour (spec 6.1: an unexpected database error gives HTTP
500, a plain error page with no SQL and no connection details, and a
server-side log line without the password).

With `app.queries.get_subject` monkeypatched to raise
psycopg.OperationalError("boom") BEFORE any SQL runs, every request below
fails in one of the nine `except` handlers in app/routes.py and must return
the plain 500 page: the route's message, never the exception's text
("boom"), its type ("psycopg"), or a traceback. The 404 path is untouched:
a genuinely missing subject is still a 404, never a 500.

Requests issued (and which handler they reach):

- GET /subjects/<id>                          — subject overview
- GET /subjects/<id>/demographics             — demographics form
- POST /subjects/<id>/demographics (invalid)  — demographics validation re-render
- GET /subjects/<id>/conditions/new           — condition form
- POST /subjects/<id>/conditions/new (invalid) — condition validation re-render
- POST /subjects/<id>/conditions/new (valid)  — condition write
- GET /subjects/<id>/medications/new          — medication form
- POST /subjects/<id>/medications/new (invalid) — medication validation re-render
- POST /subjects/<id>/medications/new (valid) — medication write

That covers every distinct handler site: the demographics POST-validation
path (3) is reached via the invalid POST; the demographics POST-write path
(4) is reached via test_demographics_form_post_valid_invalid_write, which
patches create_demographics instead (after the Pre-M4-fix the valid path
looks the subject up first, so a random id is a 404, not a 500 — that is
covered by test_demographics_valid_post_missing_subject).

The POSTs send the same Origin header the existing tests send. The tests
share the tx connection (spec section 9) and write nothing: the monkeypatch
raises before any SQL, so no row may be created; the no-leak test proves
it.
"""

import uuid

import psycopg
import pytest

from app import queries


PLAIN_PAGE = "something went wrong"
PLAIN_PAGE_NOSAVE = "something went wrong; nothing was saved"
FORBIDDEN_STRINGS = ("boom", "psycopg", "Traceback")
NO_SUBJECT = "no such subject"


@pytest.fixture
def boom(client_tx):
    """Make queries.get_subject raise psycopg.OperationalError("boom")
    before it runs any SQL, for the duration of the test. Every request in
    this module fails in the route's exception handler before any SQL runs
    on the test's connection."""
    def _boom(conn, subject_id):
        raise psycopg.OperationalError("boom")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(queries, "get_subject", _boom)
    try:
        yield _boom
    finally:
        monkeypatch.undo()


def _counts(tx_conn) -> dict:
    out = {}
    for table in ("subject", "subject_demographics", "condition",
                  "medication_request", "source_record"):
        out[table] = tx_conn.execute(
            f"SELECT count(*) AS n FROM phsa.{table}"
        ).fetchone()["n"]
    return out


def _assert_error_page(r, expected_message, tx_conn, before, body):
    assert r.status_code == 500, f"expected 500, got {r.status_code}"
    assert expected_message in body
    for needle in FORBIDDEN_STRINGS:
        assert needle not in body, f"error page leaks {needle!r}"
    # nothing was written: counts equal before and after.
    after = _counts(tx_conn)
    assert after == before


def test_subject_overview_get(client_tx, tx_conn, boom):
    before = _counts(tx_conn)
    r = client_tx.get(f"/subjects/{uuid.uuid4()}")
    _assert_error_page(r, PLAIN_PAGE, tx_conn, before, r.text)


def test_demographics_form_get(client_tx, tx_conn, boom):
    before = _counts(tx_conn)
    r = client_tx.get(f"/subjects/{uuid.uuid4()}/demographics")
    _assert_error_page(r, PLAIN_PAGE, tx_conn, before, r.text)


def test_demographics_form_post_invalid(client_tx, tx_conn, boom):
    """An invalid form (birth_year "abc") fails validation; reaching the
    422 re-render needs the subject lookup, which is the poisoned one — so
    this still 500s with the form path's plain message."""
    before = _counts(tx_conn)
    r = client_tx.post(
        f"/subjects/{uuid.uuid4()}/demographics",
        data={"family_name": "Efra", "given_name": "Don", "usual_name": "",
              "sex": "male", "birth_year": "abc", "birth_date": "",
              "home_municipality": "", "preferred_language": ""},
        headers={"Origin": "http://localhost"},
    )
    _assert_error_page(r, PLAIN_PAGE, tx_conn, before, r.text)


def test_condition_form_get(client_tx, tx_conn, boom):
    before = _counts(tx_conn)
    r = client_tx.get(f"/subjects/{uuid.uuid4()}/conditions/new")
    _assert_error_page(r, PLAIN_PAGE, tx_conn, before, r.text)


def test_condition_form_post_invalid(client_tx, tx_conn, boom):
    """An invalid form re-renders with 422; reaching the re-render needs the
    subject lookup, which is the poisoned one — so this still 500s."""
    before = _counts(tx_conn)
    r = client_tx.post(
        f"/subjects/{uuid.uuid4()}/conditions/new",
        data={"code_text": "", "code_system": "", "code": "",
              "clinical_status": "active", "verification_status": "",
              "onset_text": "", "onset_date": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    _assert_error_page(r, PLAIN_PAGE, tx_conn, before, r.text)


def test_condition_form_post_valid(client_tx, tx_conn, boom):
    before = _counts(tx_conn)
    r = client_tx.post(
        f"/subjects/{uuid.uuid4()}/conditions/new",
        data={"code_text": "Asthma", "code_system": "http://www.oma.org",
              "code": "260.7", "clinical_status": "active",
              "verification_status": "confirmed", "onset_text": "childhood",
              "onset_date": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    _assert_error_page(r, PLAIN_PAGE_NOSAVE, tx_conn, before, r.text)


def test_medication_form_get(client_tx, tx_conn, boom):
    before = _counts(tx_conn)
    r = client_tx.get(f"/subjects/{uuid.uuid4()}/medications/new")
    _assert_error_page(r, PLAIN_PAGE, tx_conn, before, r.text)


def test_medication_form_post_invalid(client_tx, tx_conn, boom):
    before = _counts(tx_conn)
    r = client_tx.post(
        f"/subjects/{uuid.uuid4()}/medications/new",
        data={"medication_text": "", "medication_code_system": "",
              "medication_code": "", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    _assert_error_page(r, PLAIN_PAGE, tx_conn, before, r.text)


def test_medication_form_post_valid(client_tx, tx_conn, boom):
    before = _counts(tx_conn)
    r = client_tx.post(
        f"/subjects/{uuid.uuid4()}/medications/new",
        data={"medication_text": "Metformin 500 mg",
              "medication_code_system": "", "medication_code": "",
              "status": "active", "authored_on": "",
              "sig_text": "500 mg BID with meals",
              "dose_value": "500", "dose_unit": "mg", "dose_unit_code": "mg",
              "route_text": "", "frequency": "2", "period": "1",
              "period_unit": "d", "timing_when": "MORN", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    _assert_error_page(r, PLAIN_PAGE_NOSAVE, tx_conn, before, r.text)


def test_demographics_valid_post_missing_subject(client_tx, tx_conn):
    """Pre-M4-fix, FIX A: a valid demographics POST for a subject id that
    does not exist must answer 404 (the valid path now looks the subject up
    first, like the condition and medication routes already did), and
    nothing may be written: row counts equal before and after. No patching
    in this test — the missing subject is a real 404 path."""
    before = _counts(tx_conn)
    r = client_tx.post(
        f"/subjects/{uuid.uuid4()}/demographics",
        data={"family_name": "Efra", "given_name": "Don", "usual_name": "",
              "sex": "male", "birth_year": "", "birth_date": "",
              "home_municipality": "", "preferred_language": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}"
    assert NO_SUBJECT in r.text
    after = _counts(tx_conn)
    assert after == before


def test_demographics_valid_post_write_booms(client_tx, tx_conn):
    """Pre-M4-fix, FIX B: with create_demographics raising
    psycopg.OperationalError("boom") before it runs any SQL, a valid
    demographics POST for a real subject answers the plain 500 page (never
    "boom"/"psycopg"/"Traceback") and writes nothing — the connection is
    still finished (rolled back) on this way out."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        queries, "create_demographics",
        lambda conn, sid, d: (_ for _ in ()).throw(
            psycopg.OperationalError("boom")))
    try:
        subject_id = queries.create_subject(tx_conn, "Boom person", None)
        before = _counts(tx_conn)
        r = client_tx.post(
            f"/subjects/{subject_id}/demographics",
            data={"family_name": "García", "given_name": "Rosa", "usual_name": "",
                  "sex": "female", "birth_year": "", "birth_date": "",
                  "home_municipality": "Caguas", "preferred_language": "es"},
            headers={"Origin": "http://localhost"},
        )
    finally:
        monkeypatch.undo()
    assert r.status_code == 500, f"expected 500, got {r.status_code}"
    assert PLAIN_PAGE_NOSAVE in r.text
    for needle in FORBIDDEN_STRINGS:
        assert needle not in r.text, f"error page leaks {needle!r}"
    after = _counts(tx_conn)
    assert after == before  # in particular, no subject_demographics row


def test_pool_put_matches_get(client_tx, tx_conn):
    """Pre-M4-fix, FIX B: the pool's get and put must balance on every way
    out of the four write blocks. For each of the six requests — a valid
    POST for a subject that does not exist (404) and a valid POST for a
    real subject whose form's create function is patched to raise
    psycopg.OperationalError("boom") before any SQL (500), on each of the
    demographics, condition and medication forms — get is called at least
    once and the number of put calls equals the number of get calls (a
    missing put is a connection never returned to the pool)."""
    monkeypatch = pytest.MonkeyPatch()
    pool = client_tx.application.extensions["phsa_pool"]
    real_get, real_put = pool.get, pool.put
    calls = {"get": 0, "put": 0}

    def counting_get():
        calls["get"] += 1
        return real_get()

    def counting_put(conn):
        calls["put"] += 1
        return real_put(conn)

    monkeypatch.setattr(pool, "get", counting_get)
    monkeypatch.setattr(pool, "put", counting_put)
    try:
        # valid form payloads, the same ones the existing create tests send
        demo_data = {"family_name": "García", "given_name": "Rosa",
                     "usual_name": "", "sex": "female", "birth_year": "",
                     "birth_date": "", "home_municipality": "Caguas",
                     "preferred_language": "es"}
        cond_data = {"code_text": "Asthma", "code_system": "http://www.oma.org",
                     "code": "260.7", "clinical_status": "active",
                     "verification_status": "confirmed", "onset_text": "childhood",
                     "onset_date": "", "note": ""}
        med_data = {"medication_text": "Metformin 500 mg",
                    "medication_code_system": "", "medication_code": "",
                    "status": "active", "authored_on": "",
                    "sig_text": "500 mg BID with meals",
                    "dose_value": "500", "dose_unit": "mg", "dose_unit_code": "mg",
                    "route_text": "", "frequency": "2", "period": "1",
                    "period_unit": "d", "timing_when": "MORN", "note": ""}
        routes = [
            ("demographics", "/demographics",
             "create_demographics", demo_data),
            ("condition", "/conditions/new",
             "create_condition", cond_data),
            ("medication", "/medications/new",
             "create_medication_request", med_data),
        ]
        for name, path, create_fn, data in routes:
            # 1) valid POST for a subject that does not exist -> 404
            calls["get"] = 0
            calls["put"] = 0
            r = client_tx.post(f"/subjects/{uuid.uuid4()}{path}", data=data,
                               headers={"Origin": "http://localhost"})
            assert r.status_code == 404, f"{name} missing subject: {r.status_code}"
            assert calls["get"] >= 1, f"{name}: get never called"
            assert calls["put"] == calls["get"], \
                f"{name} 404 path: put ({calls['put']}) != get ({calls['get']})"
            # 2) valid POST for a real subject, create raises before any SQL -> 500
            sid = queries.create_subject(tx_conn, f"Pool balance {name} person", None)
            monkeypatch.setattr(
                queries, create_fn,
                lambda conn, sid, d: (_ for _ in ()).throw(
                    psycopg.OperationalError("boom")))
            calls["get"] = 0
            calls["put"] = 0
            r = client_tx.post(f"/subjects/{sid}{path}", data=data,
                               headers={"Origin": "http://localhost"})
            assert r.status_code == 500, f"{name} boom: {r.status_code}"
            assert calls["get"] >= 1, f"{name}: get never called"
            assert calls["put"] == calls["get"], \
                f"{name} 500 path: put ({calls['put']}) != get ({calls['get']})"
    finally:
        monkeypatch.undo()
