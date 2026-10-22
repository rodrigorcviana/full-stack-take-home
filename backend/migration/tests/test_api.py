from datetime import date

from django.test import TestCase

from migration.models import LinkStatus, Observation, Patient


class PatientApiTests(TestCase):
    def setUp(self):
        self.patient = Patient.objects.create(original_id="p-1", given_name="Percival", family_name="Pemberton", gender="male", birth_date=date(1983, 2, 27))
        self.nameless = Patient.objects.create(original_id="p-2", name_text="Jane Roe")
        Observation.objects.create(original_id="o-1", patient=self.patient, original_patient_id="p-1", link_status=LinkStatus.LINKED, status="final", code="1920-8", display="AST", value_type="quantity", value_number=16, value_unit="U/L")
        Observation.objects.create(original_id="o-orphan", original_patient_id="ghost", link_status=LinkStatus.ORPHAN, status="final")

    def test_list_shows_name_age_and_counts(self):
        data = self.client.get("/api/patients/").json()
        self.assertEqual([p["id"] for p in data], [self.patient.pk, self.nameless.pk])
        first = data[0]
        self.assertEqual(first["name"], "Percival Pemberton")
        self.assertEqual(first["observation_count"], 1)
        self.assertNotIn("original_id", first)  # source record id is never exposed
        self.assertEqual(data[1]["name"], "Jane Roe")  # free-text name fallback
        self.assertIsNone(data[1]["age"])

    def test_detail_includes_linked_observations_only(self):
        data = self.client.get(f"/api/patients/{self.patient.pk}/").json()
        self.assertEqual(data["name"], "Percival Pemberton")
        self.assertEqual(data["observation_count"], 1)
        self.assertEqual([o["display"] for o in data["observations"]], ["AST"])
        self.assertEqual(data["observations"][0]["value_number"], 16.0)
        self.assertNotIn("original_id", data["observations"][0])

    def test_detail_404_is_json(self):
        response = self.client.get("/api/patients/999/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not found"})

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post("/api/patients/").status_code, 405)
