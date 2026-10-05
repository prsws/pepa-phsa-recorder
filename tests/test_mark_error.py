"""M4c: mark a condition or a medication request entered-in-error (spec 4.3).

Marking a record wrong is a supersede, not a fourth verb: a new version is
copied from the current one with exactly one value changed (the
condition's verification_status / the medication request's status becomes
'entered-in-error'), every other value is kept exactly, and the old row is
pointed at the new one.

Each HTTP test creates its own subject and record through the app (routes)
and shares the test's transaction through the overridden connection factory
(spec section 9; `client_tx` in conftest), then rolls back at the end; no
test commits, no test may leave a row behind.

Covered, for both record types:

- a POST on a record with every field filled: redirect to the subject page;
  one more source_record row and one more typed-row row; the old row's
  superseded_by equals the new row's id; the new row has the
  entered-in-error value; every other column of the new row equals the old
  row's column by column
- the new version's stored document: resource_raw.id equals
  source_resource_id; meta.security contains HTEST; content_sha256 equals
  sha256(convert_to(resource_raw::text, 'UTF8')) recomputed in a query; the
  document carries the entered-in-error value
- POST for a random id: 404, row counts unchanged
- supersede first, then mark-error the OLD id: 409, row counts unchanged
- mark-error twice: the second POST (to the NEW id) gives 409 with the
  "already marked" text, row counts unchanged
- GET the mark-error address: 405
- the matching create function monkeypatched to raise
  psycopg.OperationalError("boom") before any SQL: 500, the plain message,
  none of "boom"/"psycopg"/"Traceback" in the body, row counts unchanged
- the pool's get/put balance on every way out of the route (the same
  counting technique as tests/test_condition_supersede.py::test_pool_put_matches_get)
- the subject page shows a "Mark entered-in-error" button for a current
  record and does not show one for a record that is already marked
"""

import json
import uuid
from decimal import Decimal

import psycopg
import pytest

from app import queries


MARK_ERROR_URL = "/{table}/{rid}/mark-error"
SUBJECT_URL = "/subjects/{sid}"
ALREADY_MARKED = "this record is already marked entered-in-error"
ALREADY_SUPERSEDED = "this version has already been superseded"
PLAIN_PAGE_NOSAVE = "something went wrong; nothing was saved"
FORBIDDEN_STRINGS = ("boom", "psycopg", "Traceback")

ORIGIN = {"Origin": "http://localhost"}

COND_FULL = {"code_text": "Asthma", "code_system": "http://www.oma.org",
             "code": "260.7", "clinical_status": "active",
             "verification_status": "unconfirmed", "onset_text": "",
             "onset_date": "2019-05-01", "note": "first recorded"}

COND_NEW = {"code_text": "Asthma, reviewed", "code_system": "", "code": "",
            "clinical_status": "inactive",
            "verification_status": "confirmed", "onset_text": "childhood",
            "onset_date": "", "note": "second recorded"}

MED_FULL = {"medication_text": "Metformin 500 mg",
            "medication_code_system": "http://www.nlm.nih.gov/research/umls/rxnorm",
            "medication_code": "6809", "status": "active",
            "authored_on": "2021-04-05",
            "sig_text": "500 mg twice a day with meals",
            "dose_value": "0.4", "dose_unit": "mg", "dose_unit_code": "mg",
            "route_text": "orally", "frequency": "1", "period": "0.5",
            "period_unit": "d", "timing_when": ["MORN", "EVE"],
            "note": "first recorded"}

MED_NEW = {"medication_text": "Metformin 1000 mg",
           "medication_code_system": "", "medication_code": "",
           "status": "on-hold", "authored_on": "",
           "sig_text": "1000 mg once a day in the evening",
           "dose_value": "1", "dose_unit": "g", "dose_unit_code": "",
           "route_text": "", "frequency": "", "period": "", "period_unit": "",
           "timing_when": [], "note": "second recorded"}


def _make_condition(client_tx, tx_conn, subject_id, data, expect=302):
    """Create one condition through the app (a real request) and return its
    id (the test's transaction is undone by the fixture). The id comes from
    the database, not from the redirect target: the newest non-superseded
    row for that subject (ids are uuidv7, so the newest sorts last)."""
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


def _make_medication(client_tx, tx_conn, subject_id, data, expect=302):
    """Create one medication request through the app (a real request) and
    return its id, the same way as _make_condition."""
    r = client_tx.post(f"/subjects/{subject_id}/medications/new",
                       data=data, headers=ORIGIN)
    assert r.status_code == expect, f"expected {expect}, got {r.status_code}"
    row = tx_conn.execute(
        "SELECT id FROM phsa.medication_request "
        "WHERE subject_id = %s AND superseded_by IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (subject_id,),
    ).fetchone()
    return str(row["id"])


def _counts(tx_conn, typed_table: str) -> dict:
    tables = ("subject", typed_table, "source_record")
    return {
        t: tx_conn.execute(
            f"SELECT count(*) AS n FROM phsa.{t}"
        ).fetchone()["n"]
        for t in tables
    }


def _raw(src):
    raw = src["resource_raw"]
    if isinstance(raw, str):  # some rows come back as text
        raw = json.loads(raw)
    return raw


def _stored_document(tx_conn, new_row_id, typed_table: str):
    """The source_record that the new typed row points at, fully fetched."""
    src_row = tx_conn.execute(
        f"""SELECT source_record_id FROM phsa.{typed_table} WHERE id = %s""",
        (new_row_id,),
    ).fetchone()
    return tx_conn.execute(
        """SELECT id, source_resource_id, resource_raw, content_sha256
           FROM phsa.source_record WHERE id = %s""",
        (src_row["source_record_id"],),
    ).fetchone()


def _supersede_via_route(client_tx, tx_conn, url: str, old_id: str,
                         data: dict):
    """Supersede the old record through its own supersede route; the new id
    is read off the old row's superseded_by (the old row points at the new
    one, never the reverse)."""
    r = client_tx.post(url, data=data, headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    pointer = tx_conn.execute(
        "SELECT superseded_by FROM phsa.condition WHERE id = %s",
        (old_id,),
    ).fetchone() if "condition" in url else tx_conn.execute(
        "SELECT superseded_by FROM phsa.medication_request WHERE id = %s",
        (old_id,),
    ).fetchone()
    assert pointer["superseded_by"] is not None
    return str(pointer["superseded_by"])


def test_condition_mark_error_supersedes(client_tx, tx_conn):
    """A record with every field filled, POST mark-error: redirect; one more
    row in source_record and in the typed table; the old row's
    superseded_by equals the new row's id; the new row has the
    entered-in-error value; every other column of the new row equals the old
    row's (compared one by one, excluding id, source_record_id,
    superseded_by and verification_status)."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_condition(client_tx, tx_conn, sid, COND_FULL)
    before = _counts(tx_conn, "condition")

    r = client_tx.post(MARK_ERROR_URL.format(table="conditions", rid=old_id),
                       headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    assert r.headers["Location"].endswith(SUBJECT_URL.format(sid=sid))

    after = _counts(tx_conn, "condition")
    assert after["condition"] == before["condition"] + 1
    assert after["source_record"] == before["source_record"] + 1

    old_row = tx_conn.execute(
        "SELECT * FROM phsa.condition WHERE id = %s", (old_id,),
    ).fetchone()
    assert old_row["superseded_by"] is not None
    new_row = tx_conn.execute(
        "SELECT * FROM phsa.condition WHERE id = %s",
        (old_row["superseded_by"],),
    ).fetchone()
    assert new_row is not None
    assert old_row["superseded_by"] == new_row["id"]
    assert new_row["verification_status"] == "entered-in-error"
    # every other column compared one by one (except id, source_record_id,
    # superseded_by and the one changed column)
    for key in old_row.keys():
        if key in ("id", "source_record_id", "superseded_by",
                   "verification_status"):
            continue
        assert new_row[key] == old_row[key], f"column {key} differs"
    # named check: clinical_status is unchanged
    assert new_row["clinical_status"] == "active"
    assert new_row["superseded_by"] is None


def test_condition_mark_error_stored_document(client_tx, tx_conn):
    """The new version's stored document: resource_raw.id equals
    source_resource_id; meta.security contains HTEST; content_sha256 equals
    sha256(convert_to(resource_raw::text, 'UTF8')) recomputed in a query;
    the document carries the entered-in-error value."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_condition(client_tx, tx_conn, sid, COND_FULL)
    r = client_tx.post(MARK_ERROR_URL.format(table="conditions", rid=old_id),
                       headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"

    old_row = tx_conn.execute(
        "SELECT superseded_by FROM phsa.condition WHERE id = %s",
        (old_id,),
    ).fetchone()
    src = _stored_document(tx_conn, str(old_row["superseded_by"]), "condition")
    raw = _raw(src)
    assert raw["id"] == src["source_resource_id"]
    assert "HTEST" in [tag["code"] for tag in raw["meta"]["security"]]
    recomputed = tx_conn.execute(
        """SELECT sha256(convert_to(resource_raw::text, 'UTF8')) AS h
           FROM phsa.source_record WHERE id = %s""",
        (src["id"],),
    ).fetchone()["h"]
    assert src["content_sha256"] == recomputed
    if isinstance(raw, str):
        raw = json.loads(raw)
    code = raw["verificationStatus"]["coding"][0]["code"]
    assert code == "entered-in-error"


def test_condition_mark_error_random_id_is_404(client_tx, tx_conn):
    queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    before = _counts(tx_conn, "condition")
    r = client_tx.post(
        MARK_ERROR_URL.format(table="conditions", rid=uuid.uuid4()),
        headers=ORIGIN)
    assert r.status_code == 404, f"expected 404, got {r.status_code}"
    assert _counts(tx_conn, "condition") == before


def test_condition_superseded_then_mark_error_is_409(client_tx, tx_conn):
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_condition(client_tx, tx_conn, sid, COND_FULL)
    before = _counts(tx_conn, "condition")
    _supersede_via_route(
        client_tx, tx_conn, f"/conditions/{old_id}/supersede", old_id,
        COND_NEW)
    after_supersede = _counts(tx_conn, "condition")
    assert after_supersede["condition"] == before["condition"] + 1

    r = client_tx.post(MARK_ERROR_URL.format(table="conditions", rid=old_id),
                       headers=ORIGIN)
    assert r.status_code == 409, f"expected 409, got {r.status_code}"
    assert ALREADY_SUPERSEDED in r.text
    assert _counts(tx_conn, "condition") == after_supersede


def test_condition_mark_error_twice_second_is_409_already_marked(
        client_tx, tx_conn):
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_condition(client_tx, tx_conn, sid, COND_FULL)

    r = client_tx.post(MARK_ERROR_URL.format(table="conditions", rid=old_id),
                       headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    after_first = _counts(tx_conn, "condition")

    old_row = tx_conn.execute(
        "SELECT superseded_by FROM phsa.condition WHERE id = %s",
        (old_id,),
    ).fetchone()
    new_id = str(old_row["superseded_by"])

    r = client_tx.post(MARK_ERROR_URL.format(table="conditions", rid=new_id),
                       headers=ORIGIN)
    assert r.status_code == 409, f"expected 409, got {r.status_code}"
    assert ALREADY_MARKED in r.text
    assert _counts(tx_conn, "condition") == after_first


def test_condition_mark_error_get_is_405(client_tx, tx_conn):
    queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    r = client_tx.get(MARK_ERROR_URL.format(table="conditions",
                                            rid=uuid.uuid4()))
    assert r.status_code == 405, f"expected 405, got {r.status_code}"


def test_condition_mark_error_boom_is_500(client_tx, tx_conn):
    """Only after the record is created: the matching create function in
    app.queries is made to raise psycopg.OperationalError('boom') before any
    SQL. The POST gives 500, the plain message, none of
    'boom'/'psycopg'/'Traceback' in the body, row counts unchanged."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    cid = _make_condition(client_tx, tx_conn, sid, COND_FULL)
    before = _counts(tx_conn, "condition")
    # only the mark-error request below runs with create_condition patched:
    # the patch must not cover the setup request above (it calls the same
    # function and would otherwise fail the setup)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        queries, "create_condition",
        lambda conn, sid, d: (_ for _ in ()).throw(
            psycopg.OperationalError("boom")))
    try:
        r = client_tx.post(MARK_ERROR_URL.format(table="conditions",
                                                 rid=cid), headers=ORIGIN)
    finally:
        monkeypatch.undo()
    assert r.status_code == 500, f"expected 500, got {r.status_code}"
    assert PLAIN_PAGE_NOSAVE in r.text
    for needle in FORBIDDEN_STRINGS:
        assert needle not in r.text, f"error page leaks {needle!r}"
    assert _counts(tx_conn, "condition") == before


def test_condition_pool_put_matches_get(client_tx, tx_conn):
    """The pool's get and put must balance on every way out of the route
    (the same counting technique as tests/test_condition_supersede.py::
    test_pool_put_matches_get): for each of the five requests below, get was
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
        cid = _make_condition(client_tx, tx_conn, sid, COND_FULL)
        # the already-marked and the boom parts need conditions that are
        # not patched away: they are created before any patch is applied
        second = _make_condition(client_tx, tx_conn, sid, COND_FULL)
        already_marked = _make_condition(client_tx, tx_conn, sid, COND_FULL)
        r = client_tx.post(
            MARK_ERROR_URL.format(table="conditions", rid=already_marked),
            headers=ORIGIN)
        assert r.status_code == 302, f"expected 302, got {r.status_code}"

        def _req(name, expected, fn):
            calls["get"] = 0
            calls["put"] = 0
            r = fn()
            assert r.status_code == expected, \
                f"{name}: expected {expected}, got {r.status_code}"
            assert calls["get"] >= 1, f"{name}: get never called"
            assert calls["put"] == calls["get"], \
                f"{name}: put ({calls['put']}) != get ({calls['get']})"

        _req("successful POST 302", 302, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="conditions", rid=cid),
            headers=ORIGIN))
        _req("404 POST", 404, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="conditions", rid=uuid.uuid4()),
            headers=ORIGIN))
        _req("already-superseded POST 409", 409, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="conditions", rid=cid),
            headers=ORIGIN))
        _req("already-marked POST 409", 409, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="conditions",
                                  rid=already_marked),
            headers=ORIGIN))
        # the boom part: create_condition patched to raise before any SQL
        monkeypatch.setattr(
            queries, "create_condition",
            lambda conn, sid, d: (_ for _ in ()).throw(
                psycopg.OperationalError("boom")))
        _req("boom POST 500", 500, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="conditions", rid=second),
            headers=ORIGIN))
    finally:
        monkeypatch.undo()


def test_medication_mark_error_supersedes(client_tx, tx_conn):
    """A medication request with every field filled, POST mark-error:
    redirect; one more row in source_record and in the typed table; the old
    row's superseded_by equals the new row's id; the new row has
    status 'entered-in-error'; every other column of the new row equals the
    old row's (compared one by one, excluding id, source_record_id,
    superseded_by and status)."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_medication(client_tx, tx_conn, sid, MED_FULL)
    before = _counts(tx_conn, "medication_request")

    r = client_tx.post(
        MARK_ERROR_URL.format(table="medications", rid=old_id),
        headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    assert r.headers["Location"].endswith(SUBJECT_URL.format(sid=sid))

    after = _counts(tx_conn, "medication_request")
    assert after["medication_request"] == before["medication_request"] + 1
    assert after["source_record"] == before["source_record"] + 1

    old_row = tx_conn.execute(
        "SELECT * FROM phsa.medication_request WHERE id = %s", (old_id,),
    ).fetchone()
    assert old_row["superseded_by"] is not None
    new_row = tx_conn.execute(
        "SELECT * FROM phsa.medication_request WHERE id = %s",
        (old_row["superseded_by"],),
    ).fetchone()
    assert new_row is not None
    assert old_row["superseded_by"] == new_row["id"]
    assert new_row["status"] == "entered-in-error"
    for key in old_row.keys():
        if key in ("id", "source_record_id", "superseded_by", "status"):
            continue
        assert new_row[key] == old_row[key], f"column {key} differs"
    assert new_row["superseded_by"] is None


def test_medication_mark_error_stored_document(client_tx, tx_conn):
    """The new version's stored document: resource_raw.id equals
    source_resource_id; meta.security contains HTEST; content_sha256 equals
    sha256(convert_to(resource_raw::text, 'UTF8')) recomputed in a query;
    the document's status is 'entered-in-error'."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_medication(client_tx, tx_conn, sid, MED_FULL)
    r = client_tx.post(
        MARK_ERROR_URL.format(table="medications", rid=old_id),
        headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"

    old_row = tx_conn.execute(
        "SELECT superseded_by FROM phsa.medication_request WHERE id = %s",
        (old_id,),
    ).fetchone()
    src = _stored_document(tx_conn, str(old_row["superseded_by"]),
                           "medication_request")
    raw = _raw(src)
    assert raw["id"] == src["source_resource_id"]
    assert "HTEST" in [tag["code"] for tag in raw["meta"]["security"]]
    recomputed = tx_conn.execute(
        """SELECT sha256(convert_to(resource_raw::text, 'UTF8')) AS h
           FROM phsa.source_record WHERE id = %s""",
        (src["id"],),
    ).fetchone()["h"]
    assert src["content_sha256"] == recomputed
    if isinstance(raw, str):
        raw = json.loads(raw)
    assert raw["status"] == "entered-in-error"


def test_medication_mark_error_random_id_is_404(client_tx, tx_conn):
    queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    before = _counts(tx_conn, "medication_request")
    r = client_tx.post(
        MARK_ERROR_URL.format(table="medications", rid=uuid.uuid4()),
        headers=ORIGIN)
    assert r.status_code == 404, f"expected 404, got {r.status_code}"
    assert _counts(tx_conn, "medication_request") == before


def test_medication_superseded_then_mark_error_is_409(client_tx, tx_conn):
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_medication(client_tx, tx_conn, sid, MED_FULL)
    before = _counts(tx_conn, "medication_request")
    _supersede_via_route(
        client_tx, tx_conn, f"/medications/{old_id}/supersede", old_id,
        MED_NEW)
    after_supersede = _counts(tx_conn, "medication_request")
    assert after_supersede["medication_request"] == before[
        "medication_request"] + 1

    r = client_tx.post(
        MARK_ERROR_URL.format(table="medications", rid=old_id),
        headers=ORIGIN)
    assert r.status_code == 409, f"expected 409, got {r.status_code}"
    assert ALREADY_SUPERSEDED in r.text
    assert _counts(tx_conn, "medication_request") == after_supersede


def test_medication_mark_error_twice_second_is_409_already_marked(
        client_tx, tx_conn):
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    old_id = _make_medication(client_tx, tx_conn, sid, MED_FULL)

    r = client_tx.post(
        MARK_ERROR_URL.format(table="medications", rid=old_id),
        headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    after_first = _counts(tx_conn, "medication_request")

    old_row = tx_conn.execute(
        "SELECT superseded_by FROM phsa.medication_request WHERE id = %s",
        (old_id,),
    ).fetchone()
    new_id = str(old_row["superseded_by"])

    r = client_tx.post(
        MARK_ERROR_URL.format(table="medications", rid=new_id),
        headers=ORIGIN)
    assert r.status_code == 409, f"expected 409, got {r.status_code}"
    assert ALREADY_MARKED in r.text
    assert _counts(tx_conn, "medication_request") == after_first


def test_medication_mark_error_get_is_405(client_tx, tx_conn):
    queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    r = client_tx.get(MARK_ERROR_URL.format(table="medications",
                                            rid=uuid.uuid4()))
    assert r.status_code == 405, f"expected 405, got {r.status_code}"


def test_medication_mark_error_boom_is_500(client_tx, tx_conn):
    """Only after the record is created: the matching create function in
    app.queries is made to raise psycopg.OperationalError('boom') before any
    SQL. The POST gives 500, the plain message, none of
    'boom'/'psycopg'/'Traceback' in the body, row counts unchanged."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    mid = _make_medication(client_tx, tx_conn, sid, MED_FULL)
    before = _counts(tx_conn, "medication_request")
    # only the mark-error request below runs with create_medication_request
    # patched: the patch must not cover the setup request above (it calls the
    # same function and would otherwise fail the setup)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        queries, "create_medication_request",
        lambda conn, sid, d: (_ for _ in ()).throw(
            psycopg.OperationalError("boom")))
    try:
        r = client_tx.post(
            MARK_ERROR_URL.format(table="medications", rid=mid),
            headers=ORIGIN)
    finally:
        monkeypatch.undo()
    assert r.status_code == 500, f"expected 500, got {r.status_code}"
    assert PLAIN_PAGE_NOSAVE in r.text
    for needle in FORBIDDEN_STRINGS:
        assert needle not in r.text, f"error page leaks {needle!r}"
    assert _counts(tx_conn, "medication_request") == before


def test_medication_pool_put_matches_get(client_tx, tx_conn):
    """The pool's get and put must balance on every way out of the route
    (the same counting technique as tests/test_condition_supersede.py::
    test_pool_put_matches_get): for each of the five requests below, get was
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
        mid = _make_medication(client_tx, tx_conn, sid, MED_FULL)
        # the already-marked and the boom parts need requests that are not
        # patched away: they are created before any patch is applied
        second = _make_medication(client_tx, tx_conn, sid, MED_FULL)
        already_marked = _make_medication(client_tx, tx_conn, sid, MED_FULL)
        r = client_tx.post(
            MARK_ERROR_URL.format(table="medications", rid=already_marked),
            headers=ORIGIN)
        assert r.status_code == 302, f"expected 302, got {r.status_code}"

        def _req(name, expected, fn):
            calls["get"] = 0
            calls["put"] = 0
            r = fn()
            assert r.status_code == expected, \
                f"{name}: expected {expected}, got {r.status_code}"
            assert calls["get"] >= 1, f"{name}: get never called"
            assert calls["put"] == calls["get"], \
                f"{name}: put ({calls['put']}) != get ({calls['get']})"

        _req("successful POST 302", 302, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="medications", rid=mid),
            headers=ORIGIN))
        _req("404 POST", 404, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="medications", rid=uuid.uuid4()),
            headers=ORIGIN))
        _req("already-superseded POST 409", 409, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="medications", rid=mid),
            headers=ORIGIN))
        _req("already-marked POST 409", 409, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="medications",
                                  rid=already_marked),
            headers=ORIGIN))
        # the boom part: create_medication_request patched to raise before
        # any SQL
        monkeypatch.setattr(
            queries, "create_medication_request",
            lambda conn, sid, d: (_ for _ in ()).throw(
                psycopg.OperationalError("boom")))
        _req("boom POST 500", 500, lambda: client_tx.post(
            MARK_ERROR_URL.format(table="medications", rid=second),
            headers=ORIGIN))
    finally:
        monkeypatch.undo()


def test_subject_page_shows_and_hides_mark_error_button(client_tx, tx_conn):
    """The subject page shows a 'Mark entered-in-error' button for a current
    record (both types) and does not show one for a record that is already
    marked; the Supersede link stays on every row."""
    sid = queries.create_subject(tx_conn, "Don Efra", "SUBJ-01")
    _make_condition(client_tx, tx_conn, sid, COND_FULL)
    _make_medication(client_tx, tx_conn, sid, MED_FULL)

    r = client_tx.get(SUBJECT_URL.format(sid=sid))
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    body = r.text
    assert "Mark entered-in-error" in body
    assert "Supersede (new version)" in body

    r = client_tx.post(
        MARK_ERROR_URL.format(table="conditions", rid=uuid.uuid4()),
        headers=ORIGIN)
    assert r.status_code == 404  # sanity: nothing exists to be marked yet

    # now mark both records through the app and check the button is gone
    cond_id = tx_conn.execute(
        "SELECT id FROM phsa.condition WHERE subject_id = %s "
        "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1", (sid,),
    ).fetchone()
    med_id = tx_conn.execute(
        "SELECT id FROM phsa.medication_request WHERE subject_id = %s "
        "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1", (sid,),
    ).fetchone()
    r = client_tx.post(
        MARK_ERROR_URL.format(table="conditions", rid=str(cond_id["id"])),
        headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    r = client_tx.post(
        MARK_ERROR_URL.format(table="medications", rid=str(med_id["id"])),
        headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"

    # the marked record is hidden from the default page (M4d), so look at it
    # with entered-in-error rows shown
    r = client_tx.get(SUBJECT_URL.format(sid=sid) + "?show_entered_in_error=1")
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    body = r.text
    assert "Mark entered-in-error" not in body
    assert "Supersede (new version)" in body
