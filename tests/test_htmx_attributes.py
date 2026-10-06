"""M6b: the htmx 4 attributes in the pages and the one small script.

These tests read the HTML the server sends. What htmx then does with it in a
browser was checked by the reviewer with htmx 4.0.0 in a real browser.
"""
import pathlib
import re
import uuid

HX = {"HX-Request": "true"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
TAG = re.compile(r"<[a-zA-Z][^>]*>")


def _subject(client_tx, tx_conn):
    fixture = "HTMX-" + uuid.uuid4().hex[:8]
    client_tx.post("/subjects/new", data={
        "label": "Attribute test " + fixture, "fixture_id": fixture})
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


def _body(response):
    return response.get_data(as_text=True)


def _htmx_tags(html):
    """Every opening tag that carries an hx- attribute."""
    return [t for t in TAG.findall(html) if " hx-" in t]


def _tag_with(html, text):
    """The one opening tag that contains this text."""
    found = [t for t in TAG.findall(html) if text in t]
    assert len(found) == 1, (text, found)
    return found[0]


def test_every_page_loads_the_script_after_htmx(client_tx, tx_conn):
    page = _body(client_tx.get("/"))
    htmx = page.index('<script src="/static/vendor/htmx.min.js"></script>')
    ours = page.index('<script src="/static/app.js"></script>')
    assert htmx < ours < page.index("</head>")
    assert '<p id="request-problem" role="alert" hidden></p>' in page
    assert "<script>" not in page


def test_the_script_is_served_and_sets_no_swap(client_tx):
    r = client_tx.get("/static/app.js")
    text = _body(r)
    assert r.status_code == 200
    assert 'htmx.config.noSwap = [204, 304, 404, 409, "5xx"];' in text
    assert '"htmx:response:error"' in text
    assert "status === 422" in text
    assert text == (ROOT / "static" / "app.js").read_text()


def test_condition_row_attributes(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Attr condition")
    page = _body(client_tx.get(f"/subjects/{sid}"))
    link = _tag_with(page, f'href="/conditions/{cid}/supersede"')
    assert f'hx-get="/conditions/{cid}/supersede"' in link
    assert f'hx-target="#condition-{cid}"' in link
    assert 'hx-swap="outerHTML"' in link
    form = _tag_with(page, f'action="/conditions/{cid}/mark-error"')
    assert 'method="post"' in form
    assert f'hx-post="/conditions/{cid}/mark-error"' in form
    assert f'hx-target="#condition-{cid}"' in form
    assert 'hx-swap="outerHTML"' in form
    assert 'hx-confirm="Mark this entry as entered-in-error?' in form


def test_medication_row_attributes(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    mid = _medication(client_tx, tx_conn, sid, "Attr medication")
    page = _body(client_tx.get(f"/subjects/{sid}"))
    link = _tag_with(page, f'href="/medications/{mid}/supersede"')
    assert f'hx-get="/medications/{mid}/supersede"' in link
    assert f'hx-target="#medication-{mid}"' in link
    assert 'hx-swap="outerHTML"' in link
    form = _tag_with(page, f'action="/medications/{mid}/mark-error"')
    assert f'hx-post="/medications/{mid}/mark-error"' in form
    assert f'hx-target="#medication-{mid}"' in form
    assert 'hx-swap="outerHTML"' in form
    assert "hx-confirm=" in form


def test_add_rows_sit_inside_the_tables(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    _condition(client_tx, tx_conn, sid, "Attr add condition")
    _medication(client_tx, tx_conn, sid, "Attr add medication")
    page = _body(client_tx.get(f"/subjects/{sid}"))
    for kind, plural, label in (
        ("condition", "conditions", "Add a condition"),
        ("medication", "medications", "Add a medication request"),
    ):
        url = f"/subjects/{sid}/{plural}/new"
        assert page.count(f'<tr id="{kind}-add">') == 1
        assert page.count(label) == 1
        link = _tag_with(page, f'href="{url}"')
        assert f'hx-get="{url}"' in link
        assert f'hx-target="#{kind}-add"' in link
        assert 'hx-swap="outerHTML"' in link
        row = page.index(f'<tr id="{kind}-add">')
        assert page.rindex("<tbody>", 0, row) > page.rfind("</tbody>", 0, row)


def test_empty_tables_keep_the_plain_add_links(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    page = _body(client_tx.get(f"/subjects/{sid}"))
    assert page.count("None.") == 2
    assert f'href="/subjects/{sid}/conditions/new"' in page
    assert f'href="/subjects/{sid}/medications/new"' in page
    assert _htmx_tags(page) == []


def test_form_fragments_post_back_to_their_own_row(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Attr form condition")
    mid = _medication(client_tx, tx_conn, sid, "Attr form medication")
    for url, target in (
        (f"/conditions/{cid}/supersede", f"#condition-{cid}"),
        (f"/subjects/{sid}/conditions/new", "#condition-add"),
        (f"/medications/{mid}/supersede", f"#medication-{mid}"),
        (f"/subjects/{sid}/medications/new", "#medication-add"),
    ):
        piece = _body(client_tx.get(url, headers=HX))
        form = _tag_with(piece, f'action="{url}"')
        assert 'method="post"' in form
        assert f'hx-post="{url}"' in form
        assert f'hx-target="{target}"' in form
        assert 'hx-swap="outerHTML"' in form
        assert piece.lstrip().startswith(f'<tr id="{target[1:]}">')


def test_full_page_forms_carry_no_htmx(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Attr plain condition")
    mid = _medication(client_tx, tx_conn, sid, "Attr plain medication")
    for url in (f"/conditions/{cid}/supersede",
                f"/subjects/{sid}/conditions/new",
                f"/medications/{mid}/supersede",
                f"/subjects/{sid}/medications/new"):
        page = _body(client_tx.get(url))
        assert "<html" in page
        assert _htmx_tags(page) == []


def test_htmx_4_rules_hold_on_every_htmx_element(client_tx, tx_conn):
    sid = _subject(client_tx, tx_conn)
    cid = _condition(client_tx, tx_conn, sid, "Attr rules condition")
    mid = _medication(client_tx, tx_conn, sid, "Attr rules medication")
    pieces = [_body(client_tx.get(f"/subjects/{sid}"))]
    for url in (f"/conditions/{cid}/supersede",
                f"/subjects/{sid}/conditions/new",
                f"/medications/{mid}/supersede",
                f"/subjects/{sid}/medications/new"):
        pieces.append(_body(client_tx.get(url, headers=HX)))
    tags = [t for html in pieces for t in _htmx_tags(html)]
    assert len(tags) == 10
    for tag in tags:
        assert ("hx-get=" in tag) != ("hx-post=" in tag), tag
        assert "hx-target=" in tag and "hx-swap=" in tag, tag
        assert ":inherited" not in tag and "hx-delete" not in tag, tag
        assert "hx-vars" not in tag and "hx-ext" not in tag, tag
        assert "hx-disabled-elt" not in tag and "hx-boost" not in tag, tag

