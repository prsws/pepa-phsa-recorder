"""M4a: supersede a condition (spec 4.2, 6; the first step of M4).

Each HTTP test creates its own subject and condition through the app
(routes) and shares the test's transaction through the overridden
connection factory (spec section 9; `client_tx` in conftest), then rolls
back at the end; no test commits, no test may leave a row behind.

Covered:

- GET the form for a current condition: 200 with the current values; a
  random id: 404
- a valid POST: one more source_record, one more condition row; the old
  row's superseded_by equals the new row's id; the new row has the same
  subject_id; phsa.current_condition contains the new row and not the old;
  the response redirects to the subject page
- the new version's stored document: resource_raw.id equals
  source_resource_id; meta.security contains HTEST; content_sha256 equals
  sha256(convert_to(resource_raw::text, 'UTF8')) recomputed in a query
- an invalid POST (blank code_text): 422, the user's other input kept, row
  counts unchanged
- a POST with both onset_text and onset_date: 422, row counts unchanged
- supersede once, then POST again to the OLD id: 409, row counts unchanged
  by the second POST; then GET the old id: 409
- with app.queries.create_condition monkeypatched to raise
  psycopg.OperationalError("boom") before any SQL: a valid POST gives 500,
  the plain message, no "boom"/"psycopg"/"Traceback" in the body, row
  counts unchanged
- the pool's get/put balance on every way out of the route (the same
  counting technique as tests/test_error_pages.py::test_pool_put_matches_get)
- the data layer: supersede_condition raises SupersedeConflict when the old
  row is already superseded
"""

import json
import re
import uuid

import psycopg
import pytest

from app import queries


CONDITION_URL = "/conditions/{cid}/supersede"
SUBJECT_URL = "/subjects/{sid}"
ALREADY_SUPERSEDED = "this version has already been superseded"
PLAIN_PAGE_NOSAVE = "something went wrong; nothing was saved"
FORBIDDEN_STRINGS = ("boom", "psycopg", "Traceback")

SUBJECT_DATA = {"label": "Supersede tester", "fixture_id": ""}
COND_A = {"code_text": "Asthma", "code_system": "http://www.oma.org",
          "code": "260.7", "clinical_status": "active",
          "verification_status": "confirmed", "onset_text": "childhood",
          "onset_date": "", "note": "first recorded"}
COND_B = {"code_text": "Asthma, reviewed", "code_system": "", "code": "",
          "clinical_status": "inactive",
          "verification_status": "confirmed", "onset_text": "",
          "onset_date": "2020-03-04", "note": "second recorded"}
ORIGIN = {"Origin": "http://localhost"}


def _make_condition(client_tx, tx_conn, subject_id, data, expect=302):
    """Create one condition through the app (a real request), return the
    condition's id (the test's transaction is undone by the fixture).

    The id comes from the database, not from the redirect target: the create
    route redirects to /subjects/<subject_id>, whose last path part is the
    SUBJECT's id, not the condition's. The condition is the newest
    non-superseded row for that subject (ids are uuidv7, so the newest
    sorts last).
    """
    r = client_tx.post(f"/subjects/{subject_id}/conditions/new",
                       data=data, headers=ORIGIN)
    assert r.status_code == expect, f"expected {expect}, got {r.status_code}"
    row = tx_conn.execute(
        "SELECT id FROM phsa.condition "
        "WHERE subject_id = %s AND superseded_by IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (subject_id,),
    ).fetchone()
    return str(row["id"])


def _shared_tx_state(tx_conn):
    """The shared connection's transaction state, as a plain string. The
    fixture's connection is opened with autocommit off, so it sits INTRANS
    between statements. A request that finished must leave the connection
    in the state it found it: `abort` means the request left an
    un-rolled-back error in place (every following request on that
    connection would fail), `no-transaction` means the request committed
    (a commit on a shared connection would finalise rows the fixture must
    roll back)."""
    return tx_conn.info.transaction_status.name.lower()


def _count(table: str, tx_conn) -> int:
    return tx_conn.execute(
        f"SELECT count(*) AS n FROM phsa.{table}"
    ).fetchone()["n"]


def _counts(tx_conn) -> dict:
    return {t: _count(t, tx_conn) for t in ("subject", "condition",
                                            "source_record")}


def _raw(src):
    raw = src["resource_raw"]
    if isinstance(raw, str):  # some rows come back as text
        raw = json.loads(raw)
    return raw


def test_get_form_for_current_condition(client_tx, tx_conn):
    """GET the form for a current condition: 200 and the current values are
    in the page; the form posts to the supersede route in supersede mode."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    cid = _make_condition(client_tx, tx_conn, sid, COND_A)
    r = client_tx.get(CONDITION_URL.format(cid=cid))
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    body = r.text
    assert 'value="Asthma"' in body
    assert 'value="http://www.oma.org"' in body
    assert 'value="260.7"' in body
    assert 'value="childhood"' in body
    assert "first recorded" in body
    assert "active" in body
    assert "New version of an existing condition" in body
    assert 'action="/conditions/%s/supersede"' % cid in body
    assert "Supersede (new version)" in body


def test_get_form_random_id_is_404(client_tx, tx_conn):
    queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    r = client_tx.get(CONDITION_URL.format(cid=uuid.uuid4()))
    assert r.status_code == 404, f"expected 404, got {r.status_code}"


def test_valid_post_supersedes(client_tx, tx_conn):
    """A valid POST: one more source_record and one more condition row; the
    old row's superseded_by equals the new row's id; the new row has the
    same subject_id; phsa.current_condition contains the new row and not the
    old one; the response redirects to the subject page."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_condition(client_tx, tx_conn, sid, COND_A)
    before = _counts(tx_conn)

    r = client_tx.post(CONDITION_URL.format(cid=old_id), data=COND_B,
                       headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    assert r.headers["Location"].endswith(SUBJECT_URL.format(sid=sid))

    after = _counts(tx_conn)
    assert after["condition"] == before["condition"] + 1
    assert after["source_record"] == before["source_record"] + 1

    old_row = tx_conn.execute(
        "SELECT id, subject_id, superseded_by FROM phsa.condition WHERE id = %s",
        (old_id,),
    ).fetchone()
    # a successful request must leave the shared connection in the state it
    # found it (the fixture opened it in-transaction; an un-rolled-back error
    # would fail every following request and the next test)
    assert _shared_tx_state(tx_conn) == "intrans"

    assert old_row["superseded_by"] is not None
    new_row = tx_conn.execute(
        "SELECT id, subject_id, superseded_by FROM phsa.condition WHERE id = %s",
        (old_row["superseded_by"],),
    ).fetchone()

    assert new_row is not None
    assert old_row["superseded_by"] == new_row["id"]
    assert new_row["subject_id"] == sid
    assert new_row["superseded_by"] is None

    current = tx_conn.execute(
        "SELECT id FROM phsa.current_condition WHERE id = %s",
        (new_row["id"],),
    ).fetchone()
    current_old = tx_conn.execute(
        "SELECT id FROM phsa.current_condition WHERE id = %s",
        (old_id,),
    ).fetchone()
    assert current is not None  # the new version is current
    assert current_old is None  # the old version is no longer current


def test_valid_post_stored_document(client_tx, tx_conn):
    """The new version's stored document: resource_raw.id equals
    source_resource_id; meta.security contains HTEST; content_sha256 equals
    sha256(convert_to(resource_raw::text, 'UTF8')) recomputed in a query."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_condition(client_tx, tx_conn, sid, COND_A)
    r = client_tx.post(CONDITION_URL.format(cid=old_id), data=COND_B,
                       headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"

    new_row = tx_conn.execute(
        "SELECT source_record_id FROM phsa.condition "
        "WHERE superseded_by IS NULL AND subject_id = %s "
        "ORDER BY id DESC LIMIT 1", (sid,),
    ).fetchone()
    src = tx_conn.execute(
        """SELECT id, source_resource_id, resource_raw, content_sha256
           FROM phsa.source_record WHERE id = %s""",
        (new_row["source_record_id"],),
    ).fetchone()
    raw = _raw(src)
    assert raw["id"] == src["source_resource_id"]
    assert "HTEST" in [tag["code"] for tag in raw["meta"]["security"]]
    recomputed = tx_conn.execute(
        """SELECT sha256(convert_to(resource_raw::text, 'UTF8')) AS h
           FROM phsa.source_record WHERE id = %s""",
        (src["id"],),
    ).fetchone()["h"]
    assert src["content_sha256"] == recomputed


def test_invalid_post_blank_code_text_is_422_and_writes_nothing(
        client_tx, tx_conn):
    """An invalid POST (blank code_text): 422, the user's other input is in
    the page, row counts unchanged."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    cid = _make_condition(client_tx, tx_conn, sid, COND_A)
    before = _counts(tx_conn)

    data = dict(COND_B)
    data["code_text"] = ""
    r = client_tx.post(CONDITION_URL.format(cid=cid), data=data,
                       headers=ORIGIN)
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    # a validation-failed request must leave the shared connection in the
    # state it found it (see _shared_tx_state)
    assert _shared_tx_state(tx_conn) == "intrans"
    # the user's other input is kept in the re-rendered form
    assert "second recorded" in r.text
    assert "2020-03-04" in r.text
    assert "first recorded" not in r.text  # the form shows the post, not the row
    assert "code_text is required" in r.text
    assert _counts(tx_conn) == before


def test_invalid_post_both_onsets_is_422_and_writes_nothing(client_tx, tx_conn):
    """A POST with both onset_text and onset_date: 422, row counts
    unchanged."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    cid = _make_condition(client_tx, tx_conn, sid, COND_A)
    before = _counts(tx_conn)

    data = dict(COND_B)
    data["onset_text"] = "childhood"  # now both onset fields are given
    r = client_tx.post(CONDITION_URL.format(cid=cid), data=data,
                       headers=ORIGIN)
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    assert "not both" in r.text
    assert _counts(tx_conn) == before


def test_supersede_twice_second_post_is_409_and_get_is_409(client_tx, tx_conn):
    """Supersede a condition once, then POST again to the OLD id: 409, row
    counts unchanged by the second POST. Then GET the old id: 409."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_condition(client_tx, tx_conn, sid, COND_A)

    r = client_tx.post(CONDITION_URL.format(cid=old_id), data=COND_B,
                       headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    after_first = _counts(tx_conn)

    r = client_tx.post(CONDITION_URL.format(cid=old_id), data=COND_B,
                       headers=ORIGIN)
    assert r.status_code == 409, f"expected 409, got {r.status_code}"
    assert ALREADY_SUPERSEDED in r.text
    assert _counts(tx_conn) == after_first  # the second POST wrote nothing

    r = client_tx.get(CONDITION_URL.format(cid=old_id))
    assert r.status_code == 409, f"expected 409, got {r.status_code}"
    assert ALREADY_SUPERSEDED in r.text


def test_boom_post_is_500_and_writes_nothing(client_tx, tx_conn):
    """With app.queries.create_condition monkeypatched to raise
    psycopg.OperationalError('boom') before any SQL: a valid POST gives 500,
    the plain message, none of 'boom'/'psycopg'/'Traceback' in the body,
    and row counts unchanged."""
    monkeypatch = pytest.MonkeyPatch()
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    cid = _make_condition(client_tx, tx_conn, sid, COND_A)
    before = _counts(tx_conn)
    # only the SUPERSEDE request below runs with create_condition patched:
    # the patch must not cover the setup request above (it calls the same
    # function and would otherwise fail the setup)
    monkeypatch.setattr(
        queries, "create_condition",
        lambda conn, sid, d: (_ for _ in ()).throw(
            psycopg.OperationalError("boom")))
    try:
        r = client_tx.post(CONDITION_URL.format(cid=cid), data=COND_B,
                           headers=ORIGIN)
    finally:
        monkeypatch.undo()
    assert r.status_code == 500, f"expected 500, got {r.status_code}"
    assert PLAIN_PAGE_NOSAVE in r.text
    for needle in FORBIDDEN_STRINGS:
        assert needle not in r.text, f"error page leaks {needle!r}"
    assert _counts(tx_conn) == before


def test_pool_put_matches_get(client_tx, tx_conn):
    """The pool's get and put must balance on every way out of the route,
    using the same counting technique as the pool test in
    tests/test_error_pages.py. For each of the six requests below: get was
    called at least once and put calls equal get calls."""
    pool = client_tx.application.extensions["phsa_pool"]
    real_get, real_put = pool.get, pool.put
    calls = {"get": 0, "put": 0}

    def counting_get():
        calls["get"] += 1
        return real_get()

    def counting_put(conn):
        calls["put"] += 1
        return real_put(conn)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pool, "get", counting_get)
    monkeypatch.setattr(pool, "put", counting_put)
    try:
        sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
        cid = _make_condition(client_tx, tx_conn, sid, COND_A)
        # the boom part of this test needs its own fresh condition; it is
        # created before create_condition is patched (the patch must cover
        # only the supersede request, not this setup request)
        second = _make_condition(client_tx, tx_conn, sid, COND_B)

        def _req(name, expected, fn):
            calls["get"] = 0
            calls["put"] = 0
            r = fn()
            assert r.status_code == expected, \
                f"{name}: expected {expected}, got {r.status_code}"
            assert calls["get"] >= 1, f"{name}: get never called"
            assert calls["put"] == calls["get"], \
                f"{name}: put ({calls['put']}) != get ({calls['get']})"

        # the GETs and the invalid POST hit the (still current) condition;
        # the invalid one comes BEFORE the supersede so it still renders 422
        _req("GET 200", 200, lambda: client_tx.get(CONDITION_URL.format(cid=cid)))
        _req("GET 404", 404, lambda: client_tx.get(
            CONDITION_URL.format(cid=uuid.uuid4())))
        _req("invalid POST 422", 422, lambda: client_tx.post(
            CONDITION_URL.format(cid=cid),
            data={**COND_B, "code_text": ""}, headers=ORIGIN))
        # now supersede it for real
        _req("valid POST 302", 302, lambda: client_tx.post(
            CONDITION_URL.format(cid=cid), data=COND_B, headers=ORIGIN))
        # the condition that was just superseded answers 409 (the second
        # condition made above stays current throughout)
        _req("already-superseded POST 409", 409, lambda: client_tx.post(
            CONDITION_URL.format(cid=cid), data=COND_B, headers=ORIGIN))

        # the boom POST (500): a fresh condition, create_condition patched to
        # raise before any SQL
        monkeypatch.setattr(
            queries, "create_condition",
            lambda conn, sid, d: (_ for _ in ()).throw(
                psycopg.OperationalError("boom")))
        _req("boom POST 500", 500, lambda: client_tx.post(
            CONDITION_URL.format(cid=second), data=COND_B, headers=ORIGIN))
    finally:
        monkeypatch.undo()


def test_supersede_condition_data_layer_raises_when_already_superseded(conn):
    """The data layer: supersede_condition raises SupersedeConflict when the
    old row is already superseded (the single permitted UPDATE then matches
    0 rows)."""
    sid = queries.create_subject(conn, "Don Efra", None)
    old_row = queries.create_condition(conn, sid, {
        "code_text": "Type 2 diabetes", "code_system": None, "code": None,
        "clinical_status": "active", "verification_status": None,
        "onset_text": None, "onset_date": None, "note": None,
    })
    other_row = queries.create_condition(conn, sid, {
        "code_text": "Hypertension", "code_system": None, "code": None,
        "clinical_status": "active", "verification_status": None,
        "onset_text": None, "onset_date": None, "note": None,
    })
    # the old row is already superseded (pointed at the other row)
    cur = conn.execute(
        "UPDATE phsa.condition SET superseded_by = %s "
        "WHERE id = %s AND superseded_by IS NULL",
        (other_row["id"], old_row["id"]),
    )
    assert cur.rowcount == 1
    with pytest.raises(queries.SupersedeConflict):
        queries.supersede_condition(conn, old_row["id"], sid, {
            "code_text": "Type 2 diabetes", "code_system": None, "code": None,
            "clinical_status": "active", "verification_status": None,
            "onset_text": None, "onset_date": None, "note": None,
        })
    # the test's transaction is undone by the fixture (and on the next
    # request through the savepoint proxy); nothing needs to be undone here
