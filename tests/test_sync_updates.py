from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

import accounting
import sync


class SyncUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for target, filename in (("accounting.DB_PATH", "accounting.db"), ("sync_state.DB_PATH", "sync.db")):
            setting = patch(target, Path(temporary.name) / filename)
            setting.start()
            self.addCleanup(setting.stop)
        accounting.remaining_failures = 0
        self.accounting = TestClient(accounting.app)
        self.addCleanup(self.accounting.close)
        self.source = [{"id": "crm-001", "company_name": "Acme", "billing_email": "old@acme.example"}]
        self.lose_update_response = False
        self.transport = httpx.MockTransport(self.handle)
        self.client_class = httpx.Client

    def handle(self, request):
        if request.url.port == 8000:
            return httpx.Response(200, json=self.source)
        kwargs = {"json": json.loads(request.content)} if request.content else {}
        response = self.accounting.request(request.method, request.url.path, **kwargs)
        if request.method == "PATCH" and self.lose_update_response:
            self.lose_update_response = False
            raise httpx.ReadTimeout("Response lost after commit", request=request)
        return httpx.Response(response.status_code, json=response.json())

    def run_sync(self):
        output = io.StringIO()
        client = self.client_class(transport=self.transport)
        with patch("sync.httpx.Client", return_value=client), patch("sync.sleep"), redirect_stdout(output):
            result = sync.main()
        return result, output.getvalue()

    def test_source_change_updates_accounting_and_repeat_is_unchanged(self):
        self.assertEqual(self.run_sync()[0], 0)
        self.source[0]["billing_email"] = "new@acme.example"
        result, output = self.run_sync()
        self.assertEqual(result, 0)
        self.assertIn("Updated: crm-001", output)
        record = self.accounting.get("/customers").json()[0]
        self.assertEqual(record["invoice_email"], "new@acme.example")
        self.assertEqual(record["version"], 2)
        self.assertIn("Already exists", self.run_sync()[1])
        self.assertEqual(self.accounting.get("/customers").json()[0]["version"], 2)

    def test_independent_accounting_change_is_preserved(self):
        self.run_sync()
        record = self.accounting.get("/customers").json()[0]
        response = self.accounting.patch(f"/customers/{record['id']}", json={
            "display_name": "Acme", "invoice_email": "manual@acme.example", "expected_version": 1,
        })
        self.assertEqual(response.status_code, 200)
        self.source[0]["billing_email"] = "crm-change@acme.example"
        result, output = self.run_sync()
        self.assertEqual(result, 1)
        self.assertIn("Conflict: crm-001", output)
        self.assertEqual(self.accounting.get("/customers").json()[0]["invoice_email"], "manual@acme.example")

    def test_lost_update_response_is_retried_without_second_version_increment(self):
        self.run_sync()
        self.source[0]["billing_email"] = "new@acme.example"
        self.lose_update_response = True
        result, output = self.run_sync()
        self.assertEqual(result, 0)
        self.assertIn("Retry: crm-001", output)
        record = self.accounting.get("/customers").json()[0]
        self.assertEqual(record["version"], 2)
        self.assertEqual(record["invoice_email"], "new@acme.example")

    def test_stale_version_cannot_overwrite_a_newer_different_value(self):
        self.run_sync()
        record = self.accounting.get("/customers").json()[0]
        url = f"/customers/{record['id']}"
        self.accounting.patch(url, json={
            "display_name": "Acme", "invoice_email": "first@acme.example", "expected_version": 1,
        })
        stale = self.accounting.patch(url, json={
            "display_name": "Acme", "invoice_email": "second@acme.example", "expected_version": 1,
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.accounting.get(url).json()["invoice_email"], "first@acme.example")
