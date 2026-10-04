"""PHSA CRS app: factory.

Verbs are Create / Read / Supersede only (spec section 0). No Update, no Delete.
"""

import os

from flask import Flask

from .config import load_config
from .db import AppPool


def create_app(config=None) -> Flask:
    """Application factory. Reads env vars once; refuses to start without PHSA_DSN."""
    cfg = config if config is not None else load_config()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        root_path=repo_root,
        template_folder=os.path.join(repo_root, "templates"),
        static_folder=os.path.join(repo_root, "static"),
    )
    app.config["PHSA_CONFIG"] = cfg

    pool = AppPool(cfg.dsn)
    app.extensions["phsa_pool"] = pool

    from .routes import bp
    app.register_blueprint(bp)

    @app.teardown_appcontext
    def _close_pool_on_app_exit(exception):
        if exception:
            pool.close()

    return app
