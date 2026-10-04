"""Routes. M0 provides /healthz; M1 adds subjects and demographics:

    GET                          /                list subjects
    GET,POST                     /subjects/new    create a subject
    GET                          /subjects/<id>   subject overview
    GET,POST                     /subjects/<id>/demographics
                                 create demographics (if none) or supersede them

Page rules (spec 6.1): CRS button labels only, the fixed test-data banner
(every page, in base.html), HTTP 422 with the form re-rendered on
validation failure, a plain 500 page on unexpected database errors, and an
Origin check on every POST.

The verbs are Create / Read / Supersede (spec section 0). There is no
Update and no Delete; to change a demographics entry the app inserts a new
version and points the old row at it.
"""

import logging

import psycopg
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from . import queries, validation

log = logging.getLogger("phsa")

bp = Blueprint("main", __name__)


def _probe_pool(pool) -> bool:
    """SELECT 1 through the pool; never commits; returns reachability.

    Any failure — including a failure of the rollback in the finally block
    on a broken connection — must not escape: /healthz returns 503, never
    500. The connection is always handed back with pool.put(conn).
    """
    conn = None
    try:
        conn = pool.get()
    except Exception as e:
        log.error("database unreachable: %s: %s", type(e).__name__, e)
        return False
    try:
        conn.execute("SELECT 1")
    except Exception as e:
        log.error("database probe failed: %s: %s", type(e).__name__, e)
        return False
    finally:
        try:
            conn.rollback()  # never commit; nothing was written
        except Exception as e:
            log.error("rollback after probe failed: %s: %s", type(e).__name__, e)
        pool.put(conn)
    return True


def _origin_ok() -> bool:
    """POST safety (spec 6.1): reject a POST whose Origin header is present
    and does not match this request's own scheme + host.

    A POST with no Origin header is allowed (that is how a plain
    form-submit from this site's own pages behaves in some configurations,
    and this is a no-login LAN app kept light per the spec).
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        host = origin.split("://", 1)[1].split("/", 1)[0]
    except (IndexError, ValueError):
        return False
    return host == request.host


def _request_conn():
    """A pooled connection for this request (one open transaction).

    In tests the pool's connection factory returns the test's shared
    connection (conftest), so the request never touches the real database;
    in production it is a pooled phsa connection.
    """
    return current_app.extensions["phsa_pool"].get()


def _finish(conn, commit: bool):
    """End the request's transaction: one commit (or rollback on error),
    then hand the connection back to the pool. The rollback of a broken
    connection never escapes."""
    try:
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception as e:
        log.error("finishing the request transaction failed: %s: %s",
                  type(e).__name__, e)
    finally:
        pool = current_app.extensions["phsa_pool"]
        try:
            pool.put(conn)
        except Exception as e:
            log.error("returning the connection to the pool failed: %s: %s",
                      type(e).__name__, e)


def _error_page(message: str) -> Response:
    """500: a plain page, no SQL, no connection details (spec 6.1)."""
    return render_template("error.html", message=message), 500


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

@bp.get("/")
def index():
    """GET /: list subjects with counts of current conditions / medications."""
    conn = _request_conn()
    try:
        subjects = queries.list_subjects(conn)
    except Exception as e:
        log.error("database error listing subjects: %s: %s", type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    finally:
        _finish(conn, commit=False)  # reads only; never commit here
    return render_template("index.html", subjects=subjects)


@bp.route("/subjects/new", methods=["GET", "POST"])
def subject_new():
    """GET+POST /subjects/new: create a subject (label required;
    fixture_id optional; is_test_data is not on the form — it is a
    constant true in the data layer, spec rule 4)."""
    if request.method == "GET":
        return render_template("subject_new.html", form={}, errors=[],
                               field_errors={})

    if not _origin_ok():
        return Response("forbidden: Origin does not match", status=403)

    form = request.form
    fields, errors = validation.validate_subject(form)
    if errors:
        # Validation failure: re-render with the user's input kept, HTTP
        # 422, no database write (spec 6.1).
        return render_template("subject_new.html", form=form, errors=errors,
                               field_errors={"label": "label is required"}), 422

    conn = _request_conn()
    try:
        subject_id = queries.create_subject(conn, fields["label"], fields["fixture_id"])
    except psycopg.errors.UniqueViolation as e:
        # duplicate fixture_id: a friendly error, not a 500 (spec M1)
        _finish(conn, commit=False)
        return render_template(
            "subject_new.html", form=form, errors=[],
            field_errors={"fixture_id": "that fixture id is already used by another subject"},
        ), 409
    except Exception as e:
        log.error("database error creating subject: %s: %s", type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    _finish(conn, commit=True)
    return redirect(url_for("main.subject_page", id=subject_id))


@bp.get("/subjects/<id>")
def subject_page(id: str):
    """GET /subjects/<id>: overview — current demographics, current
    conditions, current medication requests; link to add/supersede."""
    conn = _request_conn()
    try:
        subject = queries.get_subject(conn, id)
        if subject is None:
            abort(404, description="no such subject")
        demo = queries.get_current_demographics(conn, id)
        # M1 scope: conditions and medication requests arrive in M2/M3.
        conditions, medications = [], []
    except Exception as e:
        if e.code == 404:
            raise  # the caller (Flask's 404 handler) answers
        log.error("database error in subject overview: %s: %s", type(e).__name__, e)
        return _error_page("something went wrong")
    finally:
        _finish(conn, commit=False)  # reads only; never commit here
    return render_template("subject.html", subject=subject, demo=demo,
                           conditions=conditions, medications=medications)


def _demo_form_from_row(row: dict | None) -> dict:
    """The form's pre-fill values, exactly as stored (blank stays blank)."""
    if row is None:
        return {k: "" for k in
                ("family_name", "given_name", "usual_name", "sex",
                 "birth_year", "birth_date", "home_municipality",
                 "preferred_language")}
    return {
        "family_name": row.get("family_name") or "",
        "given_name": row.get("given_name") or "",
        "usual_name": row.get("usual_name") or "",
        "sex": row.get("sex") or "",
        "birth_year": "" if row.get("birth_year") is None else str(row["birth_year"]),
        "birth_date": row["birth_date"].isoformat() if row.get("birth_date") else "",
        "home_municipality": row.get("home_municipality") or "",
        "preferred_language": row.get("preferred_language") or "",
    }


@bp.route("/subjects/<id>/demographics", methods=["GET", "POST"])
def demographics_page(id: str):
    """GET,POST /subjects/<id>/demographics (spec 6): create demographics
    (if none) or supersede them.

    POST rules: Origin check (6.1); validation failure re-renders the form
    with the user's input kept and returns HTTP 422, no database write;
    the current row missing (the subject was never seen with demographics)
    creates; otherwise it supersedes (4.2) — an old row that was already
    superseded by someone else yields HTTP 409, nothing written; an
    unexpected database error yields a plain 500 page.
    """
    if request.method == "GET":
        conn = _request_conn()
        try:
            subject = queries.get_subject(conn, id)
            if subject is None:
                abort(404, description="no such subject")
            demo = queries.get_current_demographics(conn, id)
        except Exception as e:
            if e.code == 404:
                raise  # the caller (Flask's 404 handler) answers
            log.error("database error in demographics form: %s: %s", type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)  # reads only; never commit here
        return render_template(
            "demographics.html", subject=subject, current=demo,
            form=_demo_form_from_row(demo), errors=[], field_errors={},
            mode="supersede" if demo else "create",
            sex_options=validation.SEX_OPTIONS,
        )

    if not _origin_ok():
        return Response("forbidden: Origin does not match", status=403)

    fields, errors = validation.validate_demographics(request.form)
    if errors:
        conn = _request_conn()
        try:
            subject = queries.get_subject(conn, id)
            if subject is None:
                abort(404, description="no such subject")
            current = queries.get_current_demographics(conn, id)
        except Exception as e:
            if e.code == 404:
                raise  # the caller (Flask's 404 handler) answers
            log.error("database error in demographics form: %s: %s", type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)
        # re-render with the user's input kept, HTTP 422, no database write
        field_errors = {}
        for e in errors:
            for key in ("birth_year", "birth_date"):
                if key in e and key not in field_errors:
                    field_errors[key] = e
        return render_template(
            "demographics.html", subject=subject, current=current,
            form=request.form, errors=errors, field_errors=field_errors,
            mode="supersede" if current else "create",
            sex_options=validation.SEX_OPTIONS,
        ), 422

    conn = _request_conn()
    try:
        current = queries.get_current_demographics(conn, id)
        if current is None:
            queries.create_demographics(conn, id, fields)
        else:
            try:
                queries.supersede_demographics(conn, current["id"], id, fields)
            except queries.SupersedeConflict as e:
                _finish(conn, commit=False)
                return Response(str(e), status=409, mimetype="text/plain")
    except Exception as e:
        log.error("database error in demographics write: %s: %s", type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    _finish(conn, commit=True)
    return redirect(url_for("main.subject_page", id=id))


@bp.get("/healthz")
def healthz() -> Response:
    if not _probe_pool(current_app.extensions["phsa_pool"]):
        return Response("503 database unreachable\n", status=503, mimetype="text/plain")
    return Response("ok\n", status=200, mimetype="text/plain")
