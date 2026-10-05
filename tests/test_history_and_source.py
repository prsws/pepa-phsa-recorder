"""M4e: the two read-only pages.

GET /subjects/<id>/history: every version of every typed row for the
subject, oldest first — including superseded and entered-in-error
versions. GET /source-records/<id>: the archived record with its
effective class, the content hash as hex, and the raw resource
pretty-printed.

One subject is built through the app: demographics created then
superseded once (two versions); one condition created, superseded once,
then marked entered-in-error (three versions); one medication request
(one version). Six versions in all. Each test reuses the test's
transaction (conftest: `client_tx`, `tx_conn`); nothing commits.
"""

import uuid

import psycopg
import pytest

from app import queries


SUBJECT_URL = "/subjects/{sid}"
HISTORY_URL = "/subjects/{sid}/history"
SOURCE_URL = "/source-records/{rid}"
PLAIN_PAGE = "something went wrong"
FORBIDDEN_STRINGS = ("boom", "psycopg", "Traceback")
ORIGIN = {"Origin": "http://localhost"}

HISTORY_KEYS = {"kind", "id", "source_record_id", "ingested_at",
                "effective_class", "summary", "status", "superseded_by"}

_DEMO = {"family_name": "Alvarado", "given_name": "Rosa", "sex": "female",
         "home_municipality": "Herrera", "preferred_language": "es",
         "birth_year": "1965", "birth_date": "1965-03-14"}
DEMO_A = {**_DEMO, "usual_name": "Rosa Alvarado"}
DEMO_B = {**_DEMO, "usual_name": "Rosa M. Alvarado"}

COND_1 = {"code_text": "Kilo condition", "code_system": "", "code": "",
          "clinical_status": "active", "verification_status": "",
          "onset_text": "", "onset_date": "", "note": ""}
COND_2 = {"code_text": "Kilo condition, reviewed", "code_system": "",
          "code": "", "clinical_status": "inactive",
          "verification_status": "confirmed", "onset_text": "childhood",
          "onset_date": "", "note": ""}

MED_1 = {"medication_text": "Tango medication",
         "medication_code_system": "", "medication_code": "",
         "status": "active", "authored_on": "",
         "sig_text": "once a day", "dose_value": "", "dose_unit": "",
         "dose_unit_code": "", "route_text": "", "frequency": "",
         "period": "", "period_unit": "", "timing_when": [], "note": ""}


def _post(client_tx, path, data=None):
    r = client_tx.post(path, data=data, headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    return r


def _latest(tx_conn, table: str, subject_id) -> str:
    """The newest non-superseded row of a typed table for that subject
    (ids are uuidv7, so the newest sorts last)."""
    row = tx_conn.execute(
        f"SELECT id FROM phsa.{table} WHERE subject_id = %s "
        "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1",
        (subject_id,),
    ).fetchone()
    return str(row["id"])


def _condition_ids(tx_conn, subject_id):
    """All condition versions for the subject, oldest first (the same
    order the history list uses: ingested_at, then id)."""
    rows = tx_conn.execute(
        "SELECT c.id FROM phsa.condition c "
        "JOIN phsa.source_record s ON s.id = c.source_record_id "
        "WHERE c.subject_id = %s ORDER BY s.ingested_at, c.id",
        (subject_id,),
    ).fetchall()
    return [str(r["id"]) for r in rows]


def _build_subject(client_tx, tx_conn):
    """Build the whole test subject through the app and return
    (subject_id, demo_ids[2], cond_ids[3], med_ids[1])."""
    _post(client_tx, "/subjects/new",
          {"label": "History Test Subject", "fixture_id": "SUBJ-9001"})
    sid = str(tx_conn.execute(
        "SELECT id FROM phsa.subject WHERE label = 'History Test Subject' "
        "ORDER BY id DESC LIMIT 1").fetchone()["id"])
    # demographics: create, then supersede once (two versions)
    _post(client_tx, f"/subjects/{sid}/demographics", DEMO_A)
    demo_a = _latest(tx_conn, "subject_demographics", sid)
    _post(client_tx, f"/subjects/{sid}/demographics", DEMO_B)
    demo_b = _latest(tx_conn, "subject_demographics", sid)
    # condition: create, supersede, mark entered-in-error (three versions)
    _post(client_tx, f"/subjects/{sid}/conditions/new", COND_1)
    cond_1 = _latest(tx_conn, "condition", sid)
    r = client_tx.post(f"/conditions/{cond_1}/supersede",
                       data=COND_2, headers=ORIGIN)
    assert r.status_code == 302, f"expected 302, got {r.status_code}"
    _post(client_tx, f"/conditions/{_latest(tx_conn, 'condition', sid)}/mark-error")
    # medication request: one version
    _post(client_tx, f"/subjects/{sid}/medications/new", MED_1)
    med_1 = _latest(tx_conn, "medication_request", sid)
    return sid, [demo_a, demo_b], _condition_ids(tx_conn, sid), [med_1]


def test_history_returns_six_dicts_with_exact_keys(client_tx, tx_conn):
    """list_subject_history returns one dict per version (six in all),
    each with exactly the eight keys the spec lists."""
    sid, _demo, _cond, _med = _build_subject(client_tx, tx_conn)
    history = queries.list_subject_history(tx_conn, sid)
    assert len(history) == 6, f"expected 6 versions, got {len(history)}"
    for row in history:
        assert set(row.keys()) == HISTORY_KEYS, \
            f"keys differ: {sorted(row.keys())}"
    kinds = sorted(row["kind"] for row in history)
    assert kinds == ["condition", "condition", "condition",
                     "demographics", "demographics",
                     "medication request"]


def test_history_sorted_by_ingested_at_and_id(client_tx, tx_conn):
    """The list is sorted by (ingested_at, id), and the three condition
    versions appear in the order they were created."""
    sid, _demo, cond_ids, _med = _build_subject(client_tx, tx_conn)
    history = queries.list_subject_history(tx_conn, sid)
    key = [(row["ingested_at"], row["id"]) for row in history]
    assert key == sorted(key), "history is not sorted by (ingested_at, id)"
    cond_rows = [row for row in history if row["kind"] == "condition"]
    assert [str(row["id"]) for row in cond_rows] == cond_ids


def test_history_superseded_by_chain(client_tx, tx_conn):
    """Exactly three entries (one of each kind) are current
    (superseded_by None); each of the other three points at the next
    version of the same record."""
    sid, demo_ids, cond_ids, med_ids = _build_subject(client_tx, tx_conn)
    history = queries.list_subject_history(tx_conn, sid)
    by_id = {str(row["id"]): row for row in history}
    current = [row for row in history if row["superseded_by"] is None]
    assert len(current) == 3, \
        f"expected 3 current rows, got {len(current)}"
    assert {row["kind"] for row in current} == \
        {"demographics", "condition", "medication request"}
    assert {str(row["id"]) for row in current} == \
        {demo_ids[1], cond_ids[2], med_ids[0]}
    superseded = [row for row in history
                  if row["superseded_by"] is not None]
    assert len(superseded) == 3
    # every pointer lands on the next version of the same kind
    expected_pointers = {
        demo_ids[0]: demo_ids[1],
        cond_ids[0]: cond_ids[1],
        cond_ids[1]: cond_ids[2],
    }
    for row in superseded:
        assert str(row["superseded_by"]) in by_id
        assert by_id[str(row["superseded_by"])]["kind"] == row["kind"]
    for old_id, new_id in expected_pointers.items():
        assert str(by_id[old_id]["superseded_by"]) == new_id


def test_history_page(client_tx, tx_conn):
    """GET the history page: 200; it shows every version's text, the
    'superseded by' marker, and the entered-in-error version's status."""
    sid, demo, cond_ids, _med = _build_subject(client_tx, tx_conn)
    r = client_tx.get(HISTORY_URL.format(sid=sid))
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    text = r.text
    for version_text in ("Rosa Alvarado", "Rosa M. Alvarado",
                         "Kilo condition", "Kilo condition, reviewed"):
        assert version_text in text, f"{version_text!r} not on the page"
    assert "Tango medication" in text
    assert "superseded by" in text
    assert "inactive / entered-in-error" in text
    # every condition version links to its own source record
    rows = tx_conn.execute(
        "SELECT DISTINCT source_record_id FROM phsa.condition "
        "WHERE subject_id = %s",
        (sid,),
    ).fetchall()
    for row in rows:
        assert f"href=\"{SOURCE_URL.format(rid=row['source_record_id'])}\"" \
            in text


def test_history_page_random_subject_404(client_tx, tx_conn):
    r = client_tx.get(HISTORY_URL.format(sid=uuid.uuid4()))
    assert r.status_code == 404, f"expected 404, got {r.status_code}"


def test_subject_page_links_to_history(client_tx, tx_conn):
    sid, _demo, _cond, _med = _build_subject(client_tx, tx_conn)
    r = client_tx.get(SUBJECT_URL.format(sid=sid))
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    assert f"href=\"/subjects/{sid}/history\"" in r.text
    assert "History" in r.text


def test_source_record_page(client_tx, tx_conn):
    """GET the source-record page for a condition's first version: 200; it
    shows the source system, the resource type, the HTEST security tag, the
    version's text, its effective class, and the content hash as hex."""
    sid, _demo, cond_ids, _med = _build_subject(client_tx, tx_conn)
    first_cond_id = cond_ids[0]
    row = tx_conn.execute(
        "SELECT source_record_id FROM phsa.condition WHERE id = %s",
        (first_cond_id,),
    ).fetchone()
    rid = str(row["source_record_id"])
    expected_hex = tx_conn.execute(
        "SELECT encode(content_sha256, 'hex') AS h FROM phsa.source_record "
        "WHERE id = %s",
        (rid,),
    ).fetchone()["h"]
    r = client_tx.get(SOURCE_URL.format(rid=rid))
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    text = r.text
    assert "phsa-crs-manual" in text, "source system not on the page"
    assert "Condition" in text, "resource type not on the page"
    assert "HTEST" in text, "HTEST security tag not on the page"
    assert "Kilo condition" in text, \
        "the condition version's text is not on the page"
    assert "observation" in text, "effective class not on the page"
    assert expected_hex in text, \
        f"content hash hex {expected_hex!r} not on the page"


def test_source_record_page_random_id_404(client_tx, tx_conn):
    r = client_tx.get(SOURCE_URL.format(rid=uuid.uuid4()))
    assert r.status_code == 404, f"expected 404, got {r.status_code}"


def test_history_page_database_error_is_plain_500(client_tx, tx_conn,
                                                   monkeypatch):
    """A database error in list_subject_history gives the plain 500 page:
    no 'boom', no 'psycopg', no 'Traceback' in the body."""
    sid, _demo, _cond, _med = _build_subject(client_tx, tx_conn)
    monkeypatch.setattr(
        queries, "list_subject_history",
        lambda conn, sid: (_ for _ in ()).throw(
            psycopg.OperationalError("boom")))
    r = client_tx.get(HISTORY_URL.format(sid=sid))
    assert r.status_code == 500, f"expected 500, got {r.status_code}"
    assert PLAIN_PAGE in r.text
    for needle in FORBIDDEN_STRINGS:
        assert needle not in r.text, f"error page leaks {needle!r}"


def test_source_record_page_database_error_is_plain_500(client_tx, tx_conn,
                                                        monkeypatch):
    """A database error in get_source_record gives the plain 500 page.
    The source_record_id is read BEFORE the patch is applied."""
    sid, _demo, cond_ids, _med = _build_subject(client_tx, tx_conn)
    first_cond_id = cond_ids[0]
    row = tx_conn.execute(
        "SELECT source_record_id FROM phsa.condition WHERE id = %s",
        (first_cond_id,),
    ).fetchone()
    rid = str(row["source_record_id"])
    monkeypatch.setattr(
        queries, "get_source_record",
        lambda conn, rid: (_ for _ in ()).throw(
            psycopg.OperationalError("boom")))
    r = client_tx.get(SOURCE_URL.format(rid=rid))
    assert r.status_code == 500, f"expected 500, got {r.status_code}"
    assert PLAIN_PAGE in r.text
    for needle in FORBIDDEN_STRINGS:
        assert needle not in r.text, f"error page leaks {needle!r}"


def test_pool_put_matches_get_on_read_pages(client_tx, tx_conn, monkeypatch):
    """The pool's get and put must balance on every way out of the two
    read-only routes (the same counting technique as the pool test in
    tests/test_condition_supersede.py). For each of the four requests:
    get was called at least once and put calls equal get calls."""
    sid, _demo, cond_ids, _med = _build_subject(client_tx, tx_conn)
    first_cond_id = cond_ids[0]
    row = tx_conn.execute(
        "SELECT source_record_id FROM phsa.condition WHERE id = %s",
        (first_cond_id,),
    ).fetchone()
    rid = str(row["source_record_id"])

    pool = client_tx.application.extensions["phsa_pool"]
    real_get, real_put = pool.get, pool.put
    calls = {"get": 0, "put": 0}

    def counting_get():
        calls["get"] += 1
        return real_get()

    def counting_put(conn):
        calls["put"] += 1
        return real_put(conn)

    monkeypatch.setattr(pool, "get", counting_get)
    monkeypatch.setattr(pool, "put", counting_put)

    def _req(name, expected, fn):
        calls["get"] = 0
        calls["put"] = 0
        r = fn()
        assert r.status_code == expected, \
            f"{name}: expected {expected}, got {r.status_code}"
        assert calls["get"] >= 1, f"{name}: get never called"
        assert calls["put"] == calls["get"], \
            f"{name}: put ({calls['put']}) != get ({calls['get']})"

    _req("history 200", 200,
         lambda: client_tx.get(HISTORY_URL.format(sid=sid)))
    _req("history 404", 404,
         lambda: client_tx.get(HISTORY_URL.format(sid=uuid.uuid4())))
    _req("source-record 200", 200,
         lambda: client_tx.get(SOURCE_URL.format(rid=rid)))
    _req("source-record 404", 404,
         lambda: client_tx.get(SOURCE_URL.format(rid=uuid.uuid4())))
