"""Parameterised SQL. Schema-qualified everywhere (phsa.x).

Append-only rule (spec section 0.3): the only UPDATE the app may ever issue
is the single superseded_by pointer, in the shape
    UPDATE phsa.<table> SET superseded_by = %(new_id)s
        WHERE id = %(old_id)s AND superseded_by IS NULL
No other UPDATE, and no DELETE or TRUNCATE, may appear in this file.
M0 writes no SQL; the data layer is added by later milestones.
"""
