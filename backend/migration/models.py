from django.db import models


class Patient(models.Model):
    original_id = models.CharField(max_length=64, unique=True, help_text="FHIR Patient.id")
    given_name = models.CharField(max_length=255, blank=True)
    family_name = models.CharField(max_length=255, blank=True)
    name_text = models.CharField(max_length=255, blank=True, help_text="Used when the source only has free-text name")
    gender = models.CharField(max_length=16, null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    deceased = models.BooleanField(null=True, blank=True)
    source_last_updated = models.DateTimeField(null=True, db_index=True, help_text="FHIR meta.lastUpdated; import cursor")
    source_version_id = models.CharField(max_length=32, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Patient<{self.pk} original_id={self.original_id}>"


class LinkStatus(models.TextChoices):
    LINKED = "linked"
    ORPHAN = "orphan"  # subject is a Patient we don't have locally
    PATIENT_MISSING = "patient_missing"  # repair tried and the source returned 404
    NO_PATIENT_SUBJECT = "no_patient_subject"  # subject absent or not a Patient


class ValueType(models.TextChoices):
    QUANTITY = "quantity"
    STRING = "string"
    CODEABLE = "codeable"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    COMPONENTS = "components"
    NONE = "none"


class Observation(models.Model):
    original_id = models.CharField(max_length=64, unique=True, help_text="FHIR Observation.id")
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.CASCADE, related_name="observations")
    original_patient_id = models.CharField(max_length=64, null=True, blank=True, db_index=True, help_text="Patient id from Observation.subject")
    link_status = models.CharField(max_length=32, choices=LinkStatus.choices, default=LinkStatus.ORPHAN)

    status = models.CharField(max_length=32)
    category = models.CharField(max_length=64, blank=True)
    code_system = models.CharField(max_length=255, blank=True)
    code = models.CharField(max_length=64, blank=True)
    display = models.CharField(max_length=512, blank=True)
    effective_at = models.DateTimeField(null=True, blank=True)

    value_type = models.CharField(max_length=16, choices=ValueType.choices, default=ValueType.NONE)
    value_number = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    value_unit = models.CharField(max_length=64, blank=True)
    value_text = models.TextField(blank=True)
    components = models.JSONField(default=list, blank=True)

    source_last_updated = models.DateTimeField(null=True, db_index=True, help_text="FHIR meta.lastUpdated; import cursor")
    imported_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Observation<{self.pk} original_id={self.original_id}>"


class ImportRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    phase = models.CharField(max_length=32, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    def __str__(self):
        return f"ImportRun<{self.pk} {self.status}>"
