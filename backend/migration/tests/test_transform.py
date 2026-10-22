from datetime import date, datetime, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from migration.transform import TransformError, parse_patient_reference, transform_observation, transform_patient

from .fixtures import COMPONENT_OBSERVATION, FULL_PATIENT, QUANTITY_OBSERVATION


class TransformPatientTests(SimpleTestCase):
    def test_prefers_official_name_and_keeps_original_id(self):
        rec = transform_patient(FULL_PATIENT)
        self.assertEqual(rec.original_id, "p-1")
        self.assertEqual((rec.given_name, rec.family_name), ("Percival James", "Pemberton"))
        self.assertEqual(rec.birth_date, date(1983, 2, 27))
        self.assertEqual(rec.gender, "male")
        self.assertEqual(rec.source_last_updated, datetime(2026, 9, 25, 15, 17, 9, 173000, tzinfo=timezone.utc))
        self.assertEqual(rec.source_version_id, "1")

    def test_tolerates_missing_name_birthdate_and_gender(self):
        rec = transform_patient({"resourceType": "Patient", "id": "bare"})
        self.assertEqual((rec.given_name, rec.family_name, rec.name_text), ("", "", ""))
        self.assertIsNone(rec.birth_date)
        self.assertIsNone(rec.gender)
        self.assertIsNone(rec.deceased)

    def test_text_only_name_and_partial_birthdate(self):
        rec = transform_patient({"resourceType": "Patient", "id": "t", "name": [{"text": "Jane Roe"}], "birthDate": "1990-07", "deceasedDateTime": "2020-01-01"})
        self.assertEqual(rec.name_text, "Jane Roe")
        self.assertIsNone(rec.birth_date)  # partial date is not stored
        self.assertTrue(rec.deceased)

    def test_wrong_resource_type_is_rejected(self):
        with self.assertRaises(TransformError):
            transform_patient({"resourceType": "Observation", "id": "x"})


class TransformObservationTests(SimpleTestCase):
    def test_quantity_observation(self):
        rec = transform_observation(QUANTITY_OBSERVATION)
        self.assertEqual(rec.original_patient_id, "p-1")
        self.assertEqual((rec.code_system, rec.code, rec.display), ("http://loinc.org", "1920-8", "Aspartate aminotransferase"))
        self.assertEqual(rec.category, "laboratory")
        self.assertEqual(rec.value_type, "quantity")
        self.assertEqual(rec.value_number, Decimal("16.0"))
        self.assertEqual(rec.value_unit, "U/L")
        self.assertEqual(rec.effective_at, datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc))

    def test_component_only_observation_with_absolute_reference_and_date_only(self):
        rec = transform_observation(COMPONENT_OBSERVATION)
        self.assertEqual(rec.original_patient_id, "p-1")
        self.assertEqual(rec.value_type, "components")
        self.assertIsNone(rec.value_number)
        self.assertEqual([c.model_dump() for c in rec.components], [
            {"code": "8480-6", "display": "Systolic", "value": 120.0, "unit": "mmHg"},
            {"code": "8462-4", "display": "Diastolic", "value": 80.0, "unit": "mmHg"},
        ])
        self.assertEqual(rec.effective_at, datetime(2026, 10, 21, tzinfo=timezone.utc))

    def test_string_value_and_text_only_code(self):
        rec = transform_observation({
            "resourceType": "Observation", "id": "s", "status": "final",
            "code": {"text": "Measles IgG"}, "subject": {"reference": "Patient/p-1"},
            "valueString": "Positive",
        })
        self.assertEqual((rec.code, rec.display), ("", "Measles IgG"))
        self.assertEqual((rec.value_type, rec.value_text), ("string", "Positive"))

    def test_non_patient_subject_and_missing_code(self):
        rec = transform_observation({"resourceType": "Observation", "id": "g", "status": "final", "code": {"text": "x"}, "subject": {"reference": "Group/1"}})
        self.assertIsNone(rec.original_patient_id)
        with self.assertRaises(TransformError):
            transform_observation({"resourceType": "Observation", "id": "nocode", "status": "final"})

    def test_parse_patient_reference(self):
        self.assertEqual(parse_patient_reference("Patient/abc-1"), "abc-1")
        self.assertEqual(parse_patient_reference("Patient/abc-1/_history/3"), "abc-1")
        self.assertIsNone(parse_patient_reference("Device/9"))
        self.assertIsNone(parse_patient_reference("urn:uuid:1234"))
        self.assertIsNone(parse_patient_reference(None))
