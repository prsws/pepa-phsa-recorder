"""Builders for the FHIR-shaped JSON the app writes (spec section 5).

Include a key only when the value was entered; never write null or empty
strings. A blank means blank: missing data stays absent from the JSON.
"""

import json

HTEST_TAG = {
    "system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
    "code": "HTEST",
}

_SEX_TO_GENDER = {
    "male": "male",
    "female": "female",
    "other": "other",
    "unknown": "unknown",
}

# JSON keys in canonical order (keys are only present when the value was
# entered); the bytea hash of this exact string is what the database stores
# in content_sha256 (spec 4.1: hash of PostgreSQL's own jsonb text output).
_CANONICAL_KEYS = (
    "resourceType", "id", "meta",
    "name", "gender", "birthDate", "address", "communication",
    "clinicalStatus", "verificationStatus", "code", "subject",
    "onsetString", "onsetDateTime", "note",
    "status", "intent", "medicationCodeableConcept",
    "authoredOn", "dosageInstruction",
)


def build_resource(resource_type: str, resource_id: str, fields: dict) -> str:
    """One FHIR-shaped resource as a JSON string.

    `fields` maps top-level keys to their values (None / "" / [] entries are
    dropped). The result always starts with resourceType, id, meta(HTEST).
    """
    doc: dict = {"resourceType": resource_type, "id": resource_id,
                 "meta": {"security": [HTEST_TAG]}}
    for key, value in fields.items():
        if value in (None, "", []):
            continue
        doc[key] = value
    ordered = {k: doc[k] for k in _CANONICAL_KEYS if k in doc}
    ordered.update({k: v for k, v in doc.items() if k not in ordered})
    return json.dumps(ordered, ensure_ascii=False)


def patient_json(resource_id: str, d) -> str:
    """Patient resource (spec 5.1), for subject_demographics.

    `d` is the validated field dict from validation (None / "" / [] mean
    blank). Blank inputs stay blank: no key is written for them.
    """
    name = []
    if d["family_name"] or d["given_name"]:
        entry = {"use": "official"}
        if d["family_name"]:
            entry["family"] = d["family_name"]
        if d["given_name"]:
            entry["given"] = [d["given_name"]]
        name.append(entry)
    if d["usual_name"]:
        name.append({"use": "usual", "text": d["usual_name"]})

    address = None
    if d["home_municipality"]:
        address = [{"city": d["home_municipality"]}]

    communication = None
    if d["preferred_language"]:
        communication = [{"language": {"text": d["preferred_language"]}}]

    return build_resource("Patient", resource_id, {
        "name": name or None,
        "gender": _SEX_TO_GENDER.get(d["sex"]) if d["sex"] else None,
        "birthDate": d["birth_date"],
        "address": address,
        "communication": communication,
    })
