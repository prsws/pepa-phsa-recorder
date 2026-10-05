"""Parameterised SQL. Schema-qualified everywhere (phsa.x).

Append-only rule (spec section 0.3): the only UPDATE the app may ever issue
is the single superseded_by pointer, in the shape
    UPDATE phsa.<table> SET superseded_by = %(new_id)s
        WHERE id = %(old_id)s AND superseded_by IS NULL
No other UPDATE, and no DELETE or TRUNCATE, may appear in this file.

All functions here take a connection and NEVER commit; the request layer
commits (or rolls back) at the end of the request (spec sections 4.1, 9).

is_test_data is a constant true on every row the app writes (spec rule 4);
it is never taken from the form.
"""

from datetime import date
from decimal import Decimal

from . import fhir

# source_record values that are constants of the app, not of the form
# (spec 4.1).
SOURCE_SYSTEM = "phsa-crs-manual"
EXTRACTION_METHOD = "manual_entry"


# ---------------------------------------------------------------------------
# The write recipe (spec 4.1), one transaction, never committing here.
# ---------------------------------------------------------------------------

def _new_uuid(conn) -> str:
    """Fresh id from the database: uuidv7() (spec 4.1 step 1)."""
    row = conn.execute("SELECT uuidv7()::text AS id").fetchone()
    return row["id"]


def _insert_source_record(conn, resource_type: str, resource_id: str, resource_raw: str) -> str:
    """Spec 4.1 step 3. content_sha256 is computed IN THE DATABASE from the
    same JSON string that is stored, as sha256 of PostgreSQL's own jsonb
    text output (the provisional canonicalization rule, spec 4.1 step 3)."""
    return conn.execute(
        """
        INSERT INTO phsa.source_record (
          id, source_system, source_resource_type, source_resource_id,
          resource_raw, content_sha256, extraction_method, is_test_data
        )
        VALUES (%(id)s, %(system)s, %(rtype)s, %(rid)s, %(raw)s::jsonb,
                sha256(convert_to(%(raw)s::jsonb::text, 'UTF8')),
                %(extraction)s, true)
        RETURNING id
        """,
        {
            "id": resource_id,
            "system": SOURCE_SYSTEM,
            "rtype": resource_type,
            "rid": resource_id,
            "raw": resource_raw,
            "extraction": EXTRACTION_METHOD,
        },
    ).fetchone()["id"]


def create_source_record(conn, resource_type: str, resource_id: str, resource_raw: str) -> str:
    """Insert one source_record (spec 4.1 step 3). Never commits."""
    return _insert_source_record(conn, resource_type, resource_id, resource_raw)


# ---------------------------------------------------------------------------
# subject (the identity anchor)
# ---------------------------------------------------------------------------

def create_subject(conn, label: str, fixture_id: str | None) -> str:
    """Spec 4.1 steps 1-3 for the subject row itself: the anchor is written
    with a database-issued uuidv7 id. is_test_data is a constant true.
    Never commits. The caller maps fixture_id uniqueness violations to a
    friendly error (spec M1: duplicate fixture_id, not a 500)."""
    new_id = _new_uuid(conn)
    row = conn.execute(
        """
        INSERT INTO phsa.subject (id, label, fixture_id, is_test_data)
        VALUES (%(id)s, %(label)s, %(fixture_id)s, true)
        RETURNING id
        """,
        {"id": new_id, "label": label, "fixture_id": fixture_id},
    ).fetchone()
    return row["id"]


def list_subjects(conn) -> list[dict]:
    """GET /: every subject with the counts of its CURRENT conditions and
    medication requests (spec 6)."""
    return conn.execute(
        """
        SELECT s.id, s.label, s.fixture_id, s.created_at,
               (SELECT count(*) FROM phsa.current_condition c
                 WHERE c.subject_id = s.id) AS condition_count,
               (SELECT count(*) FROM phsa.current_medication_request m
                 WHERE m.subject_id = s.id) AS medication_count
        FROM phsa.subject s
        ORDER BY s.created_at
        """
    ).fetchall()


def get_subject(conn, subject_id: str) -> dict | None:
    return conn.execute(
        "SELECT id, label, fixture_id, created_at FROM phsa.subject WHERE id = %(id)s",
        {"id": subject_id},
    ).fetchone()


# ---------------------------------------------------------------------------
# subject_demographics (typed row; one source record + one typed row per
# demographics entry, spec 4.1)
# ---------------------------------------------------------------------------

def get_current_demographics(conn, subject_id: str) -> dict | None:
    """The subject's current (non-superseded) demographics, or None."""
    return conn.execute(
        """
        SELECT d.id, d.family_name, d.given_name, d.usual_name, d.sex,
               d.birth_year, d.birth_date, d.home_municipality,
               d.preferred_language, d.active, d.superseded_by,
               d.source_record_id, e.effective_class
        FROM phsa.current_subject_demographics d
        JOIN phsa.source_record_effective e ON e.source_record_id = d.source_record_id
        WHERE d.subject_id = %(subject_id)s AND d.superseded_by IS NULL
        ORDER BY d.id
        LIMIT 1
        """,
        {"subject_id": subject_id},
    ).fetchone()


def create_demographics(conn, subject_id: str, d: dict) -> dict:
    """Write recipe 4.1 for demographics: new source record (a Patient
    resource, spec 5.1) then the typed row pointing at it.

    Blank fields stay NULL (blank means blank, spec rule 2). Never commits.
    Returns the new typed row.
    """
    new_id = _new_uuid(conn)
    raw = fhir.patient_json(new_id, d)
    source_record_id = _insert_source_record(conn, "Patient", new_id, raw)
    row = conn.execute(
        """
        INSERT INTO phsa.subject_demographics (
          id, subject_id, source_record_id,
          family_name, given_name, usual_name, sex,
          birth_year, birth_date, home_municipality, preferred_language
        )
        VALUES (%(id)s, %(subject_id)s, %(source_record_id)s,
                %(family_name)s, %(given_name)s, %(usual_name)s, %(sex)s,
                %(birth_year)s, %(birth_date)s, %(home_municipality)s,
                %(preferred_language)s)
        RETURNING id, subject_id, source_record_id,
                  family_name, given_name, usual_name, sex,
                  birth_year, birth_date, home_municipality,
                  preferred_language, active, superseded_by
        """,
        {
            "id": new_id,
            "subject_id": subject_id,
            "source_record_id": source_record_id,
            **d,
            "birth_date": date.fromisoformat(d["birth_date"]) if d.get("birth_date") else None,
            "birth_year": int(d["birth_year"]) if d.get("birth_year") is not None else None,
        },
    ).fetchone()
    return row


def supersede_demographics(conn, old_id: str, subject_id: str, d: dict) -> dict | None:
    """Supersede recipe 4.2 for demographics:

    1. do 4.1 for the new version (new source record, new typed row, same
       subject_id);
    2. point the old row at it;
    3. if the old row was already superseded (0 rows matched) the caller
       rolls back and returns HTTP 409; this function then raises
       SupersedeConflict.

    Never commits.
    """
    new_id = _new_uuid(conn)
    raw = fhir.patient_json(new_id, d)
    source_record_id = _insert_source_record(conn, "Patient", new_id, raw)
    conn.execute(
        """
        INSERT INTO phsa.subject_demographics (
          id, subject_id, source_record_id,
          family_name, given_name, usual_name, sex,
          birth_year, birth_date, home_municipality, preferred_language
        )
        VALUES (%(id)s, %(subject_id)s, %(source_record_id)s,
                %(family_name)s, %(given_name)s, %(usual_name)s, %(sex)s,
                %(birth_year)s, %(birth_date)s, %(home_municipality)s,
                %(preferred_language)s)
        """,
        {
            "id": new_id,
            "subject_id": subject_id,
            "source_record_id": source_record_id,
            **d,
            "birth_date": date.fromisoformat(d["birth_date"]) if d.get("birth_date") else None,
            "birth_year": int(d["birth_year"]) if d.get("birth_year") is not None else None,
        },
    )
    # The single permitted UPDATE (append-only rule, spec 0.3).
    cur = conn.execute(
        """
        UPDATE phsa.subject_demographics
        SET superseded_by = %(new_id)s
        WHERE id = %(old_id)s AND superseded_by IS NULL
        """,
        {"new_id": new_id, "old_id": old_id},
    )
    if cur.rowcount == 0:
        raise SupersedeConflict(
            "that record was already superseded; nothing was changed"
        )
    return conn.execute(
        """
        SELECT id, subject_id, source_record_id,
               family_name, given_name, usual_name, sex,
               birth_year, birth_date, home_municipality,
               preferred_language, active, superseded_by
        FROM phsa.subject_demographics WHERE id = %(id)s
        """,
        {"id": new_id},
    ).fetchone()


class SupersedeConflict(Exception):
    """The old row was already superseded; the request becomes HTTP 409."""


# ---------------------------------------------------------------------------
# condition (typed row; one source record + one typed row per condition,
# spec 4.1). Supersede for conditions is M4.
# ---------------------------------------------------------------------------

def list_current_conditions(conn, subject_id: str) -> list[dict]:
    """The subject's current (non-superseded) conditions, read from the
    phsa.current_condition view (spec: all "current" screens read these
    views). Includes the view's effective_class."""
    return conn.execute(
        """
        SELECT c.id, c.code_text, c.code_system, c.code,
               c.clinical_status, c.verification_status,
               c.onset_text, c.onset_date, c.note,
               c.source_record_id, e.effective_class
        FROM phsa.current_condition c
        JOIN phsa.source_record_effective e ON e.source_record_id = c.source_record_id
        WHERE c.subject_id = %(subject_id)s AND c.superseded_by IS NULL
        ORDER BY c.id
        """,
        {"subject_id": subject_id},
    ).fetchall()


def create_condition(conn, subject_id: str, d: dict) -> dict:
    """Write recipe 4.1 for a condition: new source record (a Condition
    resource, spec 5.2) then the typed row pointing at it.

    Blank fields stay NULL (blank means blank, spec rule 2). Never commits.
    Returns the new typed row.

    Raises ValueError if both onset values are present: a condition's onset
    is given as text OR as a date, never both (spec 6.1, v0.5). The schema
    has no CHECK between the two columns, so the guard lives here — before
    any SQL runs — to make no caller able to bypass the form rule.
    """
    if d.get("onset_text") and d.get("onset_date"):
        raise ValueError(
            "onset is given as text OR as a date, never both "
            "(spec 6.1: onset_text and onset_date are never both given)"
        )
    new_id = _new_uuid(conn)
    raw = fhir.condition_json(new_id, subject_id, d)
    source_record_id = _insert_source_record(conn, "Condition", new_id, raw)
    row = conn.execute(
        """
        INSERT INTO phsa.condition (
          id, subject_id, source_record_id,
          code_text, code_system, code,
          clinical_status, verification_status,
          onset_text, onset_date, note
        )
        VALUES (%(id)s, %(subject_id)s, %(source_record_id)s,
                %(code_text)s, %(code_system)s, %(code)s,
                %(clinical_status)s, %(verification_status)s,
                %(onset_text)s, %(onset_date)s, %(note)s)
        RETURNING id, subject_id, source_record_id,
                  code_text, code_system, code,
                  clinical_status, verification_status,
                  onset_text, onset_date, note, superseded_by
        """,
        {
            "id": new_id,
            "subject_id": subject_id,
            "source_record_id": source_record_id,
            "code_text": d["code_text"],
            "code_system": d.get("code_system"),
            "code": d.get("code"),
            "clinical_status": d["clinical_status"],
            "verification_status": d.get("verification_status"),
            "onset_text": d.get("onset_text"),
            "onset_date": date.fromisoformat(d["onset_date"]) if d.get("onset_date") else None,
            "note": d.get("note"),
        },
    ).fetchone()
    return row


# ---------------------------------------------------------------------------
# medication_request (typed row; one source record + one typed row per
# medication request, spec 4.1). Supersede for medication requests is M4.
# ---------------------------------------------------------------------------

def list_current_medication_requests(conn, subject_id: str) -> list[dict]:
    """The subject's current (non-superseded) medication requests, read from
    the phsa.current_medication_request view (spec: all "current" screens
    read these views). Includes the view's effective_class."""
    return conn.execute(
        """
        SELECT m.id, m.medication_text, m.medication_code_system,
               m.medication_code, m.status, m.intent, m.authored_on,
               m.sig_text, m.dose_value, m.dose_unit, m.dose_unit_code,
               m.route_text, m.frequency, m.period, m.period_unit,
               m.timing_when, m.note,
               m.source_record_id, e.effective_class
        FROM phsa.current_medication_request m
        JOIN phsa.source_record_effective e ON e.source_record_id = m.source_record_id
        WHERE m.subject_id = %(subject_id)s AND m.superseded_by IS NULL
        ORDER BY m.id
        """,
        {"subject_id": subject_id},
    ).fetchall()


def create_medication_request(conn, subject_id: str, d: dict) -> dict:
    """Write recipe 4.1 for a medication request: new source record (a
    MedicationRequest resource, spec 5.3) then the typed row pointing at it.

    Blank fields stay NULL (blank means blank, spec rule 2). `intent` is
    left to the schema default ('order') and `safety_critical` to its
    default (true): neither is on the form. Never commits. Returns the new
    typed row.
    """
    new_id = _new_uuid(conn)
    raw = fhir.medication_request_json(new_id, subject_id, d)
    source_record_id = _insert_source_record(conn, "MedicationRequest", new_id, raw)
    row = conn.execute(
        """
        INSERT INTO phsa.medication_request (
          id, subject_id, source_record_id,
          medication_text, medication_code_system, medication_code,
          status, authored_on, sig_text,
          dose_value, dose_unit, dose_unit_code,
          route_text, frequency, period, period_unit, timing_when, note
        )
        VALUES (%(id)s, %(subject_id)s, %(source_record_id)s,
                %(medication_text)s, %(medication_code_system)s, %(medication_code)s,
                %(status)s, %(authored_on)s, %(sig_text)s,
                %(dose_value)s, %(dose_unit)s, %(dose_unit_code)s,
                %(route_text)s, %(frequency)s, %(period)s, %(period_unit)s,
                %(timing_when)s, %(note)s)
        RETURNING id, subject_id, source_record_id,
                  medication_text, medication_code_system, medication_code,
                  status, intent, authored_on, sig_text,
                  dose_value, dose_unit, dose_unit_code,
                  route_text, frequency, period, period_unit, timing_when,
                  note, safety_critical, superseded_by
        """,
        {
            "id": new_id,
            "subject_id": subject_id,
            "source_record_id": source_record_id,
            "medication_text": d["medication_text"],
            "medication_code_system": d.get("medication_code_system"),
            "medication_code": d.get("medication_code"),
            "status": d["status"],
            "authored_on": date.fromisoformat(d["authored_on"]) if d.get("authored_on") else None,
            "sig_text": d.get("sig_text"),
            "dose_value": Decimal(d["dose_value"]) if d.get("dose_value") is not None else None,
            "dose_unit": d.get("dose_unit"),
            "dose_unit_code": d.get("dose_unit_code"),
            "route_text": d.get("route_text"),
            "frequency": d.get("frequency"),
            "period": Decimal(d["period"]) if d.get("period") is not None else None,
            "period_unit": d.get("period_unit"),
            "timing_when": d["timing_when"] or None,  # blank means absent
            "note": d.get("note"),
        },
    ).fetchone()
    return row


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def get_source_record(conn, source_record_id: str) -> dict | None:
    return conn.execute(
        """
        SELECT id, source_system, source_resource_type, source_resource_id,
               resource_raw, content_sha256, extraction_method,
               epistemic_class, is_test_data, ingested_at
        FROM phsa.source_record WHERE id = %(id)s
        """,
        {"id": source_record_id},
    ).fetchone()


