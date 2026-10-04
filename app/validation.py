"""Server-side validation that mirrors the database constraints (spec 6.1).

Errors never reach the database as exceptions: on any error the form is
re-rendered with the user's input kept and HTTP 422 is returned, and
nothing is written.

Rules (spec 6.1):
- label is required (subjects)
- birth_year and birth_date, if both given, must agree (schema CHECK)
- enums come from <select> lists matching the schema CHECK lists exactly
- dose_value/dose_unit both given or both blank; code_system/code both
  given or both blank (M2/M3)
- frequency: positive integer (smallint range); period: positive number
- timing_when: multi-select from a fixed list of FHIR event-timing codes
"""

SEX_OPTIONS = ("male", "female", "other", "unknown")
# FHIR event timing codes (spec 6.1: one constant in code, easy to edit).
TIMING_WHEN_OPTIONS = (
    "MORN", "NOON", "AFT", "EVE", "NIGHT", "HS", "WAKE",
    "C", "CM", "CD", "CV", "AC", "ACM", "ACD", "ACV",
    "PC", "PCM", "PCD", "PCV",
)
CLINICAL_STATUS_OPTIONS = (
    "active", "recurrence", "relapse", "inactive", "remission", "resolved",
)
VERIFICATION_STATUS_OPTIONS = (
    "unconfirmed", "provisional", "differential", "confirmed",
    "refuted", "entered-in-error",
)
MED_STATUS_OPTIONS = (
    "active", "on-hold", "ended", "stopped", "completed",
    "cancelled", "entered-in-error", "draft", "unknown",
)
PERIOD_UNIT_OPTIONS = ("s", "min", "h", "d", "wk", "mo", "a")


def _text(value) -> str | None:
    """Form text: blank means blank (None, never "")."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def validate_subject(form) -> tuple[dict, list[str]]:
    """label is required; everything else blank means blank.

    is_test_data is intentionally not on the form and never read: every
    row the app writes has is_test_data = true as a constant in code
    (spec rule 4); a forged form value is ignored.
    """
    errors: list[str] = []
    label = _text(form.get("label"))
    if not label:
        errors.append("label is required")

    return {
        "label": label,
        "fixture_id": _text(form.get("fixture_id")),
    }, errors


def validate_demographics(form) -> tuple[dict, list[str]]:
    """All fields optional (blank means blank); the only cross-field rule
    for demographics is birth_year / birth_date agreement (spec 6.1, the
    schema's birth_year_matches CHECK)."""
    errors: list[str] = []
    d = {
        "family_name": _text(form.get("family_name")),
        "given_name": _text(form.get("given_name")),
        "usual_name": _text(form.get("usual_name")),
        "sex": _text(form.get("sex")),
        "home_municipality": _text(form.get("home_municipality")),
        "preferred_language": _text(form.get("preferred_language")),
        "birth_year": None,
        "birth_date": None,
    }

    if d["sex"] is not None and d["sex"] not in SEX_OPTIONS:
        d["sex"] = None  # a value outside the CHECK list is treated as blank

    year_raw = _text(form.get("birth_year"))
    if year_raw is not None:
        try:
            d["birth_year"] = int(year_raw)
        except ValueError:
            d["birth_year"] = None
            errors.append("birth_year must be an integer, or left blank")

    date_raw = _text(form.get("birth_date"))
    if date_raw is not None:
        try:
            # datetime.date.fromisoformat accepts YYYY-MM-DD.
            from datetime import date
            d["birth_date"] = date.fromisoformat(date_raw).isoformat()
        except ValueError:
            d["birth_date"] = None
            errors.append("birth_date must be an ISO date (YYYY-MM-DD), or left blank")

    # Both given, they must agree (the database CHECK would reject them).
    if not errors and d["birth_date"] is not None and d["birth_year"] is not None \
            and d["birth_year"] != int(d["birth_date"][0:4]):
        errors.append("birth_year and birth_date do not agree")

    return d, errors
