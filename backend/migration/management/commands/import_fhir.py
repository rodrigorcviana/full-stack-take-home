import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from migration.fhir_client import FhirClient, FhirClientError
from migration.importer import Importer, ImporterError
from migration.models import ImportRun


class Command(BaseCommand):
    help = "Pull Patients then Observations from the FHIR server into the local DB. Safe to interrupt and re-run."

    def add_arguments(self, parser):
        parser.add_argument("--max-pages", type=int, default=None, help="Cap pages per phase (500 resources each). Default: all.")
        parser.add_argument("--only", choices=["patients", "observations"], help="Run a single phase.")
        parser.add_argument("--repair-orphans", action="store_true", help="Fetch missing patients for orphan observations.")
        parser.add_argument("--max-orphan-repairs", type=int, default=None, help="Cap distinct patients fetched by --repair-orphans.")
        parser.add_argument("--skip-verify", action="store_true", help="Skip the count comparison against the server.")

    def handle(self, *args, **opts):
        run = ImportRun.objects.create()
        importer = Importer(FhirClient(), run)
        self.stdout.write(f"import run #{run.pk} started")
        try:
            if opts["only"] in (None, "patients"):
                importer.import_patients(max_pages=opts["max_pages"])
            if opts["only"] in (None, "observations"):
                importer.import_observations(max_pages=opts["max_pages"])
            if opts["repair_orphans"]:
                importer.repair_orphans(limit=opts["max_orphan_repairs"])
            if not opts["skip_verify"]:
                importer.verify()
        except (FhirClientError, ImporterError) as exc:
            self._finish(run, ImportRun.Status.FAILED, str(exc))
            raise CommandError(f"import run #{run.pk} failed: {exc}. Re-run to resume.") from exc
        except KeyboardInterrupt:
            self._finish(run, ImportRun.Status.FAILED, "interrupted")
            raise CommandError(f"import run #{run.pk} interrupted. Re-run to resume.")
        self._finish(run, ImportRun.Status.SUCCEEDED)
        self.stdout.write(self.style.SUCCESS(f"import run #{run.pk} succeeded"))

    def _finish(self, run, status, error=""):
        run.status = status
        run.error = error
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        self.stdout.write(json.dumps(run.summary, indent=2, default=str))
