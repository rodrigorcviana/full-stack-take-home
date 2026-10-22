"""Thin HTTP client for a FHIR R4 server with retry/backoff.

Failure policy: transient errors (429, 5xx, connection/read errors) are
retried with exponential backoff, honouring Retry-After. When retries are
exhausted a FhirClientError is raised and the caller decides what to do;
the importer stops the run, and the next run resumes from the DB cursor.
"""

import logging
from collections.abc import Iterator

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

RETRY_STATUSES = (429, 500, 502, 503, 504)


class FhirClientError(Exception):
    """The FHIR server could not be reached or answered with an error after retries."""


def build_session(total_retries: int = 5, backoff_factor: float = 1.0) -> requests.Session:
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["Accept"] = "application/fhir+json"
    return session


class FhirClient:
    def __init__(
        self,
        base_url: str | None = None,
        page_size: int | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10, 60),
    ):
        self.base_url = (base_url or settings.FHIR_BASE_URL).rstrip("/")
        self.page_size = page_size or settings.FHIR_PAGE_SIZE
        self.session = session or build_session()
        self.timeout = timeout

    # --- low level -------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        logger.debug("GET %s params=%s", url, params)
        try:
            return self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FhirClientError(f"request failed after retries: {url}: {exc}") from exc

    def get_json(self, url: str, params: dict | None = None) -> dict:
        response = self._get(url, params)
        if response.status_code >= 400:
            raise FhirClientError(f"HTTP {response.status_code} from {response.url}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise FhirClientError(f"non-JSON response from {response.url}") from exc

    # --- FHIR operations -------------------------------------------------

    def get_resource(self, resource_type: str, resource_id: str) -> dict | None:
        """Read one resource. Returns None when the server says 404 (or 410 gone)."""
        response = self._get(f"{self.base_url}/{resource_type}/{resource_id}")
        if response.status_code in (404, 410):
            return None
        if response.status_code >= 400:
            raise FhirClientError(f"HTTP {response.status_code} reading {resource_type}/{resource_id}")
        return response.json()

    def search(self, resource_type: str, params: dict | None = None) -> dict:
        return self.get_json(f"{self.base_url}/{resource_type}", params)

    def count(self, resource_type: str, params: dict | None = None) -> int | None:
        """Server-side total via _summary=count. None when the server omits it."""
        bundle = self.search(resource_type, {**(params or {}), "_summary": "count"})
        return bundle.get("total")

    def iter_pages(
        self,
        resource_type: str,
        since: str | None = None,
        max_pages: int | None = None,
        extra_params: dict | None = None,
    ) -> Iterator[list[dict]]:
        """Yield one list of resources per page, ordered by meta.lastUpdated.

        The first page is a fresh search (restartable from `since`); later pages
        follow the server's `next` link, which is only valid during this run.
        Only `search.mode == match` entries are returned.
        """
        params = {"_sort": "_lastUpdated", "_count": self.page_size, **(extra_params or {})}
        if since:
            params["_lastUpdated"] = f"ge{since}"
        bundle = self.search(resource_type, params)
        page = 0
        while True:
            page += 1
            yield [
                entry["resource"]
                for entry in bundle.get("entry", [])
                if "resource" in entry and entry.get("search", {}).get("mode", "match") == "match"
            ]
            next_url = next((link["url"] for link in bundle.get("link", []) if link.get("relation") == "next"), None)
            if not next_url or (max_pages is not None and page >= max_pages):
                return
            bundle = self.get_json(next_url)
