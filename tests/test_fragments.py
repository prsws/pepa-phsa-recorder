"""M6a: htmx fragment responses (spec section 7). Server side only.

When a request carries the header HX-Request: true, the three interactions
(supersede, mark entered-in-error, add) answer with one table row and none of
the page around it. Without the header every route answers as before.
"""
import uuid

HX = {"HX-Request": "true"}


def _subject(client_tx, tx_conn):
    fixture = "FRAG-" + uuid.uuid4().hex[:8]
    client_tx.post("/subjects/new", data={
        "label": "Fragment test " + fixture, "fixture_id": fixture})
    row = tx_conn.execute(
        "SELECT id FROM phsa.subject WHERE fixture_id = %s", (fixture,)
    ).fetchone()
    return str(row["id"])


def _condition(client_tx, tx_conn, sid, text):
    client_tx.post(f"/subjects/{sid}/conditions/new", data={
        "code_text": text, "clinical_status": "active"})
    row = tx_conn.execute(
        "SELECT id FROM phsa.current_condition"
        " WHERE subject_id = %s AND code_text = %s", (sid, text)
    ).fetchone()
    return str(row["id"])


def _medication(client_tx, tx_conn, sid, text):
    client_tx.post(f"/subjects/{sid}/medications/new", data={
        "medication_text": text, "status": "active"})
    row = tx_conn.execute(
        "SELECT id FROM phsa.current_medication_request"
        " WHERE subject_id = %s AND medication_text = %s", (sid, text)
    ).fetchone()
    return str(row["id"])


def _newer(tx_conn, table, old_id):
    """The id of the version that superseded old_id."""
    row = tx_conn.execute(
        f"SELECT superseded_by FROM phsa.{table} WHERE id = %s", (old_id,)
    ).fetchone()
    return str(row["superseded_by"])


def _body(response):
    return response.get_data(as_text=True)


def _is_piece(body):
    """A fragment is table rows only, with none of the page around it."""
    return (body.lstrip().startswith("<tr id=") and "<html" not in body
            and "TEST DATA ONLY" not in body and "<nav>" not in body)


def test_full_page_rows_carry_ids(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag page condition")
    mid = _medication(client_tx, tx_conn, sid, "Frag page medication")
    page = _body(client_tx.get(f"/subjects/{sid}"))
    assert "<html" in page and "TEST DATA ONLY" in page
    assert f'<tr id="condition-{cid}">' in page
    assert f'<tr id="medication-{mid}">' in page


def test_condition_supersede_form_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag form condition")
    r = client_tx.get(f"/conditions/{cid}/supersede", headers=HX)
    body = _body(r)
    assert r.status_code == 200 and _is_piece(body)
    assert body.lstrip().startswith(
        f'<tr id="condition-{cid}"><td colspan="6">')
    assert body.rstrip().endswith("</td></tr>")
    assert f'action="/conditions/{cid}/supersede"' in body
    assert "Frag form condition" in body
    assert "<html" in _body(client_tx.get(f"/conditions/{cid}/supersede"))


def test_condition_supersede_invalid_is_422_form_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag invalid condition")
    r = client_tx.post(f"/conditions/{cid}/supersede", headers=HX, data={
        "code_text": "", "clinical_status": "active"})
    body = _body(r)
    assert r.status_code == 422 and _is_piece(body)
    assert body.lstrip().startswith(f'<tr id="condition-{cid}">')
    assert "code_text is required" in body
    assert _newer(tx_conn, "condition", cid) == "None"


def test_condition_supersede_ok_is_new_row_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag old condition")
    r = client_tx.post(f"/conditions/{cid}/supersede", headers=HX, data={
        "code_text": "Frag new condition", "clinical_status": "active"})
    body = _body(r)
    new = _newer(tx_conn, "condition", cid)
    assert new != "None"
    assert r.status_code == 200 and _is_piece(body)
    assert body.lstrip().startswith(f'<tr id="condition-{new}">')
    assert body.count("<tr id=") == 1
    assert "Frag new condition" in body and "Frag old condition" not in body
    assert f"/conditions/{new}/supersede" in body
    assert f"/conditions/{new}/mark-error" in body


def test_condition_mark_error_is_row_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag marked condition")
    r = client_tx.post(f"/conditions/{cid}/mark-error", headers=HX)
    body = _body(r)
    new = _newer(tx_conn, "condition", cid)
    assert r.status_code == 200 and _is_piece(body)
    assert body.lstrip().startswith(f'<tr id="condition-{new}">')
    assert "entered-in-error" in body and "Frag marked condition" in body
    assert "mark-error" not in body


def test_condition_add_form_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    r = client_tx.get(f"/subjects/{sid}/conditions/new", headers=HX)
    body = _body(r)
    assert r.status_code == 200 and _is_piece(body)
    assert body.lstrip().startswith('<tr id="condition-add"><td colspan="6">')
    assert f'action="/subjects/{sid}/conditions/new"' in body


def test_condition_add_invalid_is_422_form_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    r = client_tx.post(f"/subjects/{sid}/conditions/new", headers=HX, data={
        "code_text": "", "clinical_status": "active"})
    body = _body(r)
    assert r.status_code == 422 and _is_piece(body)
    assert body.lstrip().startswith('<tr id="condition-add">')
    assert "code_text is required" in body


def test_condition_add_ok_is_new_row_and_add_row(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    r = client_tx.post(f"/subjects/{sid}/conditions/new", headers=HX, data={
        "code_text": "Frag added condition", "clinical_status": "active"})
    body = _body(r)
    assert r.status_code == 200 and _is_piece(body)
    assert body.count("<tr id=") == 2
    assert "Frag added condition" in body
    assert '<tr id="condition-add">' in body
    assert f'href="/subjects/{sid}/conditions/new"' in body
    assert body.index("Frag added condition") < body.index("condition-add")


def test_medication_supersede_form_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    mid = _medication(client_tx, tx_conn, sid, "Frag form medication")
    r = client_tx.get(f"/medications/{mid}/supersede", headers=HX)
    body = _body(r)
    assert r.status_code == 200 and _is_piece(body)
    assert body.lstrip().startswith(
        f'<tr id="medication-{mid}"><td colspan="4">')
    assert f'action="/medications/{mid}/supersede"' in body


def test_medication_supersede_invalid_is_422_form_fragment(client_tx,
                                                           tx_conn):
    sid = _subject(client_tx, tx_conn)
    mid = _medication(client_tx, tx_conn, sid, "Frag invalid medication")
    r = client_tx.post(f"/medications/{mid}/supersede", headers=HX, data={
        "medication_text": "", "status": "active"})
    body = _body(r)
    assert r.status_code == 422 and _is_piece(body)
    assert body.lstrip().startswith(f'<tr id="medication-{mid}">')
    assert _newer(tx_conn, "medication_request", mid) == "None"


def test_medication_supersede_ok_is_new_row_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    mid = _medication(client_tx, tx_conn, sid, "Frag old medication")
    r = client_tx.post(f"/medications/{mid}/supersede", headers=HX, data={
        "medication_text": "Frag new medication", "status": "active",
        "dose_value": "0.4", "dose_unit": "mg"})
    body = _body(r)
    new = _newer(tx_conn, "medication_request", mid)
    assert new != "None"
    assert r.status_code == 200 and _is_piece(body)
    assert body.lstrip().startswith(f'<tr id="medication-{new}">')
    assert body.count("<tr id=") == 1
    assert "Frag new medication" in body


def test_medication_mark_error_is_row_fragment(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    mid = _medication(client_tx, tx_conn, sid, "Frag marked medication")
    r = client_tx.post(f"/medications/{mid}/mark-error", headers=HX)
    body = _body(r)
    new = _newer(tx_conn, "medication_request", mid)
    assert r.status_code == 200 and _is_piece(body)
    assert body.lstrip().startswith(f'<tr id="medication-{new}">')
    assert "entered-in-error" in body and "mark-error" not in body


def test_medication_add_fragments(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    url = f"/subjects/{sid}/medications/new"
    form = _body(client_tx.get(url, headers=HX))
    assert form.lstrip().startswith('<tr id="medication-add"><td colspan="4">')
    assert _is_piece(form) and f'action="{url}"' in form
    bad = client_tx.post(url, headers=HX, data={
        "medication_text": "", "status": "active"})
    assert bad.status_code == 422
    assert _body(bad).lstrip().startswith('<tr id="medication-add">')
    good = client_tx.post(url, headers=HX, data={
        "medication_text": "Frag added medication", "status": "active"})
    body = _body(good)
    assert good.status_code == 200 and _is_piece(body)
    assert body.count("<tr id=") == 2
    assert "Frag added medication" in body
    assert '<tr id="medication-add">' in body


def test_without_the_header_a_write_still_redirects(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag plain condition")
    for url, data in (
        (f"/subjects/{sid}/conditions/new",
         {"code_text": "Frag plain two", "clinical_status": "active"}),
        (f"/conditions/{cid}/supersede",
         {"code_text": "Frag plain three", "clinical_status": "active"}),
    ):
        r = client_tx.post(url, data=data)
        assert r.status_code in (302, 303)
        assert r.headers["Location"].endswith(f"/subjects/{sid}")


def test_header_value_other_than_true_is_a_full_page(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag header condition")
    r = client_tx.get(f"/conditions/{cid}/supersede",
                      headers={"HX-Request": "false"})
    assert "<html" in _body(r)


def test_origin_check_still_applies_to_fragment_posts(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    r = client_tx.post(
        f"/subjects/{sid}/conditions/new",
        headers={"HX-Request": "true", "Origin": "http://evil.example"},
        data={"code_text": "Frag origin", "clinical_status": "active"})
    assert r.status_code == 403
    n = tx_conn.execute(
        "SELECT count(*) AS n FROM phsa.condition WHERE subject_id = %s",
        (sid,)).fetchone()["n"]
    assert n == 0


def test_pool_put_matches_get_on_fragment_writes(app, client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Frag pool condition")
    pool = app.extensions["phsa_pool"]
    calls = {"get": 0, "put": 0}
    real_get, real_put = pool.get, pool.put

    def counting_get():
        calls["get"] += 1
        return real_get()

    def counting_put(conn):
        calls["put"] += 1
        return real_put(conn)

    pool.get, pool.put = counting_get, counting_put
    try:
        r = client_tx.post(f"/conditions/{cid}/supersede", headers=HX, data={
            "code_text": "Frag pool two", "clinical_status": "active"})
    finally:
        pool.get, pool.put = real_get, real_put
    assert r.status_code == 200
    assert calls["get"] == 2 and calls["put"] == 2
