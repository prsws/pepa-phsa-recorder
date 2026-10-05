"""M5b: the real-load path of the seed script (--commit --target phsa).

No test here opens a database connection. The connection and the loader are
replaced by stand-ins, so nothing can reach the real database from a test.
Written by the reviewer at José's direction, not by the builder.
"""
import pathlib
from types import SimpleNamespace

import pytest

from scripts import seed_subj01
from test_crs_words import _violations

SECRET = "s3cr3t-not-a-real-one"
REAL_DSN = f"host=db.example dbname=phsa user=someone password={SECRET}"
TEST_DSN = f"host=db.example dbname=phsa_test user=someone password={SECRET}"
LOADED = {"already_seeded": False, "subject": 1, "demographics": 1,
          "conditions": 7, "medication_requests": 7, "source_records": 15}
SEEDED = {"already_seeded": True, "subject": 0, "demographics": 0,
          "conditions": 0, "medication_requests": 0, "source_records": 0}


class FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


class Rig:
    """Stand-ins for the connection, the loader and the keyboard."""

    def __init__(self, monkeypatch, answers=(), result=LOADED, dsn=REAL_DSN,
                 test_dsn=TEST_DSN):
        self.config = SimpleNamespace(dsn=dsn, test_dsn=test_dsn)
        self.opened = []
        self.conn = FakeConn()
        self.loads = 0
        self.asked = []
        self._answers = list(answers)
        self._result = result
        monkeypatch.setattr(seed_subj01, "open_connection", self._open)
        monkeypatch.setattr(seed_subj01, "load_subj01", self._load)

    def _open(self, dsn):
        self.opened.append(dsn)
        return self.conn

    def _load(self, conn, data):
        self.loads += 1
        if isinstance(self._result, Exception):
            raise self._result
        return dict(self._result)

    def ask(self, prompt):
        self.asked.append(prompt)
        return self._answers.pop(0)

    def run(self, argv):
        return seed_subj01.main(argv, config=self.config, ask=self.ask)

    def nothing_written(self):
        return self.opened == [] and self.loads == 0 and self.conn.commits == 0


def _clean(out):
    """The output never shows the password or a whole connection string."""
    return SECRET not in out and "password" not in out and REAL_DSN not in out


@pytest.mark.parametrize("argv", [["--commit"], ["--target", "phsa"]])
def test_one_flag_alone_is_refused(monkeypatch, capsys, argv):
    rig = Rig(monkeypatch)
    assert rig.run(argv) == 2
    assert rig.nothing_written() and rig.asked == []
    assert "REFUSED" in capsys.readouterr().out


@pytest.mark.parametrize("target", ["phsa_test", "other", ""])
def test_any_other_target_is_refused(monkeypatch, target):
    rig = Rig(monkeypatch)
    with pytest.raises(SystemExit) as stop:
        rig.run(["--commit", "--target", target])
    assert stop.value.code == 2
    assert rig.nothing_written() and rig.asked == []


def test_the_two_questions_are_the_ones_in_the_spec():
    assert seed_subj01.QUESTION_DATABASE == (
        "Is this the database you intend to write to?")
    assert seed_subj01.QUESTION_BACKUP == (
        "Has a fresh backup of the database VM been taken since the last"
        " schema change?")


@pytest.mark.parametrize("answers, asked", [
    (["no"], 1), ([""], 1), (["y"], 1), (["YES"], 1), (["Yes"], 1),
    ([" yes"], 1), (["yes "], 1), (["yes", "no"], 2), (["yes", ""], 2),
    (["yes", "y"], 2), (["yes", "YES"], 2),
])
def test_anything_but_yes_twice_writes_nothing(monkeypatch, capsys, answers,
                                               asked):
    rig = Rig(monkeypatch, answers=answers)
    assert rig.run(["--commit", "--target", "phsa"]) == 1
    assert len(rig.asked) == asked
    assert rig.nothing_written()
    out = capsys.readouterr().out
    assert "Nothing was written" in out and _clean(out)


def test_yes_twice_loads_and_commits_once(monkeypatch, capsys):
    rig = Rig(monkeypatch, answers=["yes", "yes"])
    assert rig.run(["--commit", "--target", "phsa"]) == 0
    assert rig.opened == [REAL_DSN]
    assert rig.loads == 1
    assert (rig.conn.commits, rig.conn.rollbacks, rig.conn.closed) == (1, 0, 1)
    assert seed_subj01.QUESTION_DATABASE in rig.asked[0]
    assert seed_subj01.QUESTION_BACKUP in rig.asked[1]
    out = capsys.readouterr().out
    for text in ("db.example", "database: phsa", "REAL LOAD", "COMMITTED",
                 "subject: 1", "conditions: 7", "medication requests: 7",
                 "source records: 15"):
        assert text in out
    assert _clean(out)


def test_the_database_is_named_before_the_first_question(monkeypatch, capsys):
    seen = []
    rig = Rig(monkeypatch, answers=["no"])

    def ask(prompt):
        seen.append(capsys.readouterr().out)
        return "no"

    assert seed_subj01.main(["--commit", "--target", "phsa"],
                            config=rig.config, ask=ask) == 1
    assert "db.example" in seen[0] and "database: phsa" in seen[0]


def test_already_seeded_changes_nothing(monkeypatch, capsys):
    rig = Rig(monkeypatch, answers=["yes", "yes"], result=SEEDED)
    assert rig.run(["--commit", "--target", "phsa"]) == 0
    assert (rig.conn.commits, rig.conn.rollbacks, rig.conn.closed) == (0, 1, 1)
    out = capsys.readouterr().out
    assert "already seeded" in out and "COMMITTED" not in out and _clean(out)


def test_a_failed_load_rolls_back_and_shows_no_details(monkeypatch, capsys):
    boom = RuntimeError(f"could not connect: {REAL_DSN}")
    rig = Rig(monkeypatch, answers=["yes", "yes"], result=boom)
    assert rig.run(["--commit", "--target", "phsa"]) == 1
    assert (rig.conn.commits, rig.conn.rollbacks, rig.conn.closed) == (0, 1, 1)
    out = capsys.readouterr().out
    assert "FAILED (RuntimeError)" in out and "COMMITTED" not in out
    assert _clean(out)


def test_a_failed_connection_writes_nothing(monkeypatch, capsys):
    rig = Rig(monkeypatch, answers=["yes", "yes"])

    def refuse(dsn):
        raise OSError(f"no route: {dsn}")

    monkeypatch.setattr(seed_subj01, "open_connection", refuse)
    assert rig.run(["--commit", "--target", "phsa"]) == 1
    assert rig.loads == 0 and rig.conn.commits == 0
    out = capsys.readouterr().out
    assert "FAILED (OSError)" in out and _clean(out)


@pytest.mark.parametrize("dsn", [
    TEST_DSN,
    f"postgresql://someone:{SECRET}@db.example:5432/phsa_test",
    f"host=db.example user=someone password={SECRET}",
    "this is not a connection string =",
])
def test_a_dsn_that_is_not_the_phsa_database_is_refused(monkeypatch, capsys,
                                                        dsn):
    rig = Rig(monkeypatch, answers=["yes", "yes"], dsn=dsn)
    assert rig.run(["--commit", "--target", "phsa"]) == 2
    assert rig.nothing_written() and rig.asked == []
    out = capsys.readouterr().out
    assert "REFUSED" in out and _clean(out)


def test_the_same_dsn_for_real_and_test_is_refused(monkeypatch, capsys):
    rig = Rig(monkeypatch, answers=["yes", "yes"], test_dsn=REAL_DSN)
    assert rig.run(["--commit", "--target", "phsa"]) == 2
    assert rig.nothing_written() and rig.asked == []
    assert "REFUSED" in capsys.readouterr().out


def test_a_url_form_dsn_is_read_the_same_way(monkeypatch, capsys):
    dsn = f"postgresql://someone:{SECRET}@db.example:6543/phsa"
    rig = Rig(monkeypatch, answers=["yes", "yes"], dsn=dsn)
    assert rig.run(["--commit", "--target", "phsa"]) == 0
    assert rig.opened == [dsn] and rig.conn.commits == 1
    out = capsys.readouterr().out
    assert "db.example" in out and "6543" in out and "database: phsa" in out
    assert SECRET not in out and dsn not in out


def test_dry_run_never_asks_never_commits_and_uses_the_test_dsn(monkeypatch,
                                                                capsys):
    rig = Rig(monkeypatch)
    assert rig.run([]) == 0
    assert rig.opened == [TEST_DSN]
    assert rig.asked == []
    assert (rig.conn.commits, rig.conn.rollbacks, rig.conn.closed) == (0, 1, 1)
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "rolled back" in out and _clean(out)


def test_scripts_keep_to_the_three_verbs():
    files = sorted((pathlib.Path(seed_subj01.__file__).parent).glob("*.py"))
    assert files
    for path in files:
        found = _violations(path.read_text())
        assert found == [], f"{path.name}: {found}"
