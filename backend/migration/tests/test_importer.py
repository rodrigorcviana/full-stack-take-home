from unittest.mock import MagicMock

from django.test import TestCase

from migration.importer import Importer, ImporterError
from migration.models import ImportRun, LinkStatus, Observation, Patient

from .fixtures import COMPONENT_OBSERVATION, FULL_PATIENT, QUANTITY_OBSERVATION


def fake_client(pages_by_type=None, resources=None):
    client = MagicMock()
    client.iter_pages.side_effect = lambda resource_type, **kw: iter((pages_by_type or {}).get(resource_type, []))
    client.get_resource.side_effect = lambda resource_type, rid: (resources or {}).get(f"{resource_type}/{rid}")
    client.count.return_value = None
    return client


class ImportPatientsTests(TestCase):
    def test_upserts_and_resumes_from_newest_lastupdated(self):
        older = {**FULL_PATIENT, "id": "p-0", "meta": {"lastUpdated": "2026-09-01T00:00:00Z"}}
        client = fake_client({"Patient": [[older, FULL_PATIENT], [{**FULL_PATIENT, "gender": "female"}]]})
        importer = Importer(client, ImportRun.objects.create())

        stats = importer.import_patients()

        self.assertEqual(Patient.objects.count(), 2)  # p-1 appeared twice, upserted once
        self.assertEqual(Patient.objects.get(original_id="p-1").gender, "female")
        self.assertEqual(stats["pages"], 2)
        self.assertIsNone(stats["resumed_from"])
        client.iter_pages.assert_called_once_with("Patient", since=None, max_pages=None)

        importer.import_patients()  # second run resumes from the newest stored timestamp
        client.iter_pages.assert_called_with("Patient", since="2026-09-25T15:17:09.173+00:00", max_pages=None)

    def test_rejected_resources_are_counted_not_fatal(self):
        client = fake_client({"Patient": [[FULL_PATIENT, {"resourceType": "Patient"}]]})
        stats = Importer(client, ImportRun.objects.create()).import_patients()
        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(stats["rejected"], 1)


class ImportObservationsTests(TestCase):
    def test_links_to_local_patients_and_flags_orphans(self):
        client = fake_client({"Patient": [[FULL_PATIENT]], "Observation": [[
            QUANTITY_OBSERVATION,
            COMPONENT_OBSERVATION,
            {**QUANTITY_OBSERVATION, "id": "o-orphan", "subject": {"reference": "Patient/missing"}},
            {**QUANTITY_OBSERVATION, "id": "o-group", "subject": {"reference": "Group/1"}},
        ]]})
        importer = Importer(client, ImportRun.objects.create())
        importer.import_patients()
        stats = importer.import_observations()

        patient = Patient.objects.get(original_id="p-1")
        by_id = {o.original_id: o for o in Observation.objects.all()}
        self.assertEqual(by_id["o-1"].patient, patient)
        self.assertEqual(by_id["o-bp"].link_status, LinkStatus.LINKED)
        self.assertEqual(by_id["o-orphan"].link_status, LinkStatus.ORPHAN)
        self.assertEqual(by_id["o-orphan"].original_patient_id, "missing")
        self.assertEqual(by_id["o-group"].link_status, LinkStatus.NO_PATIENT_SUBJECT)
        self.assertEqual((stats["linked"], stats["orphans"], stats["no_patient_subject"]), (2, 1, 1))

    def test_refuses_to_run_before_patients(self):
        with self.assertRaises(ImporterError):
            Importer(fake_client(), ImportRun.objects.create()).import_observations()


class RepairOrphansTests(TestCase):
    def test_fetches_missing_patient_or_marks_patient_missing(self):
        Patient.objects.create(original_id="p-1")
        Observation.objects.create(original_id="a", original_patient_id="late", link_status=LinkStatus.ORPHAN, status="final")
        Observation.objects.create(original_id="b", original_patient_id="late", link_status=LinkStatus.ORPHAN, status="final")
        Observation.objects.create(original_id="c", original_patient_id="gone", link_status=LinkStatus.ORPHAN, status="final")
        client = fake_client(resources={"Patient/late": {**FULL_PATIENT, "id": "late"}})

        stats = Importer(client, ImportRun.objects.create()).repair_orphans()

        late = Patient.objects.get(original_id="late")
        self.assertEqual(Observation.objects.filter(patient=late, link_status=LinkStatus.LINKED).count(), 2)
        self.assertEqual(Observation.objects.get(original_id="c").link_status, LinkStatus.PATIENT_MISSING)
        self.assertEqual((stats["repaired_patients"], stats["linked"], stats["patient_missing"]), (1, 2, 1))
