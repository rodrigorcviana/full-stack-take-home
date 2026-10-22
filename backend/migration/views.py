"""Read API over the migrated data.

Names are shown as they would be in an EHR. There is no authentication in this
exercise; in production the whole API sits behind auth and access logging
(see MIGRATION_PLAN.md). The source record id is never exposed.
"""

from datetime import date

from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import LinkStatus, Observation, Patient
from .schemas.internal import ObservationOut, PatientDetailOut, PatientSummaryOut


def _not_found():
    return JsonResponse({"detail": "Not found"}, status=404)


def _patients_queryset():
    return Patient.objects.annotate(
        observation_count=Count("observations", filter=Q(observations__link_status=LinkStatus.LINKED))
    ).order_by("id")


def full_name(patient: Patient) -> str:
    """'Given Family', else the source's free-text name, else a dash."""
    structured = " ".join(part for part in (patient.given_name, patient.family_name) if part)
    return structured or patient.name_text or "—"


def age_years(birth_date: date | None, today: date | None = None) -> int | None:
    if birth_date is None:
        return None
    today = today or date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def summarize(patient: Patient) -> dict:
    return {
        "id": patient.pk,
        "name": full_name(patient),
        "age": age_years(patient.birth_date),
        "gender": patient.gender,
        "observation_count": patient.observation_count,
    }


@require_GET
def patient_list(request):
    data = [PatientSummaryOut(**summarize(p)).model_dump(mode="json") for p in _patients_queryset()]
    return JsonResponse(data, safe=False)


@require_GET
def patient_detail(request, pk: int):
    linked = Observation.objects.filter(link_status=LinkStatus.LINKED).order_by("-effective_at", "id")
    patient = _patients_queryset().prefetch_related(Prefetch("observations", queryset=linked)).filter(pk=pk).first()
    if patient is None:
        return _not_found()
    observations = [ObservationOut.model_validate(o, from_attributes=True) for o in patient.observations.all()]
    out = PatientDetailOut(**summarize(patient), observations=observations)
    return JsonResponse(out.model_dump(mode="json"))
