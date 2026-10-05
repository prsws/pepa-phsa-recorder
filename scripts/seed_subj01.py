"""Load the synthetic fixture person SUBJ-01 (spec section 8).

Dry run (the default): loads everything into the TEST database inside one
transaction, prints the counts, and rolls back. Nothing is kept.
    .venv/bin/python -m scripts.seed_subj01

Real load: `--commit --target phsa` writes to the database behind PHSA_DSN.
Both flags are required. The script prints the host and database name, asks
two questions, and goes on only if the answer to each is exactly `yes`.
Only the owner runs this. It never prints a password or a connection string.
    .venv/bin/python -m scripts.seed_subj01 --commit --target phsa
"""
import argparse
import json
import pathlib
import sys

from psycopg.conninfo import conninfo_to_dict
from werkzeug.datastructures import MultiDict

from app import queries, validation
from app.config import load_config
from app.db import open_connection

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED_FILE = ROOT / "seed" / "subj01.json"

TARGET = "phsa"
QUESTION_DATABASE = "Is this the database you intend to write to?"
QUESTION_BACKUP = (
    "Has a fresh backup of the database VM been taken since the last"
    " schema change?"
)


class SeedError(RuntimeError):
    """The seed file holds a value the app's own checks refuse."""


def read_seed_file(path=SEED_FILE):
    return json.loads(path.read_text(encoding="utf-8"))


def _checked(validator, values):
    # The same checks the screens use; MultiDict is what a form gives them.
    cleaned, errors = validator(MultiDict(values))
    if errors:
        raise SeedError("; ".join(errors))
    return cleaned


def load_subj01(conn, data):
    """Load the whole fixture on `conn`. Never commits. Returns the counts."""
    counts = {"already_seeded": False, "subject": 0, "demographics": 0,
              "conditions": 0, "medication_requests": 0, "source_records": 0}
    fixture_id = data["subject"]["fixture_id"]
    if queries.get_subject_by_fixture_id(conn, fixture_id) is not None:
        counts["already_seeded"] = True
        return counts

    # Check everything first, so a bad value stops the run before any write.
    subject = _checked(validation.validate_subject, data["subject"])
    demographics = _checked(
        validation.validate_demographics, data["demographics"])
    conditions = [_checked(validation.validate_condition, c)
                  for c in data["conditions"]]
    medications = [_checked(validation.validate_medication_request, m)
                   for m in data["medication_requests"]]

    subject_id = str(queries.create_subject(
        conn, subject["label"], subject["fixture_id"]))
    queries.create_demographics(conn, subject_id, demographics)
    for condition in conditions:
        queries.create_condition(conn, subject_id, condition)
    for medication in medications:
        queries.create_medication_request(conn, subject_id, medication)

    counts["subject"] = 1
    counts["demographics"] = 1
    counts["conditions"] = len(conditions)
    counts["medication_requests"] = len(medications)
    counts["source_records"] = 1 + len(conditions) + len(medications)
    return counts


def _print_counts(counts):
    print(f"subject: {counts['subject']}")
    print(f"demographics: {counts['demographics']}")
    print(f"conditions: {counts['conditions']}")
    print(f"medication requests: {counts['medication_requests']}")
    print(f"source records: {counts['source_records']}")


def _dry_run(config):
    if not config.test_dsn:
        print("PHSA_TEST_DSN is not set; nothing was done.")
        return 2
    data = read_seed_file()
    conn = open_connection(config.test_dsn)
    try:
        counts = load_subj01(conn, data)
    finally:
        conn.rollback()
        conn.close()
    print("DRY RUN against the test database.")
    if counts["already_seeded"]:
        print("already seeded: SUBJ-01 exists; nothing was changed")
    else:
        _print_counts(counts)
    print("rolled back; nothing was kept")
    return 0


def _where(dsn):
    """Host, port and database name of a connection string. Never the
    password, and never the string itself."""
    try:
        parts = conninfo_to_dict(dsn)
    except Exception:
        return None
    return {"host": str(parts.get("host") or "(local socket)"),
            "port": str(parts.get("port") or "5432"),
            "dbname": str(parts.get("dbname") or "")}


def _real_load(config, ask):
    where = _where(config.dsn)
    if where is None:
        print("REFUSED: PHSA_DSN could not be read. Nothing was written.")
        return 2
    if where["dbname"] != TARGET:
        print(f"REFUSED: PHSA_DSN names database '{where['dbname']}', not"
              f" '{TARGET}'. Nothing was written.")
        return 2
    if config.test_dsn and config.dsn == config.test_dsn:
        print("REFUSED: PHSA_DSN and PHSA_TEST_DSN are the same."
              " Nothing was written.")
        return 2

    print("REAL LOAD. This will WRITE the fixture SUBJ-01 and keep it.")
    print(f"  host:     {where['host']}")
    print(f"  port:     {where['port']}")
    print(f"  database: {where['dbname']}")
    for question in (QUESTION_DATABASE, QUESTION_BACKUP):
        answer = ask(f"{question} Type yes to go on: ")
        if answer != "yes":
            print("Stopped. Nothing was written.")
            return 1

    data = read_seed_file()
    conn = None
    try:
        conn = open_connection(config.dsn)
        counts = load_subj01(conn, data)
        if counts["already_seeded"]:
            conn.rollback()
            print("already seeded: SUBJ-01 exists; nothing was changed")
            return 0
        conn.commit()
    except Exception as exc:
        # The type only: an error text can carry connection details.
        if conn is not None:
            conn.rollback()
        print(f"FAILED ({type(exc).__name__}); rolled back."
              " Nothing was written.")
        return 1
    finally:
        if conn is not None:
            conn.close()
    _print_counts(counts)
    print(f"COMMITTED to database '{where['dbname']}' on {where['host']}.")
    return 0


def main(argv=None, config=None, ask=input):
    parser = argparse.ArgumentParser(
        description="Load SUBJ-01. With no flags: a dry run on the TEST"
                    " database, rolled back.")
    parser.add_argument(
        "--commit", action="store_true",
        help="write for real; needs --target phsa as well")
    parser.add_argument(
        "--target", choices=[TARGET],
        help="the database to write to; needs --commit as well")
    args = parser.parse_args(argv)
    if args.commit != (args.target is not None):
        print("REFUSED: a real load needs BOTH --commit and --target phsa."
              " Nothing was written.")
        return 2
    if config is None:
        config = load_config()
    if args.commit:
        return _real_load(config, ask)
    return _dry_run(config)


if __name__ == "__main__":
    sys.exit(main())
