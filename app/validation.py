"""Server-side validation that mirrors the database constraints (spec 6.1).

Errors never reach the database as exceptions: on any error the form is
re-rendered with the user's input kept and HTTP 422 is returned, and
nothing is written.

Rules (spec 6.1):
- label is required (subjects)
- birth_year and birth_date, if both given, must agree (schema CHECK)
- onset_text and onset_date: never both given (conditions; spec v0.5)
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


def validate_condition(form) -> tuple[dict, list[str]]:
    """Condition create (spec 6.1): `code_text` and `clinical_status` are
    required; `code_system` and `code` are both given or both blank (the
    schema's code_pair CHECK); the selects match the schema CHECK lists
    exactly. Blank means blank (None, never "")."""
    errors: list[str] = []
    d = {
        "code_text": _text(form.get("code_text")),
        "code_system": _text(form.get("code_system")),
        "code": _text(form.get("code")),
        "clinical_status": _text(form.get("clinical_status")),
        "verification_status": _text(form.get("verification_status")),
        "onset_text": _text(form.get("onset_text")),
        "onset_date": None,
        "note": _text(form.get("note")),
    }

    if not d["code_text"]:
        errors.append("code_text is required")
    if not d["clinical_status"]:
        errors.append("clinical_status is required")

    if d["clinical_status"] is not None and d["clinical_status"] not in CLINICAL_STATUS_OPTIONS:
        d["clinical_status"] = None
        errors.append("clinical_status is not a recognised status")

    # code_system and code: both given or both blank (spec 6.1; the schema's
    # code_pair CHECK would otherwise raise instead of a friendly 422).
    if bool(d["code_system"]) != bool(d["code"]):
        d["code_system"] = None
        d["code"] = None
        errors.append("code_system and code must both be given, or both be blank")

    if d["verification_status"] is not None and d["verification_status"] not in VERIFICATION_STATUS_OPTIONS:
        d["verification_status"] = None
        errors.append("verification_status is not a recognised status")

    date_raw = _text(form.get("onset_date"))
    date_given = date_raw is not None
    onset_text_given = d["onset_text"] is not None
    if date_raw is not None:
        try:
            # datetime.date.fromisoformat accepts YYYY-MM-DD.
            from datetime import date
            d["onset_date"] = date.fromisoformat(date_raw).isoformat()
        except ValueError:
            d["onset_date"] = None
            errors.append("onset_date must be an ISO date (YYYY-MM-DD), or left blank")

    # onset_text and onset_date: never both given (spec 6.1, v0.5; the schema
    # has no CHECK between the two columns, so this is a form rule only).
    # The user's input is kept so the form re-renders unchanged.
    if date_given and onset_text_given:
        errors.append("onset_date and onset_text: give one or the other, not both")

    return d, errors


def validate_medication_request(form) -> tuple[dict, list[str]]:
    """Medication request create (spec 6.1): `medication_text` and `status`
    are required; `medication_code_system` and `medication_code` both given
    or both blank (the schema's med_code_pair CHECK); `dose_value` and
    `dose_unit` both given or both blank (the schema's dose_pair CHECK);
    `frequency` a positive integer in smallint range; `period` a positive
    number; the selects match the schema CHECK lists exactly (status
    includes `entered-in-error` — a normal schema value, kept; the
    supersede/mark-error routes that set it are M4). Blank means blank
    (None, never ""). `intent` and `safety_critical` are not on the form;
    they are left to the schema defaults."""
    errors: list[str] = []
    d = {
        "medication_text": _text(form.get("medication_text")),
        "medication_code_system": _text(form.get("medication_code_system")),
        "medication_code": _text(form.get("medication_code")),
        "status": _text(form.get("status")),
        "authored_on": None,
        "sig_text": _text(form.get("sig_text")),
        "dose_value": None,
        "dose_unit": _text(form.get("dose_unit")),
        "dose_unit_code": _text(form.get("dose_unit_code")),
        "route_text": _text(form.get("route_text")),
        "frequency": None,
        "period": None,
        "period_unit": _text(form.get("period_unit")),
        "timing_when": [v for v in form.getlist("timing_when") if v in TIMING_WHEN_OPTIONS],
        "note": _text(form.get("note")),
    }

    if not d["medication_text"]:
        errors.append("medication_text is required")
    if not d["status"]:
        errors.append("status is required")

    if d["status"] is not None and d["status"] not in MED_STATUS_OPTIONS:
        d["status"] = None
        errors.append("status is not a recognised status")

    if d["period_unit"] is not None and d["period_unit"] not in PERIOD_UNIT_OPTIONS:
        d["period_unit"] = None
        errors.append("period_unit is not a recognised unit")

    # medication_code_system and medication_code: both given or both blank
    # (the schema's med_code_pair CHECK would otherwise raise).
    if bool(d["medication_code_system"]) != bool(d["medication_code"]):
        d["medication_code_system"] = None
        d["medication_code"] = None
        errors.append(
            "medication_code_system and medication_code must both be given, or both be blank"
        )

    # dose_value and dose_unit: both given or both blank (the schema's
    # dose_pair CHECK).
    dose_given = _text(form.get("dose_value")) is not None
    dose_unit_given = d["dose_unit"] is not None
    value = None
    if dose_given:
        try:
            value = float(form.get("dose_value").strip())
        except ValueError:
            value = None
            errors.append("dose_value must be a positive number, or left blank")
        if value is not None and value <= 0:
            value = None
            errors.append("dose_value must be a positive number, or left blank")
    if dose_given != dose_unit_given:
        d["dose_value"] = None
        d["dose_unit"] = None
        d["dose_unit_code"] = None
        errors.append(
            "dose_value and dose_unit must both be given, or both be blank"
        )
    else:
        d["dose_value"] = value
        if dose_unit_given and not d["dose_unit_code"]:
            d["dose_unit_code"] = None  # optional; blank means absent

    # frequency: a positive integer in smallint range.
    freq_raw = _text(form.get("frequency"))
    if freq_raw is not None:
        try:
            freq = int(freq_raw)
            if 1 <= freq <= 32767:  # smallint range, positive
                d["frequency"] = freq
            else:
                d["frequency"] = None
                errors.append("frequency must be a positive whole number")
        except ValueError:
            d["frequency"] = None
            errors.append("frequency must be a positive whole number")

    # period: a positive number (FHIR periods can be fractional).
    period_raw = _text(form.get("period"))
    if period_raw is not None:
        try:
            period = float(period_raw)
            if period > 0:
                d["period"] = period
            else:
                d["period"] = None
                errors.append("period must be a positive number")
        except ValueError:
            d["period"] = None
            errors.append("period must be a positive number")

    date_raw = _text(form.get("authored_on"))
    if date_raw is not None:
        try:
            # datetime.date.fromisoformat accepts YYYY-MM-DD.
            from datetime import date
            d["authored_on"] = date.fromisoformat(date_raw).isoformat()
        except ValueError:
            d["authored_on"] = None
            errors.append("authored_on must be an ISO date (YYYY-MM-DD), or left blank")

    return d, errors


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
