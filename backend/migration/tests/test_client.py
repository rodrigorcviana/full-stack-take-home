"""Retry behaviour is exercised against a real local HTTP server so the
urllib3 Retry configuration is what gets tested, not a mock of it."""

import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.test import SimpleTestCase

from migration.fhir_client import FhirClient, FhirClientError, build_session

from .fixtures import FULL_PATIENT, bundle


class ScriptedHandler(BaseHTTPRequestHandler):
    """Serves the status codes queued on the server, then the final body."""

    def do_GET(self):
        self.server.requests.append(self.path)
        status = self.server.statuses.pop(0) if self.server.statuses else 200
        body = b""
        if status == 200:
            body = json.dumps(self.server.body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/fhir+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output quiet
        pass


class LocalServer(HTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind calls socket.getfqdn(), a reverse DNS lookup
        # that can stall for ~30s on some machines. We never need the name.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.socket.getsockname()[1]


class FhirClientTests(SimpleTestCase):
    def setUp(self):
        self.server = LocalServer(("127.0.0.1", 0), ScriptedHandler)
        self.server.requests, self.server.statuses, self.server.body = [], [], FULL_PATIENT
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.client = FhirClient(
            base_url=f"http://127.0.0.1:{self.server.server_port}",
            session=build_session(total_retries=3, backoff_factor=0),
        )

    def test_transient_errors_are_retried_then_succeed(self):
        self.server.statuses = [503, 429]
        self.assertEqual(self.client.get_resource("Patient", "p-1")["id"], "p-1")
        self.assertEqual(len(self.server.requests), 3)

    def test_persistent_errors_raise_after_retries(self):
        self.server.statuses = [503] * 10
        with self.assertRaises(FhirClientError):
            self.client.get_resource("Patient", "p-1")
        self.assertEqual(len(self.server.requests), 4)  # 1 try + 3 retries

    def test_404_returns_none_without_retry(self):
        self.server.statuses = [404]
        self.assertIsNone(self.client.get_resource("Patient", "nope"))
        self.assertEqual(len(self.server.requests), 1)

    def test_iter_pages_sends_cursor_and_follows_next(self):
        port = self.server.server_port
        self.server.body = bundle([FULL_PATIENT], next_url=f"http://127.0.0.1:{port}/page2")
        pages = self.client.iter_pages("Patient", since="2026-09-25T15:17:09.173+00:00", max_pages=2)
        first = next(pages)
        self.assertEqual(first[0]["id"], "p-1")
        self.assertIn("_lastUpdated=ge2026-09-25T15%3A17%3A09.173%2B00%3A00", self.server.requests[0])
        self.assertIn("_sort=_lastUpdated", self.server.requests[0])
        next(pages)
        self.assertEqual(self.server.requests[1], "/page2")
        self.assertEqual(list(pages), [])  # max_pages reached
