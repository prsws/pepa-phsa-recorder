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

That covers every distinct handler site but one: the demographics
POST-validation path (3) is reached via the invalid POST; the demographics
POST-write path (4) is NOT reached, because a valid demographics POST never
calls the monkeypatched get_subject first — it reaches the database and the
failed write aborts the shared connection (see the note in QUESTIONS.md).

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
