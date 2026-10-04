"""Configuration: environment variables, read once at startup.

DSNs contain the password and come from the environment only (or a git-ignored
.env via python-dotenv). They are never printed, logged or copied.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    dsn: str
    test_dsn: str | None
    host: str
    port: int


def load_config() -> Config:
    # Load .env from the repository root if present; it never overrides the
    # real environment, and its contents are never printed.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(repo_root, ".env"), override=False)

    dsn = os.environ.get("PHSA_DSN")
    if not dsn:
        raise ConfigError("PHSA_DSN is not set; the app refuses to start without it")

    return Config(
        dsn=dsn,
        test_dsn=os.environ.get("PHSA_TEST_DSN") or None,
        host=os.environ.get("PHSA_HOST", "127.0.0.1"),
        port=int(os.environ.get("PHSA_PORT", "8470")),
    )


def load_config_for_tests() -> Config:
    """For the test suite: the app is pointed at the TEST database.

    M0 writes nothing to the real database; the only permitted use of
    PHSA_DSN is the SELECT 1 in /healthz, which the healthz test drives
    directly. Tests use PHSA_TEST_DSN (spec section 9) — no test may ever
    write to the real database.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(repo_root, ".env"), override=False)

    test_dsn = os.environ.get("PHSA_TEST_DSN")
    if not test_dsn:
        raise ConfigError("PHSA_TEST_DSN is not set; tests need the test database")

    return Config(
        dsn=test_dsn,
        test_dsn=test_dsn,
        host="127.0.0.1",
        port=0,
    )
