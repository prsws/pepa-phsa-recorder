"""Load the synthetic fixture person SUBJ-01 (spec section 8).

Dry run only: it loads everything into the TEST database inside one
transaction, prints the counts, and rolls back. Nothing is kept.
Run it from the repository root:  .venv/bin/python -m scripts.seed_subj01
"""
import argparse
import json
import pathlib
import sys

from werkzeug.datastructures import MultiDict

from app import queries, validation
from app.config import load_config
from app.db import open_connection

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED_FILE = ROOT / "seed" / "subj01.json"


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


def main(argv=None, config=None):
    parser = argparse.ArgumentParser(
        description="Load SUBJ-01 into the TEST database, then roll back.")
    parser.parse_args(argv)
    if config is None:
        config = load_config()
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
        print(f"subject: {counts['subject']}")
        print(f"demographics: {counts['demographics']}")
        print(f"conditions: {counts['conditions']}")
        print(f"medication requests: {counts['medication_requests']}")
        print(f"source records: {counts['source_records']}")
    print("rolled back; nothing was kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
