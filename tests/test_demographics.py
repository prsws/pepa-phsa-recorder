"""M1: demographics — create, view, supersede (spec section 9, M1 list).

- demographics create writes one source_record and one typed row with
  matching ids
- birth_year / birth_date mismatch gives 422 and writes nothing

The HTTP tests share the test's transaction through the overridden
connection factory (spec section 9; `client_tx` in conftest) and are
rolled back at the end; no test commits, no test may leave a row behind.
"""

import json
import uuid

import pytest

from app import fhir, queries


def test_create_demographics_writes_one_source_record_and_one_typed_row(conn):
    subject_id = queries.create_subject(conn, "A test person", None)
    d = {
        "family_name": "Colón Berríos",
        "given_name": "Efraín",
        "usual_name": "Don Efra",
        "sex": "male",
        "home_municipality": "Toa Alta",
        "preferred_language": "es",
        "birth_year": None,
        "birth_date": None,
    }
    row = queries.create_demographics(conn, subject_id, d)

    # one typed row, pointing at a source record ...
    typed = conn.execute(
        """SELECT id, subject_id, source_record_id, family_name, given_name,
                  usual_name, sex, home_municipality, preferred_language,
                  birth_year, birth_date, active, superseded_by
           FROM phsa.subject_demographics WHERE id = %s""",
        (row["id"],),
    ).fetchone()
    assert typed is not None
    assert typed["subject_id"] == subject_id
    assert typed["family_name"] == "Colón Berríos"
    assert typed["given_name"] == "Efraín"
    assert typed["usual_name"] == "Don Efra"
    assert typed["sex"] == "male"
    assert typed["home_municipality"] == "Toa Alta"
    assert typed["preferred_language"] == "es"
    assert typed["birth_year"] is None  # blank stays NULL
    assert typed["birth_date"] is None
    assert typed["active"] is True
    assert typed["superseded_by"] is None

    # ... and exactly one source record, whose id matches the typed row's
    # source_record_id and whose resource id matches the typed row's id.
    src = conn.execute(
        """SELECT source_system, source_resource_type, source_resource_id,
                  resource_raw, extraction_method, is_test_data, content_sha256
           FROM phsa.source_record WHERE id = %s""",
        (typed["source_record_id"],),
    ).fetchone()
    assert src is not None
    assert src["source_system"] == "phsa-crs-manual"
    assert src["source_resource_type"] == "Patient"
    assert str(src["source_resource_id"]) == str(typed["id"])  # the ids match
    assert src["extraction_method"] == "manual_entry"
    assert src["is_test_data"] is True

    raw = src["resource_raw"]
    if isinstance(raw, str):  # some rows come back as text
        raw = json.loads(raw)
    assert raw["id"] == str(typed["id"])
    assert raw["resourceType"] == "Patient"
    assert raw["meta"]["security"] == [fhir.HTEST_TAG]
    assert raw["name"] == [
        {"use": "official", "family": "Colón Berríos", "given": ["Efraín"]},
        {"use": "usual", "text": "Don Efra"},
    ]
    assert raw["gender"] == "male"
    assert raw["address"] == [{"city": "Toa Alta"}]
    assert raw["communication"] == [{"language": {"text": "es"}}]
    assert "birthDate" not in raw  # blank means absent


def test_create_demographics_blank_fields_are_null(conn):
    subject_id = queries.create_subject(conn, "Blank person", None)
    row = queries.create_demographics(conn, subject_id, {
        "family_name": None, "given_name": None, "usual_name": None,
        "sex": None, "home_municipality": None, "preferred_language": None,
        "birth_year": None, "birth_date": None,
    })
    typed = conn.execute(
        """SELECT family_name, given_name, usual_name, sex, home_municipality,
                  preferred_language, birth_year, birth_date
           FROM phsa.subject_demographics WHERE id = %s""",
        (row["id"],),
    ).fetchone()
    for key in ("family_name", "given_name", "usual_name", "sex",
                "home_municipality", "preferred_language", "birth_year",
                "birth_date"):
        assert typed[key] is None, key  # blank means blank, never ''


def test_create_demographics_via_http(client_tx, tx_conn):
    subject_id = queries.create_subject(tx_conn, "HTTP person", None)
    before = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    r = client_tx.post(
        f"/subjects/{subject_id}/demographics",
        data={"family_name": "García", "given_name": "Rosa", "usual_name": "",
              "sex": "female", "birth_year": "", "birth_date": "",
              "home_municipality": "Caguas", "preferred_language": "es"},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 302, r.text[:200]
    assert r.headers["Location"] == f"/subjects/{subject_id}"

    row = tx_conn.execute(
        "SELECT * FROM phsa.subject_demographics WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    assert row is not None
    assert row["family_name"] == "García"
    assert row["sex"] == "female"
    assert row["usual_name"] is None  # blank stayed blank
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert n == before + 1  # exactly one new source record for this write

    src = tx_conn.execute(
        "SELECT source_resource_id, is_test_data, source_resource_type "
        "FROM phsa.source_record WHERE id = %s",
        (row["source_record_id"],),
    ).fetchone()
    assert str(src["source_resource_id"]) == str(row["id"])
    assert src["is_test_data"] is True
    assert src["source_resource_type"] == "Patient"


def test_demographics_page_get_creates_then_supersedes(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "View person", None)
    client_tx = app.test_client()

    # GET before any demographics: create mode, empty form
    r = client_tx.get(f"/subjects/{subject_id}/demographics")
    assert r.status_code == 200
    assert "Create" in r.text
    assert "Supersede (new version)" not in r.text

    # create them
    r = client_tx.post(
        f"/subjects/{subject_id}/demographics",
        data={"family_name": "Díaz", "given_name": "Luis", "usual_name": "Lucho",
              "sex": "male", "birth_year": "1957", "birth_date": "1957-03-05",
              "home_municipality": "Bayamón", "preferred_language": "es"},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 302

    # GET afterwards: supersede mode, pre-filled with what was stored
    r = client_tx.get(f"/subjects/{subject_id}/demographics")
    assert r.status_code == 200
    assert "Supersede (new version)" in r.text
    assert 'value="Díaz"' in r.text
    assert 'value="1957"' in r.text
    assert 'value="1957-03-05"' in r.text
    assert 'value="Bayamón"' in r.text
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in r.text


def test_supersede_demographics_via_http(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Supersede person", None)
    client_tx = app.test_client()
    first = queries.create_demographics(tx_conn, subject_id, {
        "family_name": "Old Surname", "given_name": "Old", "usual_name": None,
        "sex": "male", "home_municipality": None, "preferred_language": None,
        "birth_year": None, "birth_date": None,
    })

    r = client_tx.post(
        f"/subjects/{subject_id}/demographics",
        data={"family_name": "New Surname", "given_name": "New", "usual_name": "Neno",
              "sex": "male", "birth_year": "", "birth_date": "",
              "home_municipality": "Hato Rey", "preferred_language": "es"},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 302, r.text[:200]

    old = tx_conn.execute(
        "SELECT id, superseded_by FROM phsa.subject_demographics WHERE id = %s",
        (first["id"],),
    ).fetchone()
    assert old["superseded_by"] is not None and old["superseded_by"] != first["id"]
    new_id = old["superseded_by"]

    new = tx_conn.execute(
        "SELECT family_name, given_name, usual_name, home_municipality, preferred_language, superseded_by "
        "FROM phsa.subject_demographics WHERE id = %s",
        (new_id,),
    ).fetchone()
    assert new["family_name"] == "New Surname"
    assert new["given_name"] == "New"
    assert new["usual_name"] == "Neno"
    assert new["home_municipality"] == "Hato Rey"
    assert new["preferred_language"] == "es"
    assert new["superseded_by"] is None

    # the current view shows only the new row, with the effective class attached
    cur = tx_conn.execute(
        "SELECT family_name, effective_class FROM phsa.current_subject_demographics "
        "WHERE subject_id = %s AND superseded_by IS NULL",
        (subject_id,),
    ).fetchall()
    assert len(cur) == 1
    assert cur[0]["family_name"] == "New Surname"
    assert cur[0]["effective_class"] == "observation"

    # and two source records now exist, the new one a Patient with HTEST
    srcs = tx_conn.execute(
        "SELECT source_resource_id, is_test_data FROM phsa.source_record "
        "WHERE source_resource_type = 'Patient' ORDER BY source_resource_id"
    ).fetchall()
    assert len(srcs) == 2
    assert all(s["is_test_data"] for s in srcs)


def test_supersede_demographics_data_layer(tx_conn):
    """The 409 branch: superseding an already-superseded row rolls back
    everything (spec 4.2 step 3) and raises SupersedeConflict."""
    subject_id = queries.create_subject(tx_conn, "Conflict person", None)
    first = queries.create_demographics(tx_conn, subject_id, {
        "family_name": "One", "given_name": None, "usual_name": None,
        "sex": None, "home_municipality": None, "preferred_language": None,
        "birth_year": None, "birth_date": None,
    })
    second = queries.create_demographics(tx_conn, subject_id, {
        "family_name": "Two", "given_name": None, "usual_name": None,
        "sex": None, "home_municipality": None, "preferred_language": None,
        "birth_year": None, "birth_date": None,
    })
    # first is still current; supersede it with a third version
    third = queries.supersede_demographics(tx_conn, first["id"], subject_id, {
        "family_name": "Three", "given_name": None, "usual_name": None,
        "sex": None, "home_municipality": None, "preferred_language": None,
        "birth_year": None, "birth_date": None,
    })
    assert third is not None
    assert third["id"] != first["id"]
    # now first is already superseded: a further supersede of it must fail
    with pytest.raises(queries.SupersedeConflict):
        queries.supersede_demographics(tx_conn, first["id"], subject_id, {
            "family_name": "Four", "given_name": None, "usual_name": None,
            "sex": None, "home_municipality": None, "preferred_language": None,
            "birth_year": None, "birth_date": None,
        })
    tx_conn.rollback()  # discard the failed attempt (uncommitted in the test tx)
    still = tx_conn.execute(
        "SELECT superseded_by FROM phsa.subject_demographics WHERE id = %s",
        (first["id"],),
    ).fetchone()
    assert still["superseded_by"] == third["id"]  # unchanged


def test_birth_year_date_mismatch_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Mismatch person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/demographics",
        data={"family_name": "", "given_name": "", "usual_name": "", "sex": "",
              "birth_year": "1950", "birth_date": "1951-06-01",
              "home_municipality": "", "preferred_language": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "do not agree" in r.text
    assert 'value="1950"' in r.text          # the user's input is kept
    assert 'value="1951-06-01"' in r.text
    n_typed = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.subject_demographics WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n_typed == 0  # nothing written
    n_src = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert n_src == 0


def test_agreeing_birth_year_and_date_allowed(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Agree person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/demographics",
        data={"family_name": "Pérez", "given_name": "Ana", "usual_name": "",
              "sex": "female", "birth_year": "1950", "birth_date": "1950-06-01",
              "home_municipality": "", "preferred_language": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 302, r.text[:200]
    row = tx_conn.execute(
        "SELECT birth_year, birth_date FROM phsa.subject_demographics WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    assert row["birth_year"] == 1950
    assert row["birth_date"].isoformat() == "1950-06-01"


def test_demographics_page_unknown_subject_404(client_tx):
    r = client_tx.get(f"/subjects/{uuid.uuid4()}/demographics")
    assert r.status_code == 404
    r = client_tx.get(f"/subjects/{uuid.uuid4()}")
    assert r.status_code == 404


def test_index_lists_subjects(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Listed person", "FIX-30")
    client_tx = app.test_client()
    r = client_tx.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Listed person" in body
    assert "FIX-30" in body
    assert f"/subjects/{subject_id}" in body
    assert "Create a subject" in body
