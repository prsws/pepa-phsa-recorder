"""M3: medication requests — create and view (spec section 9, medication
items of the M2/M3 list; supersede and mark-error are M4 and are not tested
here).

- create medication request: one source_record and one typed row with matching
  ids; intent left at its schema default and safety_critical at its default
- every blank optional field is NULL in the row and absent from resource_raw
- medication_code_system / medication_code pair violations give 422 and write
  nothing; dose_value / dose_unit pair violations give 422 and write nothing
- required medication_text / status give 422 and write nothing
- resource_raw.id equals source_resource_id
- resource_raw.meta.security contains HTEST
- content_sha256 equals sha256(convert_to(resource_raw::text, 'UTF8'))
  recomputed in a test query
- the subject overview lists the subject's current medication requests (read
  from phsa.current_medication_request) with a link to add one

The HTTP tests share the test's transaction through the overridden
connection factory (spec section 9; `client_tx` in conftest) and are
rolled back at the end; no test commits, no test may leave a row behind.
"""

import json

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


def test_create_medication_request_writes_one_source_record_and_one_typed_row(conn):
    subject_id = queries.create_subject(conn, "A test person", None)
    row = queries.create_medication_request(conn, subject_id, {
        "medication_text": "Metformin 500 mg",
        "medication_code_system": None,
        "medication_code": None,
        "status": "active",
        "authored_on": None,
        "sig_text": "500 mg BID with meals",
        "dose_value": 500,
        "dose_unit": "mg",
        "dose_unit_code": "mg",
        "route_text": None,
        "frequency": 2,
        "period": 1,
        "period_unit": "d",
        "timing_when": None,
        "note": None,
    })

    typed = conn.execute(
        """SELECT id, subject_id, source_record_id, medication_text,
                  medication_code_system, medication_code, status, intent,
                  authored_on, sig_text, dose_value, dose_unit, dose_unit_code,
                  route_text, frequency, period, period_unit, timing_when,
                  note, safety_critical, superseded_by
           FROM phsa.medication_request WHERE id = %s""",
        (row["id"],),
    ).fetchone()
    assert typed is not None
    assert typed["subject_id"] == subject_id
    assert typed["medication_text"] == "Metformin 500 mg"
    assert typed["medication_code_system"] is None  # blank stays NULL
    assert typed["medication_code"] is None
    assert typed["status"] == "active"
    assert typed["intent"] == "order"  # the schema default; not on the form
    assert typed["authored_on"] is None
    assert typed["sig_text"] == "500 mg BID with meals"
    assert typed["dose_value"] == 500
    assert typed["dose_unit"] == "mg"
    assert typed["dose_unit_code"] == "mg"
    assert typed["route_text"] is None
    assert typed["frequency"] == 2
    assert typed["period"] == 1
    assert typed["period_unit"] == "d"
    assert typed["timing_when"] is None
    assert typed["note"] is None
    assert typed["safety_critical"] is True  # the schema default
    assert typed["superseded_by"] is None

    # exactly one source record for this write, a MedicationRequest with HTEST
    src = _src_row(conn, typed["source_record_id"])
    assert src is not None
    assert src["source_system"] == "phsa-crs-manual"
    assert src["source_resource_type"] == "MedicationRequest"
    assert str(src["source_resource_id"]) == str(typed["id"])  # the ids match
    assert src["extraction_method"] == "manual_entry"
    assert src["is_test_data"] is True
    assert src["epistemic_class"] == "observation"  # the default

    raw = _raw(src)
    assert raw["resourceType"] == "MedicationRequest"
    assert raw["id"] == str(typed["id"])
    assert raw["meta"]["security"] == [fhir.HTEST_TAG]
    assert raw["status"] == "active"
    assert raw["intent"] == "order"  # spec 5.3 lists intent among the document's keys
    assert raw["intent"] == typed["intent"]  # the document and the row must agree
    assert raw["medicationCodeableConcept"] == {"text": "Metformin 500 mg"}
    assert raw["subject"] == {"reference": f"Patient/{subject_id}"}
    assert "authoredOn" not in raw  # blank means absent
    instruction = raw["dosageInstruction"][0]
    assert instruction["text"] == "500 mg BID with meals"
    assert instruction["timing"]["repeat"] == {"frequency": 2, "period": 1,
                                               "periodUnit": "d"}
    assert instruction["doseAndRate"] == [
        {"doseQuantity": {"value": 500, "unit": "mg",
                          "system": "http://unitsofmeasure.org", "code": "mg"}},
    ]
    assert "route" not in instruction
    assert "note" not in raw  # blank means absent


def test_create_medication_request_blank_optional_fields_are_null_and_absent(conn):
    subject_id = queries.create_subject(conn, "Blank medication person", None)
    row = queries.create_medication_request(conn, subject_id, {
        "medication_text": "Aspirin 81 mg",
        "medication_code_system": None,
        "medication_code": None,
        "status": "active",
        "authored_on": None,
        "sig_text": "81 mg daily",
        "dose_value": None,
        "dose_unit": None,
        "dose_unit_code": None,
        "route_text": None,
        "frequency": None,
        "period": None,
        "period_unit": None,
        "timing_when": None,
        "note": None,
    })
    typed = conn.execute(
        """SELECT medication_code_system, medication_code, authored_on,
                  sig_text, dose_value, dose_unit, dose_unit_code, route_text,
                  frequency, period, period_unit, timing_when, note
           FROM phsa.medication_request WHERE id = %s""",
        (row["id"],),
    ).fetchone()
    for key in ("medication_code_system", "medication_code", "authored_on",
                "dose_value", "dose_unit", "dose_unit_code", "route_text",
                "frequency", "period", "period_unit", "timing_when", "note"):
        assert typed[key] is None, key  # blank means blank, never ''

    raw = _raw(_src_row(conn, row["source_record_id"]))
    assert raw["medicationCodeableConcept"] == {"text": "Aspirin 81 mg"}
    assert raw["dosageInstruction"] == [
        {"text": "81 mg daily"}
    ]  # only what was entered
    for key in ("authoredOn", "note"):
        assert key not in raw, key  # absent from the JSON, never null/""
    assert "coding" not in raw["medicationCodeableConcept"]


def test_create_medication_request_nothing_in_dosage_instruction_omits_it(conn):
    """Spec 5.3: omit dosageInstruction entirely if nothing in it was entered."""
    subject_id = queries.create_subject(conn, "No instruction person", None)
    row = queries.create_medication_request(conn, subject_id, {
        "medication_text": "A listed medication",
        "medication_code_system": None,
        "medication_code": None,
        "status": "on-hold",
        "authored_on": None,
        "sig_text": None,
        "dose_value": None,
        "dose_unit": None,
        "dose_unit_code": None,
        "route_text": None,
        "frequency": None,
        "period": None,
        "period_unit": None,
        "timing_when": None,
        "note": None,
    })
    raw = _raw(_src_row(conn, row["source_record_id"]))
    assert "dosageInstruction" not in raw
    assert raw["status"] == "on-hold"


def test_create_medication_request_content_sha256_matches_database_recomputation(conn):
    """Spec section 9: content_sha256 equals sha256(convert_to(resource_raw::text,
    'UTF8')) recomputed in a test query — the canonicalization rule holds
    for medication requests as for patients and conditions."""
    subject_id = queries.create_subject(conn, "Hash person", None)
    row = queries.create_medication_request(conn, subject_id, {
        "medication_text": "Losartan 50 mg",
        "medication_code_system": None,
        "medication_code": None,
        "status": "active",
        "authored_on": "2025-09-30",
        "sig_text": "50 mg daily AM",
        "dose_value": 50,
        "dose_unit": "mg",
        "dose_unit_code": "mg",
        "route_text": None,
        "frequency": 1,
        "period": 1,
        "period_unit": "d",
        "timing_when": ["MORN"],
        "note": None,
    })
    typed = conn.execute(
        "SELECT authored_on, timing_when FROM phsa.medication_request WHERE id = %s",
        (row["id"],),
    ).fetchone()
    assert typed["authored_on"].isoformat() == "2025-09-30"
    assert typed["timing_when"] == ["MORN"]

    src = _src_row(conn, row["source_record_id"])
    recomputed = conn.execute(
        """SELECT sha256(convert_to(resource_raw::text, 'UTF8')) AS h
           FROM phsa.source_record WHERE id = %s""",
        (row["source_record_id"],),
    ).fetchone()
    assert bytes(src["content_sha256"]) == bytes(recomputed["h"])

    raw = _raw(src)
    assert raw["authoredOn"] == "2025-09-30"
    instruction = raw["dosageInstruction"][0]
    assert instruction["timing"]["repeat"]["when"] == ["MORN"]


def test_create_medication_request_via_http(client_tx, tx_conn):
    subject_id = queries.create_subject(tx_conn, "HTTP medication person", None)
    before_src = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg",
              "medication_code_system": "", "medication_code": "",
              "status": "active", "authored_on": "",
              "sig_text": "500 mg BID with meals",
              "dose_value": "500", "dose_unit": "mg", "dose_unit_code": "mg",
              "route_text": "", "frequency": "2", "period": "1",
              "period_unit": "d", "timing_when": "MORN", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 302, r.text[:200]
    assert r.headers["Location"] == f"/subjects/{subject_id}"

    row = tx_conn.execute(
        "SELECT * FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()
    assert row is not None
    assert row["medication_text"] == "Metformin 500 mg"
    assert row["medication_code_system"] is None  # blank stayed blank
    assert row["intent"] == "order"  # the schema default; not on the form
    assert row["dose_value"] == 500
    assert row["frequency"] == 2
    n_src = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert n_src == before_src + 1  # exactly one new source record for this write

    src = tx_conn.execute(
        "SELECT source_system, source_resource_type, source_resource_id, is_test_data "
        "FROM phsa.source_record WHERE id = %s",
        (row["source_record_id"],),
    ).fetchone()
    assert src["source_system"] == "phsa-crs-manual"
    assert src["source_resource_type"] == "MedicationRequest"
    assert str(src["source_resource_id"]) == str(row["id"])
    assert src["is_test_data"] is True


def test_medication_page_get(client_tx, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Page person", None)
    r = client_tx.get(f"/subjects/{subject_id}/medications/new")
    assert r.status_code == 200
    body = r.text
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in body
    assert "Create" in body  # CRS verb on the button


def test_medication_missing_text_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Missing text person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "", "medication_code_system": "",
              "medication_code": "", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    body = r.text
    assert "medication_text is required" in body
    assert 'value="active"' in body  # the other input is kept
    assert "TEST DATA ONLY. Synthetic fixtures. Not a medical record." in body
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written
    n_src = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.source_record WHERE source_system = 'phsa-crs-manual'"
    ).fetchone()["n"]
    assert n_src == 0


def test_medication_missing_status_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Missing status person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg", "medication_code_system": "",
              "medication_code": "", "status": "", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "status is required" in r.text
    assert 'value="Metformin 500 mg"' in r.text  # the input is kept
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_invalid_status_from_form_gives_422(app, tx_conn):
    """A value outside the schema CHECK list cannot reach the database:
    the form 422s before any write."""
    subject_id = queries.create_subject(tx_conn, "Bad status person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg", "medication_code_system": "",
              "medication_code": "", "status": "bogus", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "not a recognised status" in r.text
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_code_system_without_code_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Pair violation person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg",
              "medication_code_system": "http://www.rxnorm.org",
              "medication_code": "", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "both be given, or both be blank" in r.text
    assert 'value="http://www.rxnorm.org"' in r.text  # the input is kept
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_code_without_system_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Pair violation two person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg", "medication_code_system": "",
              "medication_code": "6809", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "both be given, or both be blank" in r.text
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_dose_value_without_unit_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Dose pair person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg", "medication_code_system": "",
              "medication_code": "", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "500", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "both be given, or both be blank" in r.text
    assert 'value="500"' in r.text  # the input is kept
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_unit_without_dose_value_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Dose pair two person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg", "medication_code_system": "",
              "medication_code": "", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "mg",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "both be given, or both be blank" in r.text
    assert 'value="mg"' in r.text  # the input is kept
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_bad_frequency_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Bad frequency person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg", "medication_code_system": "",
              "medication_code": "", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "zero",
              "period": "", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "frequency" in r.text
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_bad_period_gives_422_and_writes_nothing(app, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Bad period person", None)
    client_tx = app.test_client()
    r = client_tx.post(
        f"/subjects/{subject_id}/medications/new",
        data={"medication_text": "Metformin 500 mg", "medication_code_system": "",
              "medication_code": "", "status": "active", "authored_on": "",
              "sig_text": "", "dose_value": "", "dose_unit": "",
              "dose_unit_code": "", "route_text": "", "frequency": "",
              "period": "-3", "period_unit": "", "timing_when": "", "note": ""},
        headers={"Origin": "http://localhost"},
    )
    assert r.status_code == 422
    assert "period" in r.text
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.medication_request WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()["n"]
    assert n == 0  # nothing was written


def test_medication_page_unknown_subject_404(client_tx):
    import uuid
    r = client_tx.get(f"/subjects/{uuid.uuid4()}/medications/new")
    assert r.status_code == 404


def test_subject_page_lists_current_medication_requests_and_add_link(client_tx, tx_conn):
    subject_id = queries.create_subject(tx_conn, "Overview person", None)
    queries.create_medication_request(tx_conn, subject_id, {
        "medication_text": "Metformin 500 mg",
        "medication_code_system": None, "medication_code": None,
        "status": "active", "authored_on": None,
        "sig_text": "500 mg BID with meals",
        "dose_value": 500, "dose_unit": "mg", "dose_unit_code": "mg",
        "route_text": None, "frequency": 2, "period": 1, "period_unit": "d",
        "timing_when": None, "note": None,
    })
    queries.create_medication_request(tx_conn, subject_id, {
        "medication_text": "Insulin glargine 22 units",
        "medication_code_system": None, "medication_code": None,
        "status": "active", "authored_on": None,
        "sig_text": "22 units nightly",
        "dose_value": 22, "dose_unit": "units", "dose_unit_code": None,
        "route_text": None, "frequency": 1, "period": 1, "period_unit": "d",
        "timing_when": ["NIGHT"], "note": "Refrigerated",
    })

    r = client_tx.get(f"/subjects/{subject_id}")
    assert r.status_code == 200
    body = r.text
    assert "Metformin 500 mg" in body
    assert "Insulin glargine 22 units" in body
    assert f"/subjects/{subject_id}/medications/new" in body  # the add link
    assert "Add a medication request" in body
