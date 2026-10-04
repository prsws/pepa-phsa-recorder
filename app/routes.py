"""Routes. M0 provides / and /healthz; later milestones add the screens.

/healthz is the only use of PHSA_DSN in M0: a single SELECT 1.
No state-changing routes exist yet.
"""

import logging

from flask import Blueprint, Response, current_app, render_template

log = logging.getLogger("phsa")

bp = Blueprint("main", __name__)


def _probe_pool(pool) -> bool:
    """SELECT 1 through the pool; never commits; returns reachability."""
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
        conn.rollback()  # never commit; nothing was written
        pool.put(conn)
    return True


@bp.get("/")
def index():
    # M0 placeholder: no database query (the subject list arrives with M1).
    return render_template("index.html")


@bp.get("/healthz")
def healthz() -> Response:
    if not _probe_pool(current_app.extensions["phsa_pool"]):
        return Response("503 database unreachable\n", status=503, mimetype="text/plain")
    return Response("ok\n", status=200, mimetype="text/plain")
