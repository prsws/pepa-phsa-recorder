"""Post-M3 fix: decimal dose and period values are stored exactly.

Before this fix a dose typed as 0.4 was parsed with float() and stored as
0.4000000000000000222044604925031308... (the float's binary expansion).
Spec rule 2: never normalize a value. These tests pin the rule: the typed
row and the stored document both hold the digits that were entered.
"""

from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app import queries, validation

ORIGIN = {"Origin": "http://localhost"}

BASE_FORM = {
    "medication_text": "Tamsulosin 0.4 mg",
    "medication_code_system": "", "medication_code": "",
    "status": "active", "authored_on": "",
    "sig_text": "0.4 mg nightly",
    "dose_value": "0.4", "dose_unit": "mg", "dose_unit_code": "mg",
    "route_text": "", "frequency": "1", "period": "1", "period_unit": "d",
    "timing_when": "NIGHT", "note": "",
}

STORED = """
    SELECT m.dose_value, m.period,
           s.resource_raw #>> '{dosageInstruction,0,doseAndRate,0,doseQuantity,value}' AS doc_dose,
           s.resource_raw #>> '{dosageInstruction,0,timing,repeat,period}' AS doc_period
    FROM phsa.medication_request m
    JOIN phsa.source_record s ON s.id = m.source_record_id
    WHERE m.subject_id = %s
    ORDER BY m.id DESC LIMIT 1
"""


def _form(**changes) -> dict:
    return {**BASE_FORM, **changes}


def _counts(conn) -> tuple:
    return tuple(
        conn.execute(f"SELECT count(*) AS n FROM phsa.{t}").fetchone()["n"]
        for t in ("source_record", "medication_request")
    )


def _post(client_tx, subject_id, form):
    return client_tx.post(f"/subjects/{subject_id}/medications/new",
                          data=form, headers=ORIGIN)


@pytest.mark.parametrize("typed", ["0.4", "0.125", "2.5", "22", "81", "500"])
def test_dose_is_stored_with_the_digits_that_were_typed(client_tx, tx_conn, typed):
    subject_id = queries.create_subject(tx_conn, "Decimal dose person", None)
    r = _post(client_tx, subject_id, _form(dose_value=typed))
    assert r.status_code == 302, r.text[:200]
    row = tx_conn.execute(STORED, (subject_id,)).fetchone()
    assert row["dose_value"] == Decimal(typed)
    assert str(row["dose_value"]) == typed   # the typed row: exact digits
    assert row["doc_dose"] == typed          # the stored document: same digits


@pytest.mark.parametrize("typed", ["0.5", "1", "1.5", "12"])
def test_period_is_stored_with_the_digits_that_were_typed(client_tx, tx_conn, typed):
    subject_id = queries.create_subject(tx_conn, "Decimal period person", None)
    r = _post(client_tx, subject_id, _form(period=typed))
    assert r.status_code == 302, r.text[:200]
    row = tx_conn.execute(STORED, (subject_id,)).fetchone()
    assert row["period"] == Decimal(typed)
    assert str(row["period"]) == typed
    assert row["doc_period"] == typed


@pytest.mark.parametrize("typed", [
    "abc", "NaN", "Infinity", "-Infinity", "-1", "0",
    "1.2345678901234567",   # 17 significant digits: more than the 15 allowed
])
def test_a_dose_that_is_not_a_positive_number_is_422(client_tx, tx_conn, typed):
    subject_id = queries.create_subject(tx_conn, "Bad dose person", None)
    before = _counts(tx_conn)
    r = _post(client_tx, subject_id, _form(dose_value=typed))
    assert r.status_code == 422
    assert "dose_value must be a positive number" in r.text
    assert _counts(tx_conn) == before


@pytest.mark.parametrize("typed", ["abc", "NaN", "Infinity", "-2", "0"])
def test_a_period_that_is_not_a_positive_number_is_422(client_tx, tx_conn, typed):
    subject_id = queries.create_subject(tx_conn, "Bad period person", None)
    before = _counts(tx_conn)
    r = _post(client_tx, subject_id, _form(period=typed))
    assert r.status_code == 422
    assert "period must be a positive number" in r.text
    assert _counts(tx_conn) == before


def test_validation_returns_exact_decimals():
    fields, errors = validation.validate_medication_request(
        MultiDict(_form(dose_value="0.4", period="0.5")))
    assert errors == []
    assert isinstance(fields["dose_value"], Decimal)
    assert fields["dose_value"] == Decimal("0.4")
    assert isinstance(fields["period"], Decimal)
    assert fields["period"] == Decimal("0.5")


def test_data_layer_stores_a_float_as_the_digits_it_prints(conn):
    """A caller that hands the data layer a float (a script, a later seed)
    must not smuggle the float's binary expansion into the row."""
    subject_id = queries.create_subject(conn, "Float caller person", None)
    fields, errors = validation.validate_medication_request(MultiDict(_form()))
    assert errors == []
    queries.create_medication_request(
        conn, subject_id, {**fields, "dose_value": 0.4, "period": 0.5})
    row = conn.execute(STORED, (subject_id,)).fetchone()
    assert row["dose_value"] == Decimal("0.4")
    assert str(row["dose_value"]) == "0.4"
    assert row["period"] == Decimal("0.5")
    assert row["doc_dose"] == "0.4"
    assert row["doc_period"] == "0.5"
