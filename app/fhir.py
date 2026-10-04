"""Builders for the FHIR-shaped JSON the app writes (spec section 5).

Include a key only when the value was entered; never write null or empty
strings. Added by later milestones (M1-M3); the module exists so the repo
layout matches the spec from day one.
"""

HTEST_TAG = {
    "system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
    "code": "HTEST",
}
