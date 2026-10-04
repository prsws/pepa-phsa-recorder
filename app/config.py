"""Configuration: environment variables, read once at startup.

DSNs contain the password and come from the environment only (or a git-ignored
.env via python-dotenv). They are never printed, logged or copied.
"""

import os
from dataclasses import dataclass

from dotenv import dotenv_values, load_dotenv


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

    Spec section 2: tests skip with a clear message when PHSA_TEST_DSN is
    unset or empty. So when it is unset or empty, test_dsn is None and the
    database tests skip; the pool (and the stub-pool tests) still work, so
    tests that need no database run and pass.

    Note: os.environ.get(key, default) returns the default only when the
    variable is ABSENT; `PHSA_TEST_DSN=` (empty, but present) yields "".
    dotenv_values() likewise returns "" for an empty assignment and drops
    the key only when the line is absent. This helper must treat both as
    unset, so it reads dotenv_values() directly, and the result is checked
    with `or None`.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    values = dotenv_values(os.path.join(repo_root, ".env"))
    load_dotenv(os.path.join(repo_root, ".env"), override=False)

    test_dsn = os.environ.get("PHSA_TEST_DSN")
    if test_dsn is None:
        test_dsn = values.get("PHSA_TEST_DSN")
    test_dsn = test_dsn or None  # unset or empty -> None -> tests skip

    return Config(
        dsn=test_dsn or "",
        test_dsn=test_dsn,
        host="127.0.0.1",
        port=0,
    )
