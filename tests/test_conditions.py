"""M2: conditions — create and view (spec section 9, condition items of the
M2/M3 list; supersede and mark-error are M4 and are not tested here).

- create condition: one source_record and one typed row with matching ids
- every blank optional field is NULL in the row and absent from resource_raw
- code_system / code pair violations give 422 and write nothing
- required code_text / clinical_status give 422 and write nothing
- both onset_text and onset_date give 422 and write nothing (spec v0.5:
  onset is text or date, never both); the data layer refuses both by
  raising ValueError before any SQL runs
- resource_raw.id equals source_resource_id
- resource_raw.meta.security contains HTEST
- content_sha256 equals sha256(convert_to(resource_raw::text, 'UTF8'))
  recomputed in a test query
- the subject overview lists the subject's current conditions (read from
  phsa.current_condition) with a link to add one

The HTTP tests share the test's transaction through the overridden
connection factory (spec section 9; `client_tx` in conftest) and are
rolled back at the end; no test commits, no test may leave a row behind.
"""

import json
import uuid

import pytest

from app import fhir, queries


def _src_row(conn, source_record_id):
    return conn.execute(
        """SELECT id, source_system, source_resource_type, source_resource_id,
                  resource_raw, content_sha256, extraction_method,
                  epistemic_class, is_test_data
           FROM phsa.source_record WHERE id = %s""",
        (source_record_id,),
    ).fetchone()


def _raw(src):
    raw = src["resource_raw"]
    if isinstance(raw, str):  # some rows come back as text
        raw = json.loads(raw)
    return raw


def test_create_condition_writes_one_source_record_and_one_typed_row(conn):
    subject_id = queries.create_subject(conn, "A test person", None)
    row = queries.create_condition(conn, subject_id, {
        "code_text": "Type 2 diabetes",
        "code_system": None,
        "code": None,
        "clinical_status": "active",
        "verification_status": None,
        "onset_text": "approximately 14 years",
        "onset_date": None,
        "note": "Insulin-dependent",
    })

    typed = conn.execute(
        """SELECT id, subject_id, source_record_id, code_text, code_system, code,
                  clinical_status, verification_status, onset_text, onset_date,
                  note, superseded_by
           FROM phsa.condition WHERE id = %s""",
        (row["id"],),
    ).fetchone()
    assert typed is not None
    assert typed["subject_id"] == subject_id
    assert typed["code_text"] == "Type 2 diabetes"
    assert typed["code_system"] is None  # blank stays NULL
    assert typed["code"] is None
    assert typed["clinical_status"] == "active"
    assert typed["verification_status"] is None
    assert typed["onset_text"] == "approximately 14 years"
    assert typed["onset_date"] is None
    assert typed["note"] == "Insulin-dependent"
    assert typed["superseded_by"] is None

    # exactly one source record for this write, a Condition with HTEST
    src = _src_row(conn, typed["source_record_id"])
    assert src is not None
    assert src["source_system"] == "phsa-crs-manual"
    assert src["source_resource_type"] == "Condition"
    assert str(src["source_resource_id"]) == str(typed["id"])  # the ids match
    assert src["extraction_method"] == "manual_entry"
    assert src["is_test_data"] is True
    assert src["epistemic_class"] == "observation"  # the default

    raw = _raw(src)
    assert raw["resourceType"] == "Condition"
    assert raw["id"] == str(typed["id"])
    assert raw["meta"]["security"] == [fhir.HTEST_TAG]
    assert raw["code"]["text"] == "Type 2 diabetes"
    assert "coding" not in raw["code"]  # both code pair values blank
    assert raw["clinicalStatus"] == {
        "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active"}],
    }
    assert "verificationStatus" not in raw  # blank means absent
    assert raw["subject"] == {"reference": f"Patient/{subject_id}"}
    assert raw["onsetString"] == "approximately 14 years"
    assert "onsetDateTime" not in raw
    assert raw["note"] == [{"text": "Insulin-dependent"}]


def test_create_condition_blank_optional_fields_are_null_and_absent(conn):
    subject_id = queries.create_subject(conn, "Blank condition person", None)
    row = queries.create_condition(conn, subject_id, {
        "code_text": "Hypertension",
        "code_system": None,
        "code": None,
        "clinical_status": "active",
        "verification_status": None,
        "onset_text": None,
        "onset_date": None,
        "note": None,
    })
    typed = conn.execute(
        """SELECT code_system, code, verification_status, onset_text,
                  onset_date, note
           FROM phsa.condition WHERE id = %s""",
        (row["id"],),
    ).fetchone()
    for key in ("code_system", "code", "verification_status",
                "onset_text", "onset_date", "note"):
        assert typed[key] is None, key  # blank means blank, never ''

    raw = _raw(_src_row(conn, row["source_record_id"]))
    for key in ("verificationStatus", "onsetString", "onsetDateTime", "note"):
        assert key not in raw, key  # absent from the JSON, never null/""


def test_create_condition_with_coded_value_writes_code_and_coding(conn):
    subject_id = queries.create_subject(conn, "Coded person", None)
    row = queries.create_condition(conn, subject_id, {
        "code_text": "Hypertension",
        "code_system": "http://snomed.info/sct",
        "code": "38341003",
        "clinical_status": "active",
        "verification_status": "confirmed",
        "onset_text": None,
        "onset_date": "2010-05-01",
        "note": None,
    })
    typed = conn.execute(
        "SELECT code_system, code, verification_status, onset_date FROM phsa.condition WHERE id = %s",
        (row["id"],),
    ).fetchone()
    assert typed["code_system"] == "http://snomed.info/sct"
    assert typed["code"] == "38341003"
    assert typed["verification_status"] == "confirmed"
    assert typed["onset_date"].isoformat() == "2010-05-01"

    raw = _raw(_src_row(conn, row["source_record_id"]))
    assert raw["code"] == {
        "text": "Hypertension",
        "coding": [{"system": "http://snomed.info/sct", "code": "38341003"}],
    }
    assert raw["verificationStatus"] == {
        "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                    "code": "confirmed"}],
    }
    assert raw["onsetDateTime"] == "2010-05-01"
    assert "onsetString" not in raw


def test_create_condition_content_sha256_matches_database_recomputation(conn):
    """Spec section 9: content_sha256 equals sha256(convert_to(resource_raw::text,
    'UTF8')) recomputed in a test query — the canonicalization rule holds
    for conditions as for patients."""
    subject_id = queries.create_subject(conn, "Hash person", None)
    row = queries.create_condition(conn, subject_id, {
        "code_text": "CKD stage 3a",
        "code_system": None,
        "code": None,
        "clinical_status": "active",
        "verification_status": None,
        "onset_text": None,
        "onset_date": None,
        "note": "eGFR ~52",
    })
    src = _src_row(conn, row["source_record_id"])
    recomputed = conn.execute(
        """SELECT sha256(convert_to(resource_raw::text, 'UTF8')) AS h
           FROM phsa.source_record WHERE id = %s""",
        (row["source_record_id"],),
    ).fetchone()
    assert bytes(src["content_sha256"]) == bytes(recomputed["h"])


def test_create_condition_via_http(client_tx, tx_conn):
    subject_id = queries.create_subject(tx_conn, "HTTP condition person", None)
    before = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "Hypertension", "code_system": "", "code": "",
              "clinical_status": "active", "verification_status": "",
              "onset_text": "", "onset_date": "", "note": "Controlled"},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 302, r.text[:200]
    assert r.headers["Location"] == f"/subjects/{subject_id}"

    row = tx_conn.execute(
        "SELECT * FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    assert row is not None
    assert row["code_text"] == "Hypertension"
    assert row["code_system"] is None  # blank stayed blank
    assert row["note"] == "Controlled"
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert n == before + 1  # exactly one new source record for this write

    src = tx_conn.execute(
        "SELECT source_system, source_resource_type, source_resource_id, is_test_data "
        "FROM phsa.source_record WHERE id = %s",
        (row["source_record_id"],),
    ).fetchone()
    assert src["source_system"] == "phsa-crs-manual"
    assert src["source_resource_type"] == "Condition"
    assert str(src["source_resource_id"]) == str(row["id"])
    assert src["is_test_data"] is True


def test_condition_page_get(client_tx, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Page person", None)
    r = client_tx.get(f"/subjects/{subject_id}/conditions/new")
    assert r.status_code == 200
    body = r.text
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in body
    assert "Create" in body  # CRS verb on the button


def test_condition_missing_code_text_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Missing wording person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "", "code_system": "", "code": "",
              "clinical_status": "active", "verification_status": "",
              "onset_text": "", "onset_date": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    body = r.text
    assert "code_text is required" in body
    assert 'value="active"' in body  # the other input is kept
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in body
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written
    n_src = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert n_src == 0


def test_condition_missing_clinical_status_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Missing status person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "Hypertension", "code_system": "", "code": "",
              "clinical_status": "", "verification_status": "",
              "onset_text": "", "onset_date": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "clinical_status is required" in r.text
    assert 'value="Hypertension"' in r.text  # the input is kept
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_condition_code_system_without_code_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Pair violation person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "Hypertension",
              "code_system": "http://snomed.info/sct", "code": "",
              "clinical_status": "active", "verification_status": "",
              "onset_text": "", "onset_date": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "both be given, or both be blank" in r.text
    assert 'value="http://snomed.info/sct"' in r.text  # the input is kept
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_condition_code_without_system_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Pair violation two person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "Hypertension", "code_system": "",
              "code": "38341003", "clinical_status": "active",
              "verification_status": "", "onset_text": "", "onset_date": "",
              "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "both be given, or both be blank" in r.text
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_condition_bad_onset_date_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Bad date person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "Hypertension", "code_system": "", "code": "",
              "clinical_status": "active", "verification_status": "",
              "onset_text": "", "onset_date": "not-a-date", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "ISO date" in r.text
    assert 'value="not-a-date"' in r.text  # the input is kept
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_condition_both_onset_values_give_422_and_writes_nothing(app, tx_conn):
    """Spec v0.5: onset is text OR date, never both. Posting both is a 422
    with the input kept, and nothing is written (row counts before and
    after). The schema has no CHECK between the two columns, so this is a
    form-level rule."""
    subject_id = queries.create_subject(tx_conn, "Both onset person", None)
    client_tx = app.test_client()
    before_cond = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    before_src = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "Hypertension", "code_system": "", "code": "",
               "clinical_status": "active", "verification_status": "",
               "onset_text": "controlled, since spring", "onset_date": "2010-05-01",
               "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    body = r.text
    assert "one or the other" in body  # the error is shown under the onset fields
    assert 'value="controlled, since spring"' in body  # the input is kept
    assert 'value="2010-05-01"' in body
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in body
    after_cond = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    after_src = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert after_cond == before_cond  # nothing was written
    assert after_src == before_src


def test_create_condition_data_layer_refuses_both_onset_values(conn):
    """The data layer refuses both onset values by raising ValueError before
    any SQL runs, so no caller can bypass the form rule (spec v0.5)."""
    subject_id = queries.create_subject(conn, "Data layer both onset person", None)
    before_cond = conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    before_src = conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    with pytest.raises(ValueError, match="never both"):
        queries.create_condition(conn, subject_id, {
            "code_text": "Hypertension",
            "code_system": None,
            "code": None,
            "clinical_status": "active",
            "verification_status": None,
            "onset_text": "controlled, since spring",
            "onset_date": "2010-05-01",
            "note": None,
        })
    after_cond = conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    after_src = conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert after_cond == before_cond  # the ValueError raised before any SQL ran
    assert after_src == before_src


def test_condition_invalid_status_from_form_gives_422(app, tx_conn):
    """A value outside the schema CHECK list cannot reach the database:
    the form 422s before any write."""
    subject_id = queries.create_subject(tx_conn, "Bad status person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/conditions/new",
        data={"code_text": "Hypertension", "code_system": "", "code": "",
              "clinical_status": "bogus", "verification_status": "",
              "onset_text": "", "onset_date": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "not a recognised status" in r.text
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_subject_page_lists_current_conditions_and_add_link(client_tx, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Overview person", None)
    queries.create_condition(tx_conn, subject_id, {
        "code_text": "Type 2 diabetes", "code_system": None, "code": None,
        "clinical_status": "active", "verification_status": None,
        "onset_text": "approximately 14 years", "onset_date": None,
        "note": "Insulin-dependent",
    })
    queries.create_condition(tx_conn, subject_id, {
        "code_text": "Hypertension", "code_system": None, "code": None,
        "clinical_status": "active", "verification_status": "confirmed",
        "onset_text": None, "onset_date": "2010-05-01", "note": None,
    })

    r = client_tx.get(f"/subjects/{subject_id}")
    assert r.status_code == 200
    body = r.text
    assert "Type 2 diabetes" in body
    assert "Hypertension" in body
    assert "Insulin-dependent" in body
    assert "approximately 14 years" in body
    assert "2010-05-01" in body  # ISO date, shown as stored
    assert f"/subjects/{subject_id}/conditions/new" in body  # the add link
    assert "Add a condition" in body


def test_condition_page_unknown_subject_404(client_tx):
    r = client_tx.get(f"/subjects/{uuid.uuid4()}/conditions/new")
    assert r.status_code == 404
