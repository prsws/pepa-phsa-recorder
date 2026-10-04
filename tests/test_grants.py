"""M0: the role's grants, proved against phsa_test (spec section 9).

- DELETE from phsa.condition raises insufficient_privilege
- TRUNCATE phsa.condition raises insufficient_privilege
- UPDATE of any non-pointer column raises insufficient_privilege
- UPDATE of the single superseded_by pointer is permitted
- INSERT into phsa.record_confirmation raises insufficient_privilege

Every test runs inside a transaction that is rolled back (fixture `conn`).
The only statement that writes is the permitted superseded_by UPDATE on a
nonexistent id, which matches 0 rows and commits nothing.
"""

import uuid

import psycopg


def test_delete_forbidden(conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM phsa.condition")


def test_truncate_forbidden(conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("TRUNCATE phsa.condition")


def test_update_non_pointer_column_forbidden(conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE phsa.condition SET code_text = 'x'")


def test_update_superseded_by_permitted(conn):
    new_id, old_id = uuid.uuid7(), uuid.uuid7()
    # A nonexistent id: the UPDATE is permitted but matches 0 rows and writes nothing.
    assert conn.execute(
        "SELECT count(*) FROM phsa.condition WHERE id = %s", (old_id,)
    ).fetchone()["count"] == 0
    conn.execute(
        "UPDATE phsa.condition SET superseded_by = %s WHERE id = %s",
        (new_id, old_id),
    )


def test_record_confirmation_insert_forbidden(conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "INSERT INTO phsa.record_confirmation (source_record_id, action, confirmed_by) "
            "VALUES (%s, 'confirmed', 'grant-test')",
            (uuid.uuid7(),),
        )
