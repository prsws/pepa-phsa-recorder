"""M1: subject creation (spec section 9, M1 list).

- create subject
- label required (HTTP 422, input kept, no database write)
- is_test_data is true in the row whatever the form posts (a forged
  is_test_data=false is ignored)
- duplicate fixture_id shows a friendly error, not a 500

HTTP tests share the test's transaction via the overridden connection
factory (spec section 9; the `client_tx` fixture in conftest) and are
rolled back at the end; no test commits.
"""

import psycopg
import uuid

from app import queries


def test_create_subject_row(conn):
    subject_id = queries.create_subject(conn, "A test person", "FIX-01")
    row = conn.execute(
        "SELECT label, fixture_id, is_test_data FROM phsa.subject WHERE id = %s",
        (subject_id,),
    ).fetchone()
    assert row["label"] == "A test person"
    assert row["fixture_id"] == "FIX-01"
    assert row["is_test_data"] is True


def test_create_subject_blank_means_blank(conn):
    subject_id = queries.create_subject(conn, "A test person", None)
    row = conn.execute(
        "SELECT fixture_id, is_test_data FROM phsa.subject WHERE id = %s",
        (subject_id,),
    ).fetchone()
    assert row["fixture_id"] is None  # blank stays NULL, never ''
    assert row["is_test_data"] is True


def test_list_subjects(conn):
    subject_id = queries.create_subject(conn, "A test person", "FIX-02")
    rows = queries.list_subjects(conn)
    assert [s["id"] for s in rows].count(subject_id) == 1
    mine = next(s for s in rows if s["id"] == subject_id)
    assert mine["label"] == "A test person"
    assert mine["condition_count"] == 0
    assert mine["medication_count"] == 0


def test_get_subject(conn):
    subject_id = queries.create_subject(conn, "A test person", None)
    assert queries.get_subject(conn, subject_id) is not None
    assert queries.get_subject(conn, str(uuid.uuid4())) is None


def test_subject_new_page_get(client_tx):
    r = client_tx.get("/subjects/new")
    assert r.status_code == 200
    body = r.text
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in body
    assert "Create" in body  # CRS verb on the button


def test_create_subject_via_http(client_tx, tx_conn):
    r = client_tx.post("/subjects/new", data={"label": "María Ruiz (test)", "fixture_id": "FIX-10"},
                       headers={"Origin": "http://localhost"})
    assert r.status_code == 302, r.text[:200]
    assert r.headers["Location"].startswith("/subjects/")

    row = tx_conn.execute(
        "SELECT label, fixture_id, is_test_data FROM phsa.subject WHERE fixture_id = %s",
        ("FIX-10",),
    ).fetchone()
    assert row is not None
    assert row["label"] == "María Ruiz (test)"
    # is_test_data is a constant in code, whatever the form posts
    assert row["is_test_data"] is True


def test_forged_is_test_data_is_ignored(client_tx, tx_conn):
    """A forged is_test_data=false in the form is ignored: the row is
    written with is_test_data = true (spec rule 4)."""
    r = client_tx.post("/subjects/new",
                       data={"label": "Forged flag person", "is_test_data": "false"},
                       headers={"Origin": "http://localhost"})
    assert r.status_code == 302
    row = tx_conn.execute(
        "SELECT is_test_data FROM phsa.subject WHERE label = %s",
        ("Forged flag person",),
    ).fetchone()
    assert row is not None
    assert row["is_test_data"] is True


def test_label_required_gives_422_and_writes_nothing(client_tx, tx_conn):
    r = client_tx.post("/subjects/new", data={"label": "", "fixture_id": "FIX-11"},
                       headers={"Origin": "http://localhost"})
    assert r.status_code == 422
    body = r.text
    # re-rendered with the user's input kept, error visible, banner still there
    assert "label is required" in body
    assert 'value="FIX-11"' in body  # the input is kept
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in body
    row = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.subject WHERE fixture_id = %s", ("FIX-11",)
    ).fetchone()
    assert row["n"] == 0  # nothing was written


def test_duplicate_fixture_id_friendly_error(client_tx, tx_conn):
    first = client_tx.post("/subjects/new",
                           data={"label": "First person", "fixture_id": "FIX-20"},
                           headers={"Origin": "http://localhost"})
    assert first.status_code == 302
    second = client_tx.post("/subjects/new",
                            data={"label": "Second person", "fixture_id": "FIX-20"},
                            headers={"Origin": "http://localhost"})
    assert second.status_code == 409, second.text[:200]
    assert "already used" in second.text
    # not a 500 page
    assert "Something went wrong" not in second.text
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.subject WHERE fixture_id = %s", ("FIX-20",)
    ).fetchone()["n"]
    assert n == 1  # the duplicate insert did not stick


def test_post_origin_mismatch_is_403(client_tx, tx_conn):
    r = client_tx.post("/subjects/new",
                       data={"label": "Evil origin person"},
                       headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.subject WHERE label = %s", ("Evil origin person",)
    ).fetchone()["n"]
    assert n == 0  # no write


def test_post_without_origin_allowed(client_tx, tx_conn):
    # a POST with no Origin header is allowed (plain form submit)
    r = client_tx.post("/subjects/new", data={"label": "No origin person"})
    assert r.status_code == 302
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.subject WHERE label = %s", ("No origin person",)
    ).fetchone()["n"]
    assert n == 1
