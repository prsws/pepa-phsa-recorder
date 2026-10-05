"""M5a: the SUBJ-01 seed loader. Dry run only; nothing is ever kept."""
from decimal import Decimal

from scripts import seed_subj01

NOT_LOADED_MARKERS = (
    "orthostatic", "confus", "dose cap", "maximum", "clinical note",
)


def _blank(value):
    """The seed file writes a blank as "" or []; the database stores NULL."""
    return None if value in ("", []) else value


def _load(conn):
    """Load the fixture on this connection; return (data, counts, subject id)."""
    data = seed_subj01.read_seed_file()
    counts = seed_subj01.load_subj01(conn, data)
    row = conn.execute(
        "SELECT id FROM phsa.subject WHERE fixture_id = 'SUBJ-01'"
    ).fetchone()
    return data, counts, row["id"]


def _source_rows(conn, subject_id):
    """Every source record that belongs to this subject's typed rows."""
    return conn.execute(
        """
        SELECT sr.source_resource_type AS kind, sr.is_test_data,
               sr.extraction_method, sr.resource_raw::text AS raw_text
        FROM phsa.source_record sr
        WHERE sr.id IN (
            SELECT source_record_id FROM phsa.subject_demographics
            WHERE subject_id = %(s)s
            UNION ALL
            SELECT source_record_id FROM phsa.condition
            WHERE subject_id = %(s)s
            UNION ALL
            SELECT source_record_id FROM phsa.medication_request
            WHERE subject_id = %(s)s
        )
        """,
        {"s": subject_id},
    ).fetchall()


def test_seed_file_is_as_specified():
    data = seed_subj01.read_seed_file()
    assert data["subject"]["label"] == "Efra\u00edn Col\u00f3n Berr\u00edos (SUBJ-01, synthetic)"
    assert data["subject"]["fixture_id"] == "SUBJ-01"
    assert data["demographics"]["family_name"] == "Col\u00f3n Berr\u00edos"
    assert data["demographics"]["given_name"] == "Efra\u00edn"
    assert data["demographics"]["usual_name"] == "Don Efra"
    assert [c["code_text"] for c in data["conditions"]] == [
        "Type 2 diabetes", "Diabetic peripheral neuropathy", "Hypertension",
        "CKD stage 3a", "Diabetic retinopathy, mild", "BPH",
        "Hearing loss, high-frequency",
    ]
    assert [m["dose_value"] for m in data["medication_requests"]] == [
        "500", "22", "50", "5", "20", "0.4", "81",
    ]
    for m in data["medication_requests"]:
        assert isinstance(m["dose_value"], str)
        assert isinstance(m["frequency"], str)
        assert isinstance(m["period"], str)


def test_load_counts(tx_conn):
    data, counts, sid = _load(tx_conn)
    assert counts == {
        "already_seeded": False, "subject": 1, "demographics": 1,
        "conditions": 7, "medication_requests": 7, "source_records": 15,
    }
    row = tx_conn.execute(
        "SELECT * FROM phsa.subject WHERE id = %(s)s", {"s": sid}
    ).fetchone()
    assert row["label"] == data["subject"]["label"]
    assert row["fixture_id"] == "SUBJ-01"
    assert row["is_test_data"] is True
    d = tx_conn.execute(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE effective_class = 'observation') AS obs
        FROM phsa.current_subject_demographics WHERE subject_id = %(s)s
        """,
        {"s": sid},
    ).fetchone()
    assert d["n"] == 1 and d["obs"] == 1
    c = tx_conn.execute(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE effective_class = 'observation') AS obs
        FROM phsa.current_condition WHERE subject_id = %(s)s
        """,
        {"s": sid},
    ).fetchone()
    assert c["n"] == 7 and c["obs"] == 7
    m = tx_conn.execute(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE effective_class = 'observation') AS obs
        FROM phsa.current_medication_request WHERE subject_id = %(s)s
        """,
        {"s": sid},
    ).fetchone()
    assert m["n"] == 7 and m["obs"] == 7


def test_source_records_are_test_data(tx_conn):
    _, _, sid = _load(tx_conn)
    rows = _source_rows(tx_conn, sid)
    assert len(rows) == 15
    for row in rows:
        assert row["is_test_data"] is True
        assert row["extraction_method"] == "manual_entry"
    by_kind = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    assert by_kind == {
        "Patient": 1, "Condition": 7, "MedicationRequest": 7,
    }


def test_loaded_demographics_match_file(tx_conn):
    data, _, sid = _load(tx_conn)
    row = tx_conn.execute(
        "SELECT * FROM phsa.current_subject_demographics WHERE subject_id = %(s)s",
        {"s": sid},
    ).fetchone()
    for field in ("family_name", "given_name", "usual_name", "sex",
                  "home_municipality", "preferred_language"):
        assert row[field] == data["demographics"][field]
    assert row["birth_year"] is None
    assert row["birth_date"] is None


def test_loaded_conditions_match_file(tx_conn):
    data, _, sid = _load(tx_conn)
    rows = {
        r["code_text"]: r
        for r in tx_conn.execute(
            "SELECT * FROM phsa.current_condition WHERE subject_id = %(s)s",
            {"s": sid},
        ).fetchall()
    }
    assert len(rows) == 7
    for condition in data["conditions"]:
        row = rows[condition["code_text"]]
        assert row["clinical_status"] == "active"
        assert row["onset_text"] == _blank(condition["onset_text"])
        assert row["note"] == _blank(condition["note"])
        assert row["verification_status"] is None
        assert row["code_system"] is None
        assert row["code"] is None
        assert row["onset_date"] is None


def test_loaded_medications_match_file(tx_conn):
    data, _, sid = _load(tx_conn)
    rows = {
        r["medication_text"]: r
        for r in tx_conn.execute(
            "SELECT * FROM phsa.current_medication_request"
            " WHERE subject_id = %(s)s",
            {"s": sid},
        ).fetchall()
    }
    assert len(rows) == 7
    for medication in data["medication_requests"]:
        row = rows[medication["medication_text"]]
        assert row["status"] == "active"
        assert row["intent"] == "order"
        assert row["dose_value"] == Decimal(medication["dose_value"])
        assert row["dose_unit"] == medication["dose_unit"]
        assert row["dose_unit_code"] == _blank(medication["dose_unit_code"])
        assert row["frequency"] == int(medication["frequency"])
        assert row["period"] == Decimal(medication["period"])
        assert row["period_unit"] == medication["period_unit"]
        assert row["timing_when"] == _blank(medication["timing_when"])
        assert row["sig_text"] == medication["sig_text"]
        assert row["note"] == _blank(medication["note"])
        assert row["authored_on"] is None
        assert row["route_text"] is None
        assert row["medication_code_system"] is None
        assert row["medication_code"] is None
    row = rows["Tamsulosin 0.4 mg"]
    assert row["dose_value"] == Decimal("0.4")
    assert str(row["dose_value"]) == "0.4"


def test_second_load_is_already_seeded(tx_conn):
    data, counts, sid = _load(tx_conn)
    assert counts["already_seeded"] is False
    second = seed_subj01.load_subj01(tx_conn, data)
    assert second == {
        "already_seeded": True, "subject": 0, "demographics": 0,
        "conditions": 0, "medication_requests": 0, "source_records": 0,
    }
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.subject WHERE fixture_id = 'SUBJ-01'"
    ).fetchone()["n"]
    assert n == 1
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %(s)s",
        {"s": sid},
    ).fetchone()["n"]
    assert n == 7
    assert len(_source_rows(tx_conn, sid)) == 15


def test_nothing_else_is_loaded(tx_conn):
    _, _, sid = _load(tx_conn)
    rows = _source_rows(tx_conn, sid)
    seed_text = seed_subj01.SEED_FILE.read_text(encoding="utf-8").lower()
    for marker in NOT_LOADED_MARKERS:
        assert marker not in seed_text
        for row in rows:
            assert marker not in row["raw_text"].lower()
    med_rows = [r for r in rows if r["kind"] == "MedicationRequest"]
    assert sum(1 for r in med_rows if '"note"' in r["raw_text"]) == 1
    cond_rows = [r for r in rows if r["kind"] == "Condition"]
    assert sum(1 for r in cond_rows if '"note"' in r["raw_text"]) == 7


def test_dry_run_main_keeps_nothing(config, conn, capsys):
    rc = seed_subj01.main([], config=config)
    assert rc == 0
    out = capsys.readouterr().out
    for line in ("DRY RUN", "subject: 1", "demographics: 1", "conditions: 7",
                 "medication requests: 7", "source records: 15",
                 "rolled back"):
        assert line in out
    n = conn.execute(
        "SELECT count(*) AS n FROM phsa.subject WHERE fixture_id = 'SUBJ-01'"
    ).fetchone()["n"]
    assert n == 0
