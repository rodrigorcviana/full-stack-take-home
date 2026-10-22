"""Small FHIR payloads mirroring shapes seen on the HAPI sandbox."""

FULL_PATIENT = {
    "resourceType": "Patient",
    "id": "p-1",
    "meta": {"versionId": "1", "lastUpdated": "2026-09-25T11:17:09.173-04:00"},
    "identifier": [{"system": "urn:test", "value": "123"}],
    "name": [
        {"use": "usual", "family": "Nick", "given": ["Percy"]},
        {"use": "official", "family": "Pemberton", "given": ["Percival", "James"]},
    ],
    "gender": "male",
    "birthDate": "1983-02-27",
    "address": [{"city": "Nowhere"}],
}

QUANTITY_OBSERVATION = {
    "resourceType": "Observation",
    "id": "o-1",
    "meta": {"versionId": "1", "lastUpdated": "2026-09-25T11:17:07.829-04:00"},
    "status": "final",
    "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
    "code": {
        "coding": [{"system": "http://loinc.org", "code": "1920-8", "display": "Aspartate aminotransferase"}],
        "text": "AST",
    },
    "subject": {"reference": "Patient/p-1"},
    "effectiveDateTime": "2026-01-15T09:00:00Z",
    "valueQuantity": {"value": 16.0, "unit": "U/L", "system": "http://unitsofmeasure.org", "code": "U/L"},
}

COMPONENT_OBSERVATION = {
    "resourceType": "Observation",
    "id": "o-bp",
    "meta": {"lastUpdated": "2026-10-21T11:57:24.612-04:00"},
    "status": "final",
    "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel"}]},
    "subject": {"reference": "https://hapi.fhir.org/baseR4/Patient/p-1"},
    "effectiveDateTime": "2026-10-21",
    "component": [
        {"code": {"coding": [{"system": "http://loinc.org", "code": "8480-6", "display": "Systolic"}]}, "valueQuantity": {"value": 120, "unit": "mmHg"}},
        {"code": {"coding": [{"system": "http://loinc.org", "code": "8462-4", "display": "Diastolic"}]}, "valueQuantity": {"value": 80, "unit": "mmHg"}},
    ],
}


def bundle(resources, next_url=None):
    links = [{"relation": "self", "url": "http://fhir.test/self"}]
    if next_url:
        links.append({"relation": "next", "url": next_url})
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "link": links,
        "entry": [{"resource": r, "search": {"mode": "match"}} for r in resources],
    }
