"""M4d: the subject page hides entered-in-error rows by default.

A condition is entered-in-error when its verification_status is
'entered-in-error' (NULL is not); a medication request is entered-in-error
when its status is 'entered-in-error'. The subject page hides those rows
unless ?show_entered_in_error=1 is given, and offers the matching link.
The home page counts exclude entered-in-error rows.

Each test creates its own subject and records through the app, reuses the
test's transaction (conftest: `client_tx`, `tx_conn`), and lets the fixture
roll everything back.
"""

from app import queries


SUBJECT_URL = "/subjects/{sid}"
SHOW = "?show_entered_in_error=1"
ORIGIN = {"Origin": "http://localhost"}


def _make_subject(client_tx, tx_conn, label, fixture_id):
    """Create a subject through the app and return its id (database-issued)."""
    r = client_tx.post("/subjects/new",
                       data={"label": label, "fixture_id": fixture_id},
                       headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    row = tx_conn.execute(
        "SELECT id FROM phsa.subject WHERE label = %s "
        "ORDER BY id DESC LIMIT 1", (label,),
    ).fetchone()
    return row["id"]  # keep the uuid type: queries compare ids as uuids


def _make_condition(client_tx, tx_conn, subject_id, data, expect=302):
    """Create one condition through the app and return its id (the newest
    non-superseded row for that subject)."""
    r = client_tx.post(f"/subjects/{subject_id}/conditions/new",
                       data=data, headers=ORIGIN)
    assert r.status_code == expect, f"expected {expect}, got {r.status_code}"
    row = tx_conn.execute(
        "SELECT id FROM phsa.condition WHERE subject_id = %s "
        "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1", (subject_id,),
    ).fetchone()
    return str(row["id"])


def _make_medication(client_tx, tx_conn, subject_id, data, expect=302):
    """Create one medication request through the app, same way."""
    r = client_tx.post(f"/subjects/{subject_id}/medications/new",
                       data=data, headers=ORIGIN)
    assert r.status_code == expect, f"expected {expect}, got {r.status_code}"
    row = tx_conn.execute(
        "SELECT id FROM phsa.medication_request WHERE subject_id = %s "
        "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1", (subject_id,),
    ).fetchone()
    return str(row["id"])


def _mark_error(client_tx, table, record_id):
    r = client_tx.post(f"/{table}/{record_id}/mark-error", headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"


def _fetch(client_tx, url):
    r = client_tx.get(url)
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    return r.text


# distinct texts so a test can tell which rows are on the page
COND_A = {"code_text": "Alpha condition", "code_system": "", "code": "",
          "clinical_status": "active", "verification_status": "",
          "onset_text": "", "onset_date": "", "note": ""}
COND_B = {"code_text": "Bravo condition", "code_system": "", "code": "",
          "clinical_status": "active", "verification_status": "",
          "onset_text": "", "onset_date": "", "note": ""}
COND_BLANK = {"code_text": "Charlie condition", "code_system": "", "code": "",
              "clinical_status": "active", "verification_status": "",
              "onset_text": "", "onset_date": "", "note": ""}
COND_CREATED_EIE = {"code_text": "Delta condition", "code_system": "",
                    "code": "", "clinical_status": "active",
                    "verification_status": "entered-in-error",
                    "onset_text": "", "onset_date": "", "note": ""}

MED_A = {"medication_text": "Alpha medication", "medication_code_system": "",
         "medication_code": "", "status": "active", "authored_on": "",
         "sig_text": "", "dose_value": "", "dose_unit": "",
         "dose_unit_code": "", "route_text": "", "frequency": "",
         "period": "", "period_unit": "", "timing_when": [], "note": ""}
MED_B = {"medication_text": "Bravo medication", "medication_code_system": "",
         "medication_code": "", "status": "active", "authored_on": "",
         "sig_text": "", "dose_value": "", "dose_unit": "",
         "dose_unit_code": "", "route_text": "", "frequency": "",
         "period": "", "period_unit": "", "timing_when": [], "note": ""}


def test_conditions_default_hides_marked_row(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-a", "M4D-A")
    id_a = _make_condition(client_tx, tx_conn, sid, COND_A)
    id_b = _make_condition(client_tx, tx_conn, sid, COND_B)
    _mark_error(client_tx, "conditions", id_b)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid))
    assert "Alpha condition" in body
    assert "Bravo condition" not in body
    assert "show entered-in-error (1)" in body


def test_conditions_show_parameter_shows_marked_row(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-b", "M4D-B")
    id_a = _make_condition(client_tx, tx_conn, sid, COND_A)
    id_b = _make_condition(client_tx, tx_conn, sid, COND_B)
    _mark_error(client_tx, "conditions", id_b)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid) + SHOW)
    assert "Alpha condition" in body
    assert "Bravo condition" in body
    assert "hide entered-in-error" in body
    assert "show entered-in-error" not in body


def test_medications_default_hides_marked_row(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-c", "M4D-C")
    id_a = _make_medication(client_tx, tx_conn, sid, MED_A)
    id_b = _make_medication(client_tx, tx_conn, sid, MED_B)
    _mark_error(client_tx, "medications", id_b)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid))
    assert "Alpha medication" in body
    assert "Bravo medication" not in body
    assert "show entered-in-error (1)" in body


def test_medications_show_parameter_shows_marked_row(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-d", "M4D-D")
    id_a = _make_medication(client_tx, tx_conn, sid, MED_A)
    id_b = _make_medication(client_tx, tx_conn, sid, MED_B)
    _mark_error(client_tx, "medications", id_b)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid) + SHOW)
    assert "Alpha medication" in body
    assert "Bravo medication" in body
    assert "hide entered-in-error" in body
    assert "show entered-in-error" not in body


def test_no_marked_records_no_link(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-e", "M4D-E")
    _make_condition(client_tx, tx_conn, sid, COND_A)
    _make_medication(client_tx, tx_conn, sid, MED_A)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid))
    assert "entered-in-error (" not in body
    assert "hide entered-in-error" not in body


def test_only_condition_marked_none_text_and_link(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-f", "M4D-F")
    id_a = _make_condition(client_tx, tx_conn, sid, COND_A)
    _mark_error(client_tx, "conditions", id_a)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid))
    assert "Alpha condition" not in body
    assert "show entered-in-error (1)" in body


def test_created_as_entered_in_error_is_hidden_and_counted(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-g", "M4D-G")
    _make_condition(client_tx, tx_conn, sid, COND_CREATED_EIE)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid))
    assert "Delta condition" not in body
    assert "show entered-in-error (1)" in body


def test_blank_verification_status_shown_by_default(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-h", "M4D-H")
    _make_condition(client_tx, tx_conn, sid, COND_BLANK)

    body = _fetch(client_tx, SUBJECT_URL.format(sid=sid))
    assert "Charlie condition" in body
    assert "entered-in-error (" not in body
    assert "hide entered-in-error" not in body


def test_home_page_counts_exclude_entered_in_error(client_tx, tx_conn):
    sid = _make_subject(client_tx, tx_conn, "Eve M4d-i", "M4D-I")
    id_ca = _make_condition(client_tx, tx_conn, sid, COND_A)
    _make_condition(client_tx, tx_conn, sid, COND_B)
    id_ma = _make_medication(client_tx, tx_conn, sid, MED_A)
    _make_medication(client_tx, tx_conn, sid, MED_B)
    _mark_error(client_tx, "conditions", id_ca)
    _mark_error(client_tx, "medications", id_ma)

    # read on the test's own connection: nothing is committed, so no other
    # connection can see these rows
    subjects = queries.list_subjects(tx_conn)
    row = next(s for s in subjects if s["id"] == sid)
    assert row["condition_count"] == 1
    assert row["medication_count"] == 1
