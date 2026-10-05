"""Routes. M0 provides /healthz; M1 adds subjects and demographics:

    GET                          /                list subjects
    GET,POST                     /subjects/new    create a subject
    GET                          /subjects/<id>   subject overview
    GET,POST                     /subjects/<id>/demographics
                                 create demographics (if none) or supersede them
    GET,POST                     /subjects/<id>/conditions/new
                                 add a condition
    GET,POST                     /conditions/<id>/supersede
                                 supersede a condition (new version)
    GET,POST                     /subjects/<id>/medications/new
                                 add a medication request
    GET,POST                     /medications/<id>/supersede
                                   supersede a medication request (new version)

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
from werkzeug.exceptions import HTTPException
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
    ok = False
    try:
        subject_id = queries.create_subject(conn, fields["label"], fields["fixture_id"])
        ok = True
    except psycopg.errors.UniqueViolation as e:
        # duplicate fixture_id: a friendly error, not a 500 (spec M1)
        return render_template(
            "subject_new.html", form=form, errors=[],
            field_errors={"fixture_id": "that fixture id is already used by another subject"},
        ), 409
    except Exception as e:
        log.error("database error creating subject: %s: %s", type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    finally:
        _finish(conn, commit=ok)
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
        conditions = queries.list_current_conditions(conn, id)
        medications = queries.list_current_medication_requests(conn, id)
    except HTTPException:
        raise  # the caller (Flask's 404 handler) answers
    except Exception as e:
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
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
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
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
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
    ok = False
    try:
        subject = queries.get_subject(conn, id)
        if subject is None:
            abort(404, description="no such subject")
        current = queries.get_current_demographics(conn, id)
        if current is None:
            queries.create_demographics(conn, id, fields)
        else:
            try:
                queries.supersede_demographics(conn, current["id"], id, fields)
            except queries.SupersedeConflict as e:
                return Response(str(e), status=409, mimetype="text/plain")
        ok = True
    except HTTPException:
        raise  # the caller (Flask's 404 handler) answers
    except Exception as e:
        log.error("database error in demographics write: %s: %s", type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    finally:
        _finish(conn, commit=ok)
    return redirect(url_for("main.subject_page", id=id))


def _condition_form_from_form(form) -> dict:
    """The condition form's re-render values, exactly as posted (blank stays
    blank). Used to keep the user's input on a 422."""
    return {
        "code_text": form.get("code_text") or "",
        "code_system": form.get("code_system") or "",
        "code": form.get("code") or "",
        "clinical_status": form.get("clinical_status") or "",
        "verification_status": form.get("verification_status") or "",
        "onset_text": form.get("onset_text") or "",
        "onset_date": form.get("onset_date") or "",
        "note": form.get("note") or "",
    }


@bp.route("/subjects/<id>/conditions/new", methods=["GET", "POST"])
def condition_new_page(id: str):
    """GET,POST /subjects/<id>/conditions/new (spec 6): add a condition.
    Supersede (new version) is M4a; mark entered-in-error is later M4.

    POST rules (spec 6.1): Origin check; validation failure re-renders the
    form with the user's input kept and returns HTTP 422, no database
    write; an unexpected database error yields a plain 500 page.
    """
    if request.method == "GET":
        conn = _request_conn()
        try:
            subject = queries.get_subject(conn, id)
            if subject is None:
                abort(404, description="no such subject")
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in condition form: %s: %s", type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)  # reads only; never commit here
        return render_template(
            "condition_new.html", subject=subject, form={},
            errors=[], field_errors={},
            clinical_status_options=validation.CLINICAL_STATUS_OPTIONS,
            verification_status_options=validation.VERIFICATION_STATUS_OPTIONS,
            mode="create",
        )

    if not _origin_ok():
        return Response("forbidden: Origin does not match", status=403)

    form = request.form
    fields, errors = validation.validate_condition(form)
    if errors:
        conn = _request_conn()
        try:
            subject = queries.get_subject(conn, id)
            if subject is None:
                abort(404, description="no such subject")
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in condition form: %s: %s", type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)
        # re-render with the user's input kept, HTTP 422, no database write
        field_errors = {}
        for e in errors:
            for key in ("code_text", "code_system", "code", "clinical_status",
                        "verification_status", "onset_date"):
                if key in e and key not in field_errors:
                    field_errors[key] = e
        return render_template(
            "condition_new.html", subject=subject, form=_condition_form_from_form(form),
            errors=errors, field_errors=field_errors,
            clinical_status_options=validation.CLINICAL_STATUS_OPTIONS,
            verification_status_options=validation.VERIFICATION_STATUS_OPTIONS,
        ), 422

    conn = _request_conn()
    ok = False
    try:
        subject = queries.get_subject(conn, id)
        if subject is None:
            abort(404, description="no such subject")
        queries.create_condition(conn, id, fields)
        ok = True
    except HTTPException:
        raise  # the caller (Flask's 404 handler) answers
    except Exception as e:
        log.error("database error creating condition: %s: %s", type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    finally:
        _finish(conn, commit=ok)
    return redirect(url_for("main.subject_page", id=id))


def _condition_form_from_row(row: dict) -> dict:
    """The condition form's pre-fill values, exactly as stored (blank stays
    blank; a date as ISO YYYY-MM-DD, spec 6.1)."""
    return {
        "code_text": row.get("code_text") or "",
        "code_system": row.get("code_system") or "",
        "code": row.get("code") or "",
        "clinical_status": row.get("clinical_status") or "",
        "verification_status": row.get("verification_status") or "",
        "onset_text": row.get("onset_text") or "",
        "onset_date": row["onset_date"].isoformat() if row.get("onset_date") else "",
        "note": row.get("note") or "",
    }


@bp.route("/conditions/<id>/supersede", methods=["GET", "POST"])
def condition_supersede_page(id: str):
    """GET,POST /conditions/<id>/supersede (spec 6, M4a): a new version of
    an existing condition.

    GET: no such condition is a 404; a condition whose superseded_by is not
    NULL answers HTTP 409 with a plain message; otherwise the condition form
    filled with the current row's values (supersede mode of the same
    condition form the create route uses).

    POST rules: Origin check (6.1); the SAME validation the create-condition
    route uses; a validation failure re-renders the form with the user's
    input kept, HTTP 422, no database write; a condition that does not exist
    is a 404; a condition already superseded (SupersedeConflict) is HTTP 409
    with a plain message, nothing written; an unexpected database error is a
    plain 500 page. On success: redirect to the subject page.
    """
    if request.method == "GET":
        conn = _request_conn()
        try:
            cond = queries.get_condition(conn, id)
            if cond is None:
                abort(404, description="no such condition")
            if cond["superseded_by"] is not None:
                return Response(
                    "this version has already been superseded",
                    status=409, mimetype="text/plain")
            subject = queries.get_subject(conn, cond["subject_id"])
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in condition supersede form: %s: %s",
                      type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)  # reads only; never commit here
        return render_template(
            "condition_new.html", subject=subject,
            form=_condition_form_from_row(cond), errors=[], field_errors={},
            clinical_status_options=validation.CLINICAL_STATUS_OPTIONS,
            verification_status_options=validation.VERIFICATION_STATUS_OPTIONS,
            condition_id=id, mode="supersede",
        )

    if not _origin_ok():
        return Response("forbidden: Origin does not match", status=403)

    form = request.form
    fields, errors = validation.validate_condition(form)
    if errors:
        conn = _request_conn()
        try:
            cond = queries.get_condition(conn, id)
            if cond is None:
                abort(404, description="no such condition")
            if cond["superseded_by"] is not None:
                return Response(
                    "this version has already been superseded",
                    status=409, mimetype="text/plain")
            subject = queries.get_subject(conn, cond["subject_id"])
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in condition supersede form: %s: %s",
                      type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)
        # re-render with the user's input kept, HTTP 422, no database write
        field_errors = {}
        for e in errors:
            for key in ("code_text", "code_system", "code", "clinical_status",
                        "verification_status", "onset_date"):
                if key in e and key not in field_errors:
                    field_errors[key] = e
        return render_template(
            "condition_new.html", subject=subject,
            form=_condition_form_from_form(form),
            errors=errors, field_errors=field_errors,
            clinical_status_options=validation.CLINICAL_STATUS_OPTIONS,
            verification_status_options=validation.VERIFICATION_STATUS_OPTIONS,
            condition_id=id, mode="supersede",
        ), 422

    conn = _request_conn()
    ok = False
    try:
        cond = queries.get_condition(conn, id)
        if cond is None:
            abort(404, description="no such condition")
        if cond["superseded_by"] is not None:
            return Response(
                "this version has already been superseded",
                status=409, mimetype="text/plain")
        queries.supersede_condition(conn, cond["id"], cond["subject_id"], fields)
        ok = True
    except HTTPException:
        raise  # the caller (Flask's 404 handler) answers
    except queries.SupersedeConflict as e:
        return Response(str(e), status=409, mimetype="text/plain")
    except Exception as e:
        log.error("database error in condition supersede: %s: %s",
                  type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    finally:
        _finish(conn, commit=ok)
    return redirect(url_for("main.subject_page", id=cond["subject_id"]))


def _med_form_from_row(row: dict) -> dict:
    """The medication request form's pre-fill values, exactly as stored
    (blank stays blank; a date as ISO YYYY-MM-DD, spec 6.1)."""
    return {
        "medication_text": row.get("medication_text") or "",
        "medication_code_system": row.get("medication_code_system") or "",
        "medication_code": row.get("medication_code") or "",
        "status": row.get("status") or "",
        "authored_on": row["authored_on"].isoformat() if row.get("authored_on") else "",
        "sig_text": row.get("sig_text") or "",
        "dose_value": "" if row.get("dose_value") is None else str(row["dose_value"]),
        "dose_unit": row.get("dose_unit") or "",
        "dose_unit_code": row.get("dose_unit_code") or "",
        "route_text": row.get("route_text") or "",
        "frequency": "" if row.get("frequency") is None else str(row["frequency"]),
        "period": "" if row.get("period") is None else str(row["period"]),
        "period_unit": row.get("period_unit") or "",
        "timing_when": list(row["timing_when"]) if row.get("timing_when") else [],
        "note": row.get("note") or "",
    }


def _med_form_from_form(form) -> dict:
    """The medication request form's re-render values, exactly as posted
    (blank stays blank). Used to keep the user's input on a 422."""
    return {
        "medication_text": form.get("medication_text") or "",
        "medication_code_system": form.get("medication_code_system") or "",
        "medication_code": form.get("medication_code") or "",
        "status": form.get("status") or "",
        "authored_on": form.get("authored_on") or "",
        "sig_text": form.get("sig_text") or "",
        "dose_value": form.get("dose_value") or "",
        "dose_unit": form.get("dose_unit") or "",
        "dose_unit_code": form.get("dose_unit_code") or "",
        "route_text": form.get("route_text") or "",
        "frequency": form.get("frequency") or "",
        "period": form.get("period") or "",
        "period_unit": form.get("period_unit") or "",
        "timing_when": [v for v in form.getlist("timing_when") if v],
        "note": form.get("note") or "",
    }


@bp.route("/subjects/<id>/medications/new", methods=["GET", "POST"])
def medication_request_new_page(id: str):
    """GET,POST /subjects/<id>/medications/new (spec 6): add a medication
    request. Supersede (new version) and mark entered-in-error are M4.

    POST rules (spec 6.1): Origin check; validation failure re-renders the
    form with the user's input kept and returns HTTP 422, no database
    write; an unexpected database error yields a plain 500 page. `intent`
    and `safety_critical` are not on the form; they are left to the schema
    defaults.
    """
    if request.method == "GET":
        conn = _request_conn()
        try:
            subject = queries.get_subject(conn, id)
            if subject is None:
                abort(404, description="no such subject")
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in medication request form: %s: %s",
                      type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)  # reads only; never commit here
        return render_template(
            "medication_request_new.html", subject=subject, form={},
            errors=[], field_errors={},
            status_options=validation.MED_STATUS_OPTIONS,
            period_unit_options=validation.PERIOD_UNIT_OPTIONS,
            timing_when_options=validation.TIMING_WHEN_OPTIONS,
        )

    if not _origin_ok():
        return Response("forbidden: Origin does not match", status=403)

    form = request.form
    fields, errors = validation.validate_medication_request(form)
    if errors:
        conn = _request_conn()
        try:
            subject = queries.get_subject(conn, id)
            if subject is None:
                abort(404, description="no such subject")
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in medication request form: %s: %s",
                      type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)
        # re-render with the user's input kept, HTTP 422, no database write
        field_errors = {}
        for e in errors:
            for key in ("medication_text", "medication_code_system",
                         "medication_code", "status", "authored_on",
                         "dose_value", "dose_unit", "frequency", "period",
                         "period_unit"):
                if key in e and key not in field_errors:
                    field_errors[key] = e
        return render_template(
            "medication_request_new.html", subject=subject,
            form=_med_form_from_form(form),
            errors=errors, field_errors=field_errors,
            status_options=validation.MED_STATUS_OPTIONS,
            period_unit_options=validation.PERIOD_UNIT_OPTIONS,
            timing_when_options=validation.TIMING_WHEN_OPTIONS,
        ), 422

    conn = _request_conn()
    ok = False
    try:
        subject = queries.get_subject(conn, id)
        if subject is None:
            abort(404, description="no such subject")
        queries.create_medication_request(conn, id, fields)
        ok = True
    except HTTPException:
        raise  # the caller (Flask's 404 handler) answers
    except Exception as e:
        log.error("database error creating medication request: %s: %s",
                  type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    finally:
        _finish(conn, commit=ok)
    return redirect(url_for("main.subject_page", id=id))


@bp.route("/medications/<id>/supersede", methods=["GET", "POST"])
def medication_supersede_page(id: str):
    """GET,POST /medications/<id>/supersede (spec 6, M4b): a new version of
    an existing medication request.

    GET: no such medication request is a 404; a version whose superseded_by
    is not NULL answers HTTP 409 with a plain message; otherwise the
    medication form filled with the current row's values (supersede mode of
    the same medication form the create route uses).

    POST rules: Origin check (6.1); the SAME validation the
    create-medication route uses; a validation failure re-renders the form
    with the user's input kept, HTTP 422, no database write; a medication
    request that does not exist is a 404; one already superseded
    (SupersedeConflict) is HTTP 409 with a plain message, nothing written;
    an unexpected database error is a plain 500 page. On success: redirect
    to the subject page.
    """
    if request.method == "GET":
        conn = _request_conn()
        try:
            med = queries.get_medication_request(conn, id)
            if med is None:
                abort(404, description="no such medication request")
            if med["superseded_by"] is not None:
                return Response(
                    "this version has already been superseded",
                    status=409, mimetype="text/plain")
            subject = queries.get_subject(conn, med["subject_id"])
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in medication supersede form: %s: %s",
                      type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)  # reads only; never commit here
        return render_template(
            "medication_request_new.html", subject=subject,
            form=_med_form_from_row(med), errors=[], field_errors={},
            status_options=validation.MED_STATUS_OPTIONS,
            period_unit_options=validation.PERIOD_UNIT_OPTIONS,
            timing_when_options=validation.TIMING_WHEN_OPTIONS,
            medication_id=id, mode="supersede",
        )

    if not _origin_ok():
        return Response("forbidden: Origin does not match", status=403)

    form = request.form
    fields, errors = validation.validate_medication_request(form)
    if errors:
        conn = _request_conn()
        try:
            med = queries.get_medication_request(conn, id)
            if med is None:
                abort(404, description="no such medication request")
            if med["superseded_by"] is not None:
                return Response(
                    "this version has already been superseded",
                    status=409, mimetype="text/plain")
            subject = queries.get_subject(conn, med["subject_id"])
        except HTTPException:
            raise  # the caller (Flask's 404 handler) answers
        except Exception as e:
            log.error("database error in medication supersede form: %s: %s",
                      type(e).__name__, e)
            return _error_page("something went wrong")
        finally:
            _finish(conn, commit=False)
        # re-render with the user's input kept, HTTP 422, no database write
        field_errors = {}
        for e in errors:
            for key in ("medication_text", "medication_code_system",
                        "medication_code", "status", "authored_on",
                        "dose_value", "dose_unit", "frequency", "period",
                        "period_unit"):
                if key in e and key not in field_errors:
                    field_errors[key] = e
        return render_template(
            "medication_request_new.html", subject=subject,
            form=_med_form_from_form(form),
            errors=errors, field_errors=field_errors,
            status_options=validation.MED_STATUS_OPTIONS,
            period_unit_options=validation.PERIOD_UNIT_OPTIONS,
            timing_when_options=validation.TIMING_WHEN_OPTIONS,
            medication_id=id, mode="supersede",
        ), 422

    conn = _request_conn()
    ok = False
    try:
        med = queries.get_medication_request(conn, id)
        if med is None:
            abort(404, description="no such medication request")
        if med["superseded_by"] is not None:
            return Response(
                "this version has already been superseded",
                status=409, mimetype="text/plain")
        queries.supersede_medication_request(conn, med["id"], med["subject_id"], fields)
        ok = True
    except HTTPException:
        raise  # the caller (Flask's 404 handler) answers
    except queries.SupersedeConflict as e:
        return Response(str(e), status=409, mimetype="text/plain")
    except Exception as e:
        log.error("database error in medication supersede: %s: %s",
                  type(e).__name__, e)
        return _error_page("something went wrong; nothing was saved")
    finally:
        _finish(conn, commit=ok)
    return redirect(url_for("main.subject_page", id=med["subject_id"]))


@bp.get("/healthz")
def healthz() -> Response:
    if not _probe_pool(current_app.extensions["phsa_pool"]):
        return Response("503 database unreachable\n", status=503, mimetype="text/plain")
    return Response("ok\n", status=200, mimetype="text/plain")
