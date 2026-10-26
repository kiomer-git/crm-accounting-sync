from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

import accounting


class CustomerCreationTests(unittest.TestCase):
    def setUp(self):
        accounting.remaining_failures = 0
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_db_path = accounting.DB_PATH
        accounting.DB_PATH = Path(self.temp_directory.name) / "test.db"
        self.client = TestClient(accounting.app)
        self.customer = {
            "crm_id": "crm-001",
            "display_name": "Acme Rentals",
            "invoice_email": "billing@acme.example",
        }

    def tearDown(self):
        accounting.remaining_failures = 0
        self.client.close()
        accounting.DB_PATH = self.original_db_path
        self.temp_directory.cleanup()

    def test_repeating_create_returns_same_customer(self):
        first = self.client.post("/customers", json=self.customer)
        second = self.client.post("/customers", json=self.customer)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self.client.get("/customers").json()), 1)

    def test_changed_details_are_rejected_without_overwriting(self):
        self.client.post("/customers", json=self.customer)
        changed = {**self.customer, "invoice_email": "new@acme.example"}
        response = self.client.post("/customers", json=changed)
        self.assertEqual(response.status_code, 409)
        saved = self.client.get("/customers").json()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["invoice_email"], self.customer["invoice_email"])

    def test_same_company_name_with_different_crm_ids_stays_separate(self):
        self.client.post("/customers", json=self.customer)
        other = {**self.customer, "crm_id": "crm-002"}
        response = self.client.post("/customers", json=other)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.client.get("/customers").json()), 2)

    def test_customer_survives_a_new_client_and_database_connection(self):
        created = self.client.post("/customers", json=self.customer).json()
        with TestClient(accounting.app) as another_client:
            self.assertEqual(another_client.get("/customers").json(), [created])
            repeated = another_client.post("/customers", json=self.customer)
            self.assertEqual(repeated.status_code, 200)
            self.assertEqual(repeated.json()["id"], created["id"])

    def test_failure_control_fails_two_writes_then_recovers(self):
        configured = self.client.post("/admin/failures", json={"next_writes": 2})
        self.assertEqual(configured.status_code, 200)
        for _ in range(2):
            self.assertEqual(self.client.post("/customers", json=self.customer).status_code, 503)
        self.assertEqual(self.client.get("/customers").json(), [])
        self.assertEqual(self.client.post("/customers", json=self.customer).status_code, 201)
