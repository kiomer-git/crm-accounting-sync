import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class CustomerCreationTests(unittest.TestCase):
    def setUp(self):
        storage = patch("main.customers", [])
        storage.start()
        self.addCleanup(storage.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.customer = {
            "company_name": "Bluebird Events",
            "billing_email": "billing@bluebird.example",
        }

    def test_created_customer_can_be_listed_and_edited(self):
        response = self.client.post("/customers", json=self.customer)
        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertTrue(created["id"].startswith("crm-"))
        self.assertEqual(self.client.get("/customers").json(), [created])
        updated = self.client.patch(f"/customers/{created['id']}", json={
            "billing_email": "new@bluebird.example",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["billing_email"], "new@bluebird.example")

    def test_missing_or_empty_fields_do_not_create_customers(self):
        for payload in ({}, {**self.customer, "company_name": ""}, {**self.customer, "billing_email": ""}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/customers", json=payload).status_code, 422)
        self.assertEqual(self.client.get("/customers").json(), [])

    def test_separate_creates_receive_distinct_ids(self):
        first = self.client.post("/customers", json=self.customer).json()
        second = self.client.post("/customers", json=self.customer).json()
        self.assertNotEqual(first["id"], second["id"])
